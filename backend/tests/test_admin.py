"""Admin/editorial API, policy changes, corrections export and generic bank import."""
import json
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Question


def test_overview_and_question_search(admin, synced):
    ov = admin.get("/api/admin/overview").json()
    assert ov["questions"]["total"] >= len(synced["ids"]) and ov["questions"]["served"] > 0
    cid = synced["ids"]["badformula"]
    r = admin.get(f"/api/admin/questions?q={cid}").json()
    assert r["total"] == 1 and r["items"][0]["state"] == "NEEDS_FORMULA_REVIEW"
    r = admin.get("/api/admin/questions?state=NEEDS_ANSWER_LINKING").json()
    assert r["total"] >= 2
    r = admin.get("/api/admin/questions?reason=group_context_missing").json()
    assert r["total"] == 2
    d = admin.get(f"/api/admin/questions/{r['items'][0]['id']}").json()
    assert d["provenance"]["document_id"] and d["current_version"]["content"]["stem"] and d["versions"]


def test_override_enable_disable_affects_serving(admin, student, synced):
    db = SessionLocal()
    q = db.scalar(select(Question).where(Question.external_id == synced["ids"]["v1"]))
    qid = q.id
    db.close()
    assert admin.post(f"/api/admin/questions/{qid}/override", {"action": "disable", "note": "sai chính tả"}).json()[
        "served"] is False
    before = admin.get("/api/admin/questions?served=false&q=" + synced["ids"]["v1"]).json()
    assert before["total"] == 1
    assert admin.post(f"/api/admin/questions/{qid}/override", {"action": "clear"}).json()["served"] is True


def test_reports_and_corrections_export(admin, student, synced):
    db = SessionLocal()
    qid = db.scalar(select(Question.id).where(Question.external_id == synced["ids"]["m5"]))
    db.close()
    sid = student.post("/api/sessions", {"subjects": ["math"], "count": 40}).json()["session"]["id"]
    student.post("/api/reports", {"question_ref": qid, "category": "typo", "message": "lỗi chính tả", "session_id": sid})
    reps = admin.get("/api/admin/reports?status=open").json()["items"]
    rep = [r for r in reps if r["question_id"] == qid][0]
    assert rep["external_id"] == synced["ids"]["m5"] and rep["question_version_id"]
    assert admin.patch(f"/api/admin/reports/{rep['id']}", {"status": "triaged"}).json()["status"] == "triaged"
    c = admin.post(f"/api/admin/questions/{qid}/corrections", {"field": "stem", "new_value": "Giá trị đúng là",
                                                               "evidence": "đề gốc trang 3", "report_id": rep["id"]})
    assert c.status_code == 200
    assert admin.post(f"/api/admin/questions/{qid}/corrections", {"field": "bogus", "new_value": "x"}).status_code == 422
    out = admin.post("/api/admin/corrections/export").text.strip().splitlines()
    row = json.loads(out[-1])
    assert row["question_id"] == synced["ids"]["m5"] and row["field"] == "stem" and row["evidence"]
    assert admin.get("/api/admin/corrections?status=exported").json()["total"] >= 1


def test_policy_change_recomputes_serving(admin, synced):
    cur = admin.get("/api/admin/settings/serving_policy").json()["value"]
    r = admin.put("/api/admin/settings/serving_policy",
                  {"value": dict(cur, allowed_states=["READY_TO_SERVE", "NEEDS_VISUAL_REVIEW"])})
    assert r.status_code == 200 and r.json()["recomputed"]["changed"] >= 1
    lowres = admin.get("/api/admin/questions?q=" + synced["ids"]["lowres"]).json()["items"][0]
    assert lowres["served"] is True
    admin.put("/api/admin/settings/serving_policy", {"value": cur})
    lowres = admin.get("/api/admin/questions?q=" + synced["ids"]["lowres"]).json()["items"][0]
    assert lowres["served"] is False
    assert admin.put("/api/admin/settings/serving_policy",
                     {"value": dict(cur, allowed_states=["NOPE"])}).status_code == 422


def test_blueprint_crud_and_validation(admin, synced):
    bad = admin.post("/api/admin/blueprints/validate", {"config": {"sections": [], "timing": "global"}}).json()
    assert bad["valid"] is False
    too_many = admin.post("/api/admin/blueprints/validate", {"config": {
        "sections": [{"key": "a", "title": "A", "pools": [{"subjects": ["english"], "count": 400}]}],
        "timing": "none"}}).json()
    assert too_many["valid"] and too_many["ok"] is False and too_many["pools"][0]["available"] < 400
    code = f"bp_{uuid.uuid4().hex[:6]}"
    cfg = {"sections": [{"key": "a", "title": "A", "pools": [{"subjects": ["math"], "count": 5}]}], "timing": "global",
           "duration_minutes": 15}
    r = admin.post("/api/admin/blueprints", {"code": code, "name": "Đề mới", "config": cfg, "price_vnd": 20000,
                                            "is_published": True})
    assert r.status_code == 200 and r.json()["availability"]["ok"]
    bid, v = r.json()["id"], r.json()["version"]
    cfg2 = dict(cfg, duration_minutes=20)
    r = admin.put(f"/api/admin/blueprints/{bid}", {"code": code, "name": "Đề mới", "config": cfg2, "price_vnd": 20000,
                                                   "is_published": True})
    assert r.json()["version"] == v + 1
    fixed = {"sections": [{"key": "f", "title": "Cố định", "items": [{"external_id": synced["ids"]["m1"]},
                                                                      {"external_id": synced["ids"]["m2"]}]}],
             "timing": "none"}
    r = admin.post("/api/admin/blueprints", {"code": code + "_fixed", "name": "Đề cố định", "kind": "fixed",
                                            "config": fixed, "is_published": True})
    assert r.status_code == 200 and r.json()["availability"]["ok"]


def test_users_admin(admin, student):
    lst = admin.get(f"/api/admin/users?q={student.user['email']}").json()
    assert lst["total"] == 1
    uid = lst["items"][0]["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"is_active": False}).status_code == 200
    assert student.get("/api/auth/me").json()["user"] is None  # sessions revoked
    admin.patch(f"/api/admin/users/{uid}", {"is_active": True})
    assert admin.patch(f"/api/admin/users/{admin.user['id']}", {"role": "student"}).status_code == 409


def test_generic_bank_import(admin, student, tmp_path):
    rows = [
        {"external_id": "demo-1", "type": "single_choice", "subject": "math", "status": "READY_TO_SERVE",
         "stem_md": "Tính {{tex:\\frac{1}{2}+\\frac{1}{2}}}", "options": [{"label": "A", "md": "1"}, {"label": "B", "md": "2"}],
         "answer": {"kind": "labels", "labels": ["A"]}},
        {"external_id": "demo-2", "type": "numeric_response", "subject": "math", "status": "READY_TO_SERVE",
         "stem_md": "2 + 2 = ?", "answer": {"kind": "numeric", "value": 4}},
        {"external_id": "demo-3", "type": "single_choice", "subject": "nope", "stem_md": "x"},
    ]
    data = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode()
    r = admin.c.post("/api/admin/banks/demo_bank/import", files={"file": ("bank.jsonl", data)},
                     data={"name": "Ngân hàng demo"}, headers={"X-CSRF-Token": admin.csrf})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["created"] == 2 and len(st["errors"]) == 1
    again = admin.c.post("/api/admin/banks/demo_bank/import", files={"file": ("bank.jsonl", data)},
                         headers={"X-CSRF-Token": admin.csrf}).json()
    assert again["created"] == 0 and again["versions_created"] == 0  # idempotent
    banks = {b["code"]: b for b in admin.get("/api/admin/banks").json()["items"]}
    assert banks["demo_bank"]["served"] == 2
    cfg = {"sections": [{"key": "d", "title": "Demo", "pools": [{"banks": ["demo_bank"], "count": 2}]}],
           "timing": "none"}
    bid = admin.post("/api/admin/blueprints", {"code": f"demo_{uuid.uuid4().hex[:5]}", "name": "Demo", "config": cfg,
                                              "is_published": True}).json()["id"]
    sid = student.post("/api/sessions", {"blueprint_id": bid}).json()["session"]["id"]
    items = student.get(f"/api/sessions/{sid}").json()["items"]
    assert len(items) == 2
    assert admin.c.post("/api/admin/banks/hsa/import", files={"file": ("x.jsonl", data)},
                        headers={"X-CSRF-Token": admin.csrf}).status_code == 422
