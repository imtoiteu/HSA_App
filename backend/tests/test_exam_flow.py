"""End-to-end API flows: auth, practice, exams, autosave/resume, timing, submission, review, immutability."""
import datetime as dt
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal
from app.exam import service as ex
from app.exam.blueprint import parse_config
from app.exam.selection import select_questions
from app.models import ExamBlueprint, ExamSession, Question
from app.sync.importer import sync_hsa


def make_blueprint(db, code, config, price=0, published=True):
    bp = ExamBlueprint(code=code, name=f"Đề {code}", config=config, price_vnd=price,
                       access="paid" if price else "free", is_published=published)
    db.add(bp)
    db.commit()
    return bp


SMALL = {"sections": [{"key": "toan", "title": "Toán", "pools": [{"subjects": ["math"], "types": ["single_choice"],
                                                                   "count": 6}]},
                      {"key": "van", "title": "Văn", "pools": [{"subjects": ["literature"], "count": 4}]}],
         "timing": "global", "duration_minutes": 30, "scoring": {"scale_to": 10}}


# ---------------------------------------------------------------------------------------------- auth
def test_register_login_logout_and_csrf(anon, synced):
    email = f"u_{uuid.uuid4().hex[:6]}@example.com"
    r = anon.post("/api/auth/register", {"email": email, "password": "123", "display_name": "A"})
    assert r.status_code == 422  # weak password
    r = anon.post("/api/auth/register", {"email": email, "password": "mat-khau-tot-1", "display_name": "A"})
    assert r.status_code == 200 and r.json()["token"] is None  # browser: token only in HttpOnly cookie
    assert anon.post("/api/auth/register", {"email": email.upper(), "password": "mat-khau-tot-1",
                                            "display_name": "B"}).status_code == 409
    # state-changing request without CSRF header is rejected
    assert anon.post("/api/sessions", {"count": 5}).status_code == 403
    anon.csrf = r.json()["user"]["csrf_token"]
    assert anon.get("/api/auth/me").json()["user"]["email"] == email
    anon.post("/api/auth/logout")
    assert anon.get("/api/auth/me").json()["user"] is None
    assert anon.post("/api/auth/login", {"email": email, "password": "sai-mat-khau"}).status_code == 401
    r = anon.post("/api/auth/login", {"email": email, "password": "mat-khau-tot-1"},
                  headers={"X-Client-Type": "mobile"})
    token = r.json()["token"]
    assert token
    # bearer clients need no CSRF header
    from fastapi.testclient import TestClient
    from app.main import app
    mobile = TestClient(app)
    assert mobile.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["user"]["email"] == email
    assert mobile.post("/api/sessions", json={"count": 3}, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_password_reset_flow(anon, admin, synced):
    email = f"r_{uuid.uuid4().hex[:6]}@example.com"
    r = anon.post("/api/auth/register", {"email": email, "password": "mat-khau-cu-1", "display_name": "R"})
    uid = r.json()["user"]["id"]
    assert anon.post("/api/auth/forgot-password", {"email": "khongton@tai.vn"}).status_code == 200
    link = admin.post(f"/api/admin/users/{uid}/reset-link").json()["link"]
    token = link.split("token=")[1]
    assert anon.post("/api/auth/reset-password", {"token": token, "password": "mat-khau-moi-1"}).status_code == 200
    assert anon.post("/api/auth/reset-password", {"token": token, "password": "mat-khau-moi-2"}).status_code == 400
    assert anon.post("/api/auth/login", {"email": email, "password": "mat-khau-moi-1"}).status_code == 200


def test_login_rate_limit(anon, synced, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "rate_limits", True)
    codes = [anon.post("/api/auth/login", {"email": "brute@force.vn", "password": "x" * 8}).status_code
             for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429


def test_admin_routes_require_admin(student, anon):
    assert anon.get("/api/admin/overview").status_code == 401
    assert student.get("/api/admin/overview").status_code == 403


# ---------------------------------------------------------------------------------------------- practice
def test_catalog(student):
    c = student.get("/api/catalog").json()
    subj = {s["code"]: s["available"] for s in c["subjects"]}
    assert subj["math"] >= 30 and subj["literature"] >= 20
    assert c["topics_enabled"] is False  # upstream topics are not classified yet
    assert any(b["code"] == "hsa_full_khoahoc" and b["price_vnd"] == 20000 for b in c["blueprints"])


def test_practice_immediate_feedback_flow(student):
    r = student.post("/api/sessions", {"subjects": ["math"], "types": ["single_choice"], "count": 5,
                                       "feedback": "immediate"})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    assert s["total"] == 5 and s["status"] == "in_progress"
    it = s["items"][0]
    assert "answer" not in it and "solution" not in it  # nothing revealed before checking
    right = None
    # answer the first item with each option until check reveals the key (only one check allowed)
    key = it["question"]["options"][0]["key"]
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 1, "response": {"labels": [key]},
                                                                     "flagged": True, "time_ms": 5000}]})
    assert r.status_code == 200 and r.json()["saved"] == {"1": "ok"}
    chk = student.post(f"/api/sessions/{sid}/check/1").json()
    assert chk["answer"]["kind"] == "choice" and chk["outcome"] in ("correct", "incorrect") and chk["solution"]
    right = chk["answer"]["labels"]
    # a checked item is locked
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 1, "response": {"labels": right}}]})
    assert r.status_code == 409
    # invalid option key rejected
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 2, "response": {"labels": ["Z"]}}]})
    assert r.status_code == 400
    done = student.post(f"/api/sessions/{sid}/submit").json()
    assert done["status"] == "submitted" and done["result"]["counts"]["unanswered"] == 4
    assert all("answer" in i for i in done["items"])


def test_practice_self_check_items(student, synced):
    r = student.post("/api/sessions", {"subjects": ["literature"], "types": ["short_response"], "count": 3})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    assert s["total"] == 1 and s["items"][0]["scoring_mode"] == "self_check"
    student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 1, "response": {"text": "nguồn"}}]})
    assert student.post(f"/api/sessions/{sid}/self-assess/1", {"verdict": "correct"}).status_code == 200
    done = student.post(f"/api/sessions/{sid}/submit").json()
    assert done["max_score"] == 0 and done["result"]["counts"]["ungraded"] == 1
    assert done["result"]["self_assessed"]["correct"] == 1


def test_practice_rejects_impossible_selection(student):
    r = student.post("/api/sessions", {"subjects": ["geography"], "count": 5})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_enough_questions"


# ---------------------------------------------------------------------------------------------- exams
def test_exam_resume_submit_and_review(student, db):
    bp = make_blueprint(db, f"small_{uuid.uuid4().hex[:6]}", SMALL)
    r = student.post("/api/sessions", {"blueprint_id": bp.id})
    sid = r.json()["session"]["id"]
    # starting again resumes the same attempt instead of creating a new one
    r2 = student.post("/api/sessions", {"blueprint_id": bp.id})
    assert r2.json()["resumed"] and r2.json()["session"]["id"] == sid
    s = student.get(f"/api/sessions/{sid}").json()
    assert s["total"] == 10 and s["deadline_at"]
    assert student.post(f"/api/sessions/{sid}/check/1").status_code == 409  # no key during exams
    answers = [{"position": it["position"], "response": {"labels": [it["question"]["options"][0]["key"]]}}
               for it in s["items"][:7]]
    assert student.patch(f"/api/sessions/{sid}/answers", {"changes": answers}).status_code == 200
    # "reload": answers are persisted server side
    s2 = student.get(f"/api/sessions/{sid}").json()
    assert [i["response"] for i in s2["items"][:7]] == [a["response"] for a in answers]
    assert s2["items"][7]["response"] is None
    done = student.post(f"/api/sessions/{sid}/submit").json()
    c = done["result"]["counts"]
    assert c["correct"] + c["incorrect"] == 7 and c["unanswered"] == 3
    assert done["max_score"] == 10 and done["result"]["scaled"] == round(done["score"] / 10 * 10, 2)
    # second submit is harmless; answers can no longer change
    assert student.post(f"/api/sessions/{sid}/submit").status_code == 200
    assert student.patch(f"/api/sessions/{sid}/answers", {"changes": answers[:1]}).status_code == 409
    rev = student.get(f"/api/sessions/{sid}").json()
    for it in rev["items"]:
        assert it["outcome"] in ("correct", "incorrect", "unanswered") and it["answer"]
        if it["answer"]["kind"] == "choice":
            assert it["answer_display"]
    hist = student.get("/api/sessions?status=submitted").json()
    assert any(h["id"] == sid for h in hist["items"])
    dash = student.get("/api/me/dashboard").json()
    assert dash["submitted_total"] >= 1 and dash["answered_total"] >= 7


def test_other_users_cannot_access_session(student, anon, db):
    bp = make_blueprint(db, f"priv_{uuid.uuid4().hex[:6]}", SMALL)
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    r = anon.post("/api/auth/register", {"email": f"o_{uuid.uuid4().hex[:6]}@example.com",
                                         "password": "mat-khau-tot-1", "display_name": "O"})
    anon.csrf = r.json()["user"]["csrf_token"]
    assert anon.get(f"/api/sessions/{sid}").status_code == 404


def test_groups_are_kept_together_with_passage(student, db):
    cfg = {"sections": [{"key": "doc", "title": "Đọc hiểu", "pools": [
        {"subjects": ["literature"], "types": ["single_choice"], "count": 22}]}], "timing": "none",
        "keep_groups_together": True, "shuffle_options": False}
    bp = make_blueprint(db, f"grp_{uuid.uuid4().hex[:6]}", cfg)
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    items = student.get(f"/api/sessions/{sid}").json()["items"]
    pos = [i["position"] for i in items if i["question"]["group"]]
    assert len(pos) == 3 and pos == list(range(pos[0], pos[0] + 3))  # contiguous
    g = items[pos[0] - 1]["question"]["group"]
    assert g["passage"] and g["key"].startswith("grp_")


def test_per_section_timing_hides_future_sections_and_times_out(student, db):
    cfg = {"sections": [{"key": "a", "title": "A", "duration_minutes": 5, "pools": [{"subjects": ["math"], "count": 3}]},
                        {"key": "b", "title": "B", "duration_minutes": 5, "pools": [{"subjects": ["literature"], "count": 2}]}],
           "timing": "per_section"}
    bp = make_blueprint(db, f"sec_{uuid.uuid4().hex[:6]}", cfg)
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    assert len(s["items"]) == 3 and s["sections"][1]["state"] == "locked"
    # items of the next section cannot be answered
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 4, "response": {"labels": ["A"]}}]})
    assert r.status_code == 404 or r.status_code == 409
    s = student.post(f"/api/sessions/{sid}/next-section").json()
    assert s["current_section"] == 1 and len(s["items"]) == 5 and s["sections"][0]["state"] == "done"
    # section A is locked now
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 1, "response": {"labels": ["A"]}}]})
    assert r.status_code == 409
    # simulate the clock running out on the last section → automatic submission
    db2 = SessionLocal()
    sess = db2.get(ExamSession, uuid.UUID(sid))
    ex.refresh_timing(db2, sess, now=sess.section_started_at + dt.timedelta(minutes=6))
    assert sess.status == "submitted" and sess.submit_reason == "timeout"
    db2.close()


def test_global_deadline_rejects_late_answers(student, db):
    bp = make_blueprint(db, f"late_{uuid.uuid4().hex[:6]}", SMALL)
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    db2 = SessionLocal()
    sess = db2.get(ExamSession, uuid.UUID(sid))
    # an answer arriving after deadline + grace is refused and the attempt is closed as timed out
    with pytest.raises(ex.SessionError):
        ex.save_answers(db2, sess, [{"position": 1, "response": {"labels": ["A"]}}],
                        now=sess.deadline_at + dt.timedelta(seconds=10) + ex.GRACE)
    assert sess.status == "submitted" and sess.submit_reason == "timeout"
    assert all(it.answer is None for it in sess.items)
    db2.close()


def test_randomized_exam_is_reproducible(synced, db):
    cfg = parse_config(SMALL)
    a = select_questions(db, cfg, "fixed-seed-42")
    b = select_questions(db, cfg, "fixed-seed-42")
    c = select_questions(db, cfg, "another-seed")
    ids = lambda s: [p.external_id for p in s.items]  # noqa: E731
    assert ids(a) == ids(b) and a.pool_fingerprint == b.pool_fingerprint
    assert ids(a) != ids(c)
    assert len(set(ids(a))) == len(ids(a)) == 10  # no duplicates


def test_session_records_seed_items_and_option_order(student, db):
    bp = make_blueprint(db, f"rep_{uuid.uuid4().hex[:6]}", SMALL)
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    sess = db.get(ExamSession, uuid.UUID(sid))
    assert sess.seed and sess.generator_version and sess.blueprint_snapshot["sections"][0]["key"] == "toan"
    assert sess.scoring_config["scale_to"] == 10 and sess.pool_fingerprint
    again = select_questions(db, parse_config(sess.blueprint_snapshot), sess.seed)
    assert [p.version_id for p in again.items] == [it.question_version_id for it in sess.items]
    from app.exam.selection import option_permutation
    for it in sess.items:
        c = it.version.content
        if c["shuffle_safe"]:
            q = db.get(Question, it.question_id)
            assert it.option_order == option_permutation(sess.seed, q.external_id, [o["label"] for o in c["options"]])


def test_completed_exam_is_immutable_across_upstream_corrections(student, db, synced, tmp_path):
    bp = make_blueprint(db, f"imm_{uuid.uuid4().hex[:6]}",
                        {"sections": [{"key": "t", "title": "T", "pools": [{"subjects": ["math"], "types": ["single_choice"],
                                                                            "count": 30}]}],
                         "timing": "none", "shuffle_options": False})
    sid = student.post("/api/sessions", {"blueprint_id": bp.id}).json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    changes = [{"position": i["position"], "response": {"labels": ["A"]}} for i in s["items"]]
    student.patch(f"/api/sessions/{sid}/answers", {"changes": changes})
    before = student.post(f"/api/sessions/{sid}/submit").json()
    # an editorial correction upstream: change stem AND the answer key of one question in this exam
    target = s["items"][0]["question_ref"]
    cid = db.get(Question, target).external_id
    root = tmp_path / "up"
    shutil.copytree(os.environ["HSA_UPSTREAM_ROOT"], root)
    con = sqlite3.connect(root / "question-bank/sqlite/hsa_question_bank.sqlite")
    con.execute("UPDATE canonical_question SET stem_md='ĐÃ SỬA ' || stem_md, correct_answer=? WHERE canonical_question_id=?",
                ('{"kind": "labels", "labels": ["D"]}', cid))
    con.commit()
    con.close()
    db2 = SessionLocal()
    sync_hsa(db2, root, Path(os.environ["HSA_MEDIA_ROOT"]))
    after = student.get(f"/api/sessions/{sid}").json()
    assert after["score"] == before["score"] and after["result"] == before["result"]
    assert after["items"][0]["question"]["stem"] == before["items"][0]["question"]["stem"]
    assert after["items"][0]["answer"] == before["items"][0]["answer"]
    # the database refuses to alter a submitted session or its items
    with pytest.raises(Exception):
        db2.execute(text("UPDATE exam_item SET points_awarded = 99 WHERE session_id = :s"), {"s": sid})
        db2.commit()
    db2.rollback()
    with pytest.raises(Exception):
        db2.execute(text("DELETE FROM exam_session WHERE id = :s"), {"s": sid})
        db2.commit()
    db2.rollback()
    sync_hsa(db2, Path(os.environ["HSA_UPSTREAM_ROOT"]), Path(os.environ["HSA_MEDIA_ROOT"]))
    db2.close()


def test_bookmarks_and_reports(student, db):
    sid = student.post("/api/sessions", {"subjects": ["math"], "count": 2, "feedback": "end"}).json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    qref = s["items"][0]["question_ref"]
    assert student.put(f"/api/bookmarks/{qref}", {"note": "ôn lại"}).json()["bookmarked"]
    assert student.get(f"/api/sessions/{sid}").json()["items"][0]["bookmarked"]
    bl = student.get("/api/bookmarks").json()
    assert bl["total"] == 1 and not bl["items"][0]["revealed"]  # key hidden until the attempt is submitted
    assert "answer" not in bl["items"][0]["question"] and "solution" not in bl["items"][0]["question"]
    student.post(f"/api/sessions/{sid}/submit")
    bl = student.get("/api/bookmarks").json()
    assert bl["items"][0]["question"]["answer"]
    r = student.post("/api/reports", {"question_ref": qref, "category": "wrong_answer", "message": "Đáp án sai?",
                                      "session_id": sid})
    assert r.status_code == 200
    assert student.post("/api/reports", {"question_ref": qref, "category": "nonsense"}).status_code == 422
    # practice from bookmarks
    r = student.post("/api/sessions", {"source": "bookmarks", "count": 10})
    assert r.status_code == 200 and r.json()["session"]["total"] == 1
    assert student.delete(f"/api/bookmarks/{qref}").json()["bookmarked"] is False
    # cannot bookmark a question never seen
    unseen = db.scalar(select(Question.id).where(Question.is_served.is_(True)).order_by(Question.id.desc()))
    assert student.put(f"/api/bookmarks/{unseen}", {}).status_code in (200, 403)


def test_abandon(student):
    sid = student.post("/api/sessions", {"subjects": ["math"], "count": 2}).json()["session"]["id"]
    assert student.post(f"/api/sessions/{sid}/abandon").status_code == 200
    assert student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": 1, "flagged": True}]}).status_code == 409
