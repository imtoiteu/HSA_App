"""Review Studio: roles, queues, drafts, QA dimensions bound to content hashes, safe publishing, history,
restore, sync preservation, validators, sources and the on-demand AI switch."""
import io
import os
import uuid
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Question, QuestionReview, QuestionVersion, ReviewEvent, User
from app.review import content as C
from app.review import validators as V

from .conftest import Api, _client


def staff_api(role: str) -> Api:
    from app.auth import hash_password
    db = SessionLocal()
    email = f"{role}_{uuid.uuid4().hex[:8]}@example.com"
    db.add(User(email=email, display_name=role.title(), password_hash=hash_password("staff-pass-123"), role=role))
    db.commit()
    db.close()
    api = Api(_client())
    r = api.post("/api/auth/login", {"email": email, "password": "staff-pass-123"})
    assert r.status_code == 200, r.text
    api.csrf = r.json()["user"]["csrf_token"]
    api.user = r.json()["user"]
    return api


@pytest.fixture(scope="module")
def refreshed(synced):
    from app.review.bulk import refresh_all
    db = SessionLocal()
    r = refresh_all(db, Path(os.environ["HSA_MEDIA_ROOT"]), Path(os.environ["HSA_UPSTREAM_ROOT"]))
    db.close()
    return r


def qid_of(cid):
    db = SessionLocal()
    i = db.scalar(select(Question.id).where(Question.external_id == cid))
    db.close()
    return i


def plain(text):
    return [{"t": "p", "c": [{"t": "s", "v": text}]}]


def test_roles(student, admin, refreshed, synced):
    assert student.get("/api/review/meta").status_code == 403
    rev = staff_api("reviewer")
    m = rev.get("/api/review/meta").json()
    assert m["me"]["role"] == "reviewer" and "confirm" in m["me"]["perms"] and "edit" not in m["me"]["perms"]
    qid = qid_of(synced["ids"]["m1"])
    assert rev.put(f"/api/review/questions/{qid}", {"stem": plain("x")}).status_code == 403
    assert rev.post(f"/api/review/questions/{qid}/approve", {}).status_code == 403
    # staff roles are not admins
    assert rev.get("/api/admin/overview").status_code == 403
    # admin can grant a staff role
    uid = rev.user["id"]
    assert admin.patch(f"/api/admin/users/{uid}", {"role": "approver"}).status_code == 200


def test_queues_and_incremental_refresh(admin, refreshed, synced):
    assert refreshed["candidates"] >= len(synced["ids"]) and refreshed["validated"] >= 1
    r = admin.get("/api/review/queue?queue=NEEDS_FORMULA_REVIEW&size=200").json()
    ext = {i["external_id"] for i in r["items"]}
    assert synced["ids"]["badformula"] in ext and synced["ids"]["m1"] not in ext
    assert all("NEEDS_FORMULA_REVIEW" in i["queues"] for i in r["items"])
    counts = admin.get("/api/review/queue/counts").json()["queues"]
    assert counts["READY"] > 0 and counts["NOT_READY"] > 0
    # nothing changed → a second run validates nothing
    from app.review.bulk import refresh_all
    db = SessionLocal()
    again = refresh_all(db, Path(os.environ["HSA_MEDIA_ROOT"]))
    db.close()
    assert again["candidates"] == 0
    # filters + sort + paging, neighbors
    page = admin.get("/api/review/queue?ready=no&sort=cq_id&size=3").json()
    ids = [i["id"] for i in page["items"]]
    nb = admin.get(f"/api/review/queue/neighbors?id={ids[1]}&ready=no&sort=cq_id").json()
    assert nb["prev"] == ids[0] and nb["next"] == ids[2] and nb["in_filter"]
    # keyset with the key seen earlier still finds the next item when the question left the filter
    import json as _j
    nb2 = admin.get(f"/api/review/queue/neighbors?id={ids[1]}&ready=yes&sort=cq_id&key={_j.dumps(nb['key'])}").json()
    assert nb2["in_filter"] is False and nb2["next"] not in (None, ids[1])
    for sort in ("priority", "oldest", "document", "subject", "updated"):
        page = admin.get(f"/api/review/queue?ready=no&sort={sort}&size=3").json()["items"]
        nb = admin.get(f"/api/review/queue/neighbors?id={page[1]['id']}&ready=no&sort={sort}").json()
        assert nb["next"] == page[2]["id"] and nb["prev"] == page[0]["id"], sort
    assert admin.get("/api/review/queue?q=" + synced["ids"]["m1"]).json()["total"] == 1
    assert admin.get("/api/review/queue?q=chính tả").json()["total"] >= 20


def test_draft_dims_publish_and_history(admin, student, refreshed, synced):
    cid = synced["ids"]["pdf_math"]
    qid = qid_of(cid)
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["ready"] is False and "NEEDS_FORMULA_REVIEW" in d["review"]["queues"]
    cur_before = d["current_version_id"]
    labels = [o["label"] for o in d["working"]["content"]["options"]]

    # confirm answer + subject, then edit the stem: only content/formula are invalidated
    for dim in ("answer", "subject", "source"):
        assert admin.post(f"/api/review/questions/{qid}/confirm", {"dim": dim, "state": "ok"}).status_code == 200
    stem = [{"t": "p", "c": [{"t": "s", "v": "Tính "}, {"t": "m", "tex": "\\sin x"}, {"t": "s", "v": " khi "},
                             {"t": "m", "tex": "x=0"}, {"t": "s", "v": " — có dấu tiếng Việt: ữ ặ ỡ."}]}]
    r = admin.put(f"/api/review/questions/{qid}", {"stem": stem, "note": "khôi phục công thức",
                                                   "tex_checks": {"\\sin x": {"ok": True}, "x=0": {"ok": True}}})
    assert r.status_code == 200 and r.json()["changed"] and "stem" in r.json()["changes"]
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["current_version_id"] == cur_before, "draft must not replace the served version"
    assert d["review"]["draft_version_id"] and d["working"]["origin"] == "review"
    assert d["working"]["content"]["stem"][0]["c"][4]["v"].endswith("ữ ặ ỡ.")
    assert d["dims"]["answer"]["state"] == "ok" and d["dims"]["subject"]["state"] == "ok"
    assert d["dims"]["content"]["state"] == "pending" and d["dims"]["formula"]["state"] == "pending"
    ev = [h for h in d["history"] if h["action"] == "edit"][0]
    assert ev["note"] == "khôi phục công thức" and any(c["field"] == "stem" for c in ev["changes"])

    # approving with open dimensions is refused
    r = admin.post(f"/api/review/questions/{qid}/approve", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "qa_incomplete"
    for dim in ("content", "formula", "visual"):
        admin.post(f"/api/review/questions/{qid}/confirm", {"dim": dim, "state": "ok"})
    # answer change re-opens ANSWER_OK only
    new_label = labels[1]
    assert admin.put(f"/api/review/questions/{qid}", {"answer": {"kind": "choice", "labels": [new_label]}}).json()[
        "changed"]
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["dims"]["answer"]["state"] == "stale" and d["dims"]["formula"]["state"] == "ok"
    assert any(c["field"] == "answer" for h in d["history"] if h["action"] == "edit" for c in h["changes"])
    assert admin.put(f"/api/review/questions/{qid}", {"answer": {"kind": "choice", "labels": ["Z"]}}).status_code == 422
    admin.post(f"/api/review/questions/{qid}/confirm", {"dim": "answer", "state": "ok"})

    # approve → published, READY via review, served
    r = admin.post(f"/api/review/questions/{qid}/approve", {"note": "đã đối chiếu nguồn"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["gate"] == "ready" and res["served"] is True, res
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["current_version_id"] != cur_before and d["review"]["draft_version_id"] is None
    assert d["ready"] and "READY" in d["review"]["queues"] and "NEEDS_FORMULA_REVIEW" not in d["review"]["queues"]
    db = SessionLocal()
    q = db.get(Question, qid)
    assert q.editorial_state == "NEEDS_FORMULA_REVIEW", "upstream state is kept; the review gate decides"
    assert q.answer_source_type == "EDITORIAL_TRANSCRIBED"
    v = db.get(QuestionVersion, q.current_version_id)
    assert v.origin == "review" and v.answer == {"kind": "choice", "labels": [new_label]}
    old = db.get(QuestionVersion, cur_before)
    assert old is not None  # history kept (immutable)
    db.close()

    # sync keeps the reviewed version and flags an upstream change
    from app.sync.importer import sync_hsa
    db = SessionLocal()
    sync_hsa(db, Path(os.environ["HSA_UPSTREAM_ROOT"]), Path(os.environ["HSA_MEDIA_ROOT"]), full=True, only=[cid])
    q = db.get(Question, qid)
    assert db.get(QuestionVersion, q.current_version_id).origin == "review" and q.review_gate == "ready"
    db.close()

    # restore the upstream version as a draft, then discard it
    r = admin.post(f"/api/review/questions/{qid}/restore", {"version_id": cur_before, "note": "so sánh"})
    assert r.status_code == 200 and r.json()["draft_version_id"] == cur_before
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["gate"] == "ready" and d["served"], "the published version keeps serving while a draft is open"
    assert admin.post(f"/api/review/questions/{qid}/status", {"action": "discard_draft"}).status_code == 200

    # reopen withdraws the approval (back to the upstream decision)
    assert admin.post(f"/api/review/questions/{qid}/status", {"action": "reopen"}).json()["gate"] is None
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["served"] is False


def test_fail_dimension_blocks_ready_question(admin, refreshed, synced):
    qid = qid_of(synced["ids"]["m2"])
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["served"] is True
    r = admin.post(f"/api/review/questions/{qid}/confirm", {"dim": "answer", "state": "fail"})
    assert r.status_code == 422  # a failure needs a note
    r = admin.post(f"/api/review/questions/{qid}/confirm", {"dim": "answer", "state": "fail",
                                                           "note": "đáp án nguồn sai"})
    assert r.status_code == 200
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["gate"] == "blocked" and d["served"] is False and "review:blocked" in d["policy_reasons"]
    admin.post(f"/api/review/questions/{qid}/confirm", {"dim": "answer", "state": None})
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["gate"] is None and d["served"] is True


def test_subject_change_invalidates_subject_only(admin, refreshed, synced):
    qid = qid_of(synced["ids"]["m3"])
    for dim in ("subject", "visual", "content"):
        admin.post(f"/api/review/questions/{qid}/confirm", {"dim": dim, "state": "ok"})
    r = admin.post(f"/api/review/questions/{qid}/subject", {"subject_code": "physics", "note": "thử"})
    assert r.status_code == 200 and r.json()["subject"] == "physics"
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["dims"]["subject"]["state"] == "stale" and d["dims"]["content"]["state"] == "ok"
    assert d["dims"]["visual"]["state"] in ("ok", "na")
    assert any(h["action"] == "subject" for h in d["history"])
    admin.post(f"/api/review/questions/{qid}/subject", {"subject_code": None})
    db = SessionLocal()
    assert db.get(Question, qid).subject_code == "math"
    db.close()


def test_findings_dismiss_and_assignments(admin, refreshed, synced):
    qid = qid_of(synced["ids"]["noanswer"])
    d = admin.get(f"/api/review/questions/{qid}").json()
    f = [x for x in d["findings"] if x["code"] == "answer_missing"][0]
    assert f["severity"] == "critical" and f["status"] == "open" and "VALIDATOR" in d["review"]["queues"]
    assert admin.post(f"/api/review/questions/{qid}/findings/{f['id']}", {"action": "dismiss"}).status_code == 422
    assert admin.post(f"/api/review/questions/{qid}/findings/{f['id']}",
                      {"action": "dismiss", "reason": "câu tự luận"}).json()["status"] == "dismissed"
    # assignment by filter
    rev = staff_api("reviewer")
    r = admin.post("/api/review/assign", {"reviewer_id": rev.user["id"], "filters": {"queue": "NEEDS_ANSWER_LINKING"},
                                          "limit": 2, "label": "Gắn đáp án · 2 câu"}).json()
    assert r["assigned"] == 2 and r["assignment_id"]
    mine = rev.get("/api/review/queue?assignee=me").json()
    assert mine["total"] == 2
    assert rev.get("/api/review/me").json()["assigned"] == 2
    assert admin.get("/api/review/assignments").json()["items"][0]["total"] == 2
    assert admin.post("/api/review/assign", {"reviewer_id": 999999, "ids": [qid]}).status_code in (422, 500)


def test_images_sources_ai(admin, refreshed, synced):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "PNG")
    r = admin.c.post("/api/review/assets", files={"file": ("a.png", buf.getvalue(), "image/png")},
                     headers={"X-CSRF-Token": admin.csrf})
    assert r.status_code == 200, r.text
    img = r.json()
    assert img["w"] == 40 and C.SRC_RE.match(img["src"])
    bad = admin.c.post("/api/review/assets", files={"file": ("a.png", b"not an image", "image/png")},
                       headers={"X-CSRF-Token": admin.csrf})
    assert bad.status_code == 422
    qid = qid_of(synced["ids"]["m4"])
    stem = plain("Quan sát hình:") + [{"t": "img", "src": img["src"], "w": 40, "h": 30, "alt": "hình đỏ"}]
    assert admin.put(f"/api/review/questions/{qid}", {"stem": stem}).json()["changed"]
    d = admin.get(f"/api/review/questions/{qid}").json()
    assert d["preview"]["stem"][1]["src"] == img["src"] and d["dims"]["visual"]["required"]
    assert any(c["field"] == "images" for h in d["history"] for c in h["changes"])
    bogus = plain("x") + [{"t": "img", "src": "ab/" + "0" * 64 + ".png"}]
    assert admin.put(f"/api/review/questions/{qid}", {"stem": bogus}).status_code == 422
    admin.post(f"/api/review/questions/{qid}/status", {"action": "discard_draft"})
    # the fixture bank has no source-original archives → explicit SOURCE_UNAVAILABLE after preparation
    src = admin.get(f"/api/review/questions/{qid}/source").json()
    assert "provenance" in src
    if src["documents"]:
        from app.review.sources import process_queue, SourceStore
        db = SessionLocal()
        process_queue(db, SourceStore(Path(os.environ["HSA_MEDIA_ROOT"]).parent / "sources",
                                      Path(os.environ["HSA_UPSTREAM_ROOT"])))
        db.close()
        st = admin.get(f"/api/review/sources/{src['documents'][0]['document_id']}").json()
        assert st["status"] == "unavailable"
    # AI is off unless configured
    r = admin.post(f"/api/review/questions/{qid}/ai", {"action": "solve"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ai_not_configured"
    assert admin.get("/api/review/meta").json()["ai"]["configured"] is False
    st = admin.get("/api/review/stats").json()
    assert st["total"] >= len(synced["ids"]) and st["ready"] >= st["upstream_ready"] - 5


def test_validators_unit():
    assert V.latex_problem("\\frac{1}{2") == "thiếu dấu }"
    assert V.latex_problem("\\left( x \\right.") is None
    assert V.latex_problem("\\left( x") is not None
    assert V.latex_problem("\\begin{matrix}1\\end{pmatrix}") is not None
    assert V.latex_problem("x^{2}+\\sqrt{3}") is None
    assert V.ocr_suspect("Tiáº¿ng Viá»‡t") and V.ocr_suspect("Câu hỏi bình thường về hàm số y = x^2.") is None

    class Q:
        bank_id, external_id, group_key, subject_code, subject_source = 1, "cq_abc", None, "math", "upstream_effective"
        editorial_state, editorial_states, policy_reasons = "READY_TO_SERVE", ["READY_TO_SERVE"], []
    content = {"type": "single_choice", "input": {"kind": "choice", "multi": False}, "group": None,
               "stem": plain("Câu hỏi đủ dài để kiểm tra trùng lặp nội dung giữa hai câu."),
               "options": [{"label": "A", "content": plain("1")}, {"label": "B", "content": plain("1")}],
               "solution": plain("Câu 12: Tiếp theo là câu khác hoàn toàn nhé A. 1 B. 2")}
    codes = {f.code for f in V.validate(Q, content, {"kind": "choice", "labels": ["C"]}, None)}
    assert {"duplicate_option", "answer_not_in_options", "solution_contains_next_question"} <= codes
    ex, near = V.duplicate_keys(content)
    assert ex and near


def test_hash_dimensions_unit():
    c = {"type": "single_choice", "stem": plain("a"), "options": [], "group": None, "solution": None}
    p1 = C.part_hashes(c, {"kind": "choice", "labels": ["A"]}, "math", {"document_id": "doc_1"})
    p2 = C.part_hashes(c, {"kind": "choice", "labels": ["B"]}, "math", {"document_id": "doc_1"})
    p3 = C.part_hashes(c, {"kind": "choice", "labels": ["A"]}, "physics", {"document_id": "doc_1"})
    assert C.dim_hash("answer", p1) != C.dim_hash("answer", p2)
    assert C.dim_hash("content", p1) == C.dim_hash("content", p2)
    assert C.dim_hash("visual", p1) == C.dim_hash("visual", p3) and C.dim_hash("subject", p1) != C.dim_hash("subject", p3)
    with pytest.raises(C.ContentError):
        C.sanitize_blocks([{"t": "script", "v": "x"}], lambda s: True)
    out = C.sanitize_blocks([{"t": "list", "ordered": True, "items": [plain("một"), plain("hai")]},
                             {"t": "p", "c": [{"t": "s", "v": "x", "m": ["b", "evil"]}]}], lambda s: True)
    assert out[0]["t"] == "list" and out[1]["c"][0]["m"] == ["b"]


def test_source_materialize_from_zip(tmp_path):
    """A document inside a zip archive is extracted read-only, verified by sha256 and rendered."""
    import hashlib
    import json
    import shutil
    import sqlite3
    from app.review.sources import SourceStore
    pdf = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    sha = hashlib.sha256(pdf).hexdigest()
    up = tmp_path / "up"
    (up / "source-original").mkdir(parents=True)
    with zipfile.ZipFile(up / "source-original" / "Bộ đề.zip", "w") as z:
        z.writestr("Đề 1/de.pdf", pdf)
    (up / "question-bank" / "sqlite").mkdir(parents=True)
    con = sqlite3.connect(up / "question-bank" / "sqlite" / "hsa_question_bank.sqlite")
    con.execute("CREATE TABLE document (document_id TEXT, blob_sha256 TEXT, file_kind TEXT, extraction_method TEXT, "
                "document_role TEXT, exam_name TEXT, year INT, representative_path TEXT, source_chains TEXT)")
    chains = [{"chain": [{"path": "Bộ đề.zip", "kind": "source_file"}, {"path": "Đề 1/de.pdf", "kind": "archive_member"}]}]
    con.execute("INSERT INTO document VALUES ('doc_0123456789abcdef', ?, 'pdf', 'pdf_text_layer', 'paper', 'x', 2025, "
                "'Bộ đề.zip ▸ Đề 1/de.pdf', ?)", (sha, json.dumps(chains)))
    con.commit()
    con.close()
    store = SourceStore(tmp_path / "cache", up)
    res = store.prepare("doc_0123456789abcdef")
    assert res["status"] == "ready" and store.pdf_path(sha).read_bytes() == pdf
    assert (up / "source-original" / "Bộ đề.zip").exists()  # source untouched
    if shutil.which("pdftoppm"):
        assert store.render(sha, 1, 110) is not None
    assert store.prepare("doc_ffffffffffffffff")["status"] == "unavailable"


def test_remediation_matching_unit():
    from app.review import remediate as M
    pages = ["Đề thi thử\nCâu 3. Từ nào viết đúng chính tả trong các từ sau đây?\nA. sáng sủa  B. xáng sủa\nC. sáng xủa D. xáng xủa\n",
             "Câu 4. Câu khác hoàn toàn"]
    c = {"stem": plain("Từ nào viết đúng chính tả trong các từ sau đây?"),
         "options": [{"label": l, "content": plain(t)} for l, t in zip("ABCD", ["sáng sủa", "xáng sủa", "sáng xủa", "xáng xủa"])]}
    v = M.check_source_text(c, pages)
    assert v.ok and v.page == 1
    bad = dict(c, options=c["options"][:3] + [{"label": "D", "content": plain("không có trong nguồn")}])
    assert not M.check_source_text(bad, pages).ok
    assert not M.check_source_text(dict(c, stem=plain("Một câu hỏi không hề có trong tài liệu gốc")), pages).ok
    nfd = {"stem": plain(__import__("unicodedata").normalize("NFD", "Tiếng Việt")), "options": []}
    fixed, changed = M.nfc_content(nfd)
    assert changed and fixed["stem"][0]["c"][0]["v"] == "Tiếng Việt"
    assert M.LABEL_RE.search("Vậy chọn đáp án C.").group(1) == "C"


def test_strip_watermarks_unit():
    from app.review import remediate as M
    wm = sorted(M.WATERMARKS)[0]
    c = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Câu hỏi "}, {"t": "s", "v": "("},
                                   {"t": "img", "src": f"{wm[:2]}/{wm}.png", "inline": True}, {"t": "s", "v": ")"}]},
                  {"t": "p", "c": [{"t": "s", "v": "Đọc đoạn trích."}]},
                  {"t": "img", "src": f"{wm[:2]}/{wm}.png"}],
         "options": [{"label": "A", "content": plain("x")}], "solution": None, "explanation": None, "group": None}
    out, changed = M.strip_watermarks(c)
    assert changed and C.images(out) == [] and out["stem"][1]["c"][0]["v"] == "Đọc đoạn trích."
    assert M.strip_watermarks(out)[1] is False


def test_trim_contaminated_option_unit():
    from app.review import remediate as M
    pages = ["Câu 5. Thứ tự bán kính tăng dần của các hạt là\nA. Na+ < Mg2+\nB. Mg < Na\nC. Al < Mg\nD. O2- > Na+ > Mg2+.\nCâu 6. ..."]
    c = {"stem": plain("Thứ tự bán kính tăng dần của các hạt là"),
         "options": [{"label": "A", "content": plain("Na+ < Mg2+")}, {"label": "B", "content": plain("Mg < Na")},
                     {"label": "C", "content": plain("Al < Mg")},
                     {"label": "D", "content": plain("O2- > Na+ > Mg2+.") + plain("Một dòng thừa không có trong nguồn")}]}
    assert not M.check_source_text(c, pages).ok
    fixed, labels = M.trim_options(c, pages)
    assert labels == ["D"] and len(fixed["options"][3]["content"]) == 1 and M.check_source_text(fixed, pages).ok
    ok = dict(c, options=c["options"][:3] + [{"label": "D", "content": plain("O2- > Na+") + plain("> Mg2+.")}])
    assert M.trim_options(ok, pages)[1] == []  # multi-line option present in the source: untouched


def test_remediation_group_passage_unit():
    from app.review import remediate as M
    pages = ["Đọc đoạn văn sau: Bão số 3 đổ bộ vào miền Bắc gây mưa lớn kéo dài nhiều ngày ở các tỉnh.\n"
             "Câu 7. Theo đoạn văn, bão số 3 gây ra hiện tượng gì ở các tỉnh?\nA. Mưa lớn\nB. Nắng nóng\nC. Hạn hán\nD. Rét đậm\n"]
    c = {"stem": plain("Theo đoạn văn, bão số 3 gây ra hiện tượng gì ở các tỉnh?"),
         "options": [{"label": l, "content": plain(t)} for l, t in zip("ABCD", ["Mưa lớn", "Nắng nóng", "Hạn hán", "Rét đậm"])],
         "group": {"key": "g", "header": [], "passage": plain("Bão số 3 đổ bộ vào miền Bắc gây mưa lớn kéo dài nhiều ngày ở các tỉnh.")}}
    assert M.check_source_text(c, pages).ok
    bad = dict(c, group={"key": "g", "header": [], "passage": plain("Lời giải Trong giai đoạn 1949-1959, Trung Quốc thi hành")})
    assert "solution text" in M.check_source_text(bad, pages).reason
    other = dict(c, group={"key": "g", "header": [], "passage": plain("Một đoạn văn hoàn toàn khác không có trong tài liệu gốc này")})
    assert not M.check_source_text(other, pages).ok


def test_structure_guard_and_revert(admin, refreshed, synced):
    from app.review import remediate as M
    from app.review import service as S
    four = {"options": [{"label": l, "content": plain(t)} for l, t in zip("ABCD", "wxyz")]}
    assert M.check_structure(four).ok
    glued = {"options": four["options"][:3] + [{"label": "D", "content": plain("Line d A. a-b-c B. b-a-c C. c-a-b D. c-b-a")}]}
    assert not M.check_structure(glued).ok
    assert not M.check_structure({"options": four["options"] + [{"label": "E", "content": plain("v")}]}).ok
    # revert: an approved+published review version goes back to the upstream version
    qid = qid_of(synced["ids"]["pdf_math"])
    db = SessionLocal()
    q = db.get(Question, qid)
    who = M.actor(db)
    S.save_edit(db, q, who, {"stem": plain("Tính sin 0")}, note="t")
    for dim in ("content", "answer", "formula", "visual", "subject", "source"):
        S.confirm(db, q, who, dim, "ok", "t")
    S.approve(db, q, who, "t")
    db.commit()
    assert q.review_gate == "ready" and q.current_version_id != q.upstream_version_id
    res = S.revert_publication(db, q, who, "sai cấu trúc")
    db.commit()
    assert q.current_version_id == q.upstream_version_id and q.review_gate is None and res["served"] is False
    db.close()


def test_check_complete_unit():
    from app.review import remediate as M
    assert not M.check_complete({"stem": plain("Cho sơ đồ biến hóa sau: Công thức đúng của (X), (Y) là:")}).ok
    assert M.check_complete({"stem": plain("Theo sơ đồ: C4H8O2 → A2 → A3, chất A2 là")}).ok
    assert not M.check_complete({"stem": plain("Quan sát hình vẽ và cho biết")}).ok
    assert M.check_complete({"stem": plain("Quan sát hình vẽ") + [{"t": "img", "src": "ab/" + "a" * 64 + ".png"}]}).ok
    assert M.check_complete({"stem": plain("Từ nào viết đúng chính tả?")}).ok
    assert not M.check_complete({"stem": plain("Cho phản ứng: Fe2O3 + 3CO      2Fe + 3CO2. Khi tăng áp suất")}).ok
    assert M.check_complete({"stem": plain("Cho phản ứng: Fe2O3 + 3CO ⇌ 2Fe + 3CO2. Khi tăng áp suất")}).ok


def test_strip_own_labels_unit():
    from app.review import remediate as M
    c = {"options": [{"label": "A", "content": plain("Mặt trời")}, {"label": "B", "content": plain("B. Đoàn người")}]}
    out, fixed = M.strip_own_labels(c)
    assert fixed == ["B"] and out["options"][1]["content"][0]["c"][0]["v"] == "Đoàn người"
    assert M.strip_own_labels({"options": [{"label": "A", "content": plain("A")}]})[1] == []


def test_hsa_watermark_and_solution_contradiction_unit():
    from app.review import remediate as M
    c = {"stem": plain("Câu hỏi"), "options": [{"label": "B", "content": [{"t": "p", "c": [
        {"t": "s", "v": "It eliminates the need."}, {"t": "br"}, {"t": "s", "v": "H"}, {"t": "tab"}, {"t": "s", "v": "S"},
        {"t": "tab"}, {"t": "s", "v": "A"}]}]}]}
    out, changed = M.strip_hsa_watermark(c)
    assert changed and out["options"][0]["content"][0]["c"] == [{"t": "s", "v": "It eliminates the need."}]
    q = {"options": [{"label": "A", "content": plain("Niềm đam mê học Luật và lối học dùi kinh nấu sử")},
                     {"label": "D", "content": plain("Niềm đam mê đọc các bản dịch và nguyên tác ở người viết")}],
         "solution": plain("Nội dung chính của đoạn trích là niềm đam mê học Luật và lối học dùi kinh nấu sử")}
    assert M.solution_contradicts({"kind": "choice", "labels": ["D"]}, q)
    assert M.solution_contradicts({"kind": "choice", "labels": ["A"]}, q) is None


def test_instruction_prefix_unit():
    from app.review import remediate as M
    pages = ["Choose A, B, C or D to complete each sentence.\nQuestion 1. She ___ to school.\nA. go\nB. goes\nC. going\nD. gone\n"
             "Question 2. They ___ football every Sunday afternoon in the park.\nA. play\nB. plays\nC. playing\nD. played\n"]
    c = {"stem": plain("Choose A, B, C or D to complete each sentence.") + plain("They ___ football every Sunday afternoon in the park."),
         "options": [{"label": l, "content": plain(t)} for l, t in zip("ABCD", ["play", "plays", "playing", "played"])]}
    assert M.check_source_text(c, pages).ok


def test_strip_furniture_unit():
    from app.review import remediate as M
    c = {"stem": plain("The organization encourages_______ in the local community. Tài liệu được sưu tầm miễn phí, nghiêm cấm sử dụng tài liệu kiếm lợi nhuận"),
         "options": [{"label": "A", "content": plain("volunteering")}],
         "solution": plain("Giải thích. ---------- Hết chủ đề Lịch sử, hãy làm chủ đề tiếp theo nếu đã chọn ----------")}
    out, changed = M.strip_furniture(c)
    assert changed and M.plain_text(out["stem"]).strip() == "The organization encourages_______ in the local community."
    assert M.plain_text(out["solution"]).strip() == "Giải thích."
    assert M.strip_furniture({"stem": plain("Mệnh đề nào dưới đây đúng?"), "options": []})[1] is False


def test_group_instruction_only():
    from app.review import content as Cn
    from app.review import remediate as M
    hdr = [{"t": "p", "c": [{"t": "s", "v": "Question "}, {"t": "range"},
                            {"t": "s", "v": ": Choose a suitable word or phrase (marked A, B, C or D) to fill in each blank."}]}]
    c = {"stem": plain("The lecture was so _______ that half the students fell asleep."),
         "group": {"key": "g", "header": hdr, "passage": []}}
    assert M.check_group_instruction(c).ok and M.group_english_instruction(c)
    assert "group_context_missing" in Cn.derived_flags(c, None)
    c["group"]["instruction_only"] = True
    assert "group_context_missing" not in Cn.derived_flags(c, None)
    # a numbered blank of a lost passage, a stem that leans on the passage, a header that announces data
    assert not M.check_group_instruction(dict(c, stem=plain("(31) ______"))).ok
    assert not M.check_group_instruction(dict(c, stem=plain("According to the passage, why did the author leave?"))).ok
    data = {"key": "g", "header": [{"t": "p", "c": [{"t": "s", "v": "Sử dụng thông tin dưới đây trả lời câu hỏi 3-5"}]}],
            "passage": []}
    assert not M.check_group_instruction(dict(c, group=data)).ok
    err = {"key": "g", "header": [{"t": "p", "c": [{"t": "s", "v": "Each of the following sentences has one error "
                                                                   "(A, B, C or D). Find it and blacken your choice on your answer sheet."}]}],
           "passage": []}
    assert not M.check_group_instruction(dict(c, group=err, stem=plain("He go to school every days by bus."))).ok
    pages = ["Question 1-2: Choose a suitable word or phrase (marked A, B, C or D) to fill in each blank.\n"
             "1. The lecture was so _______ that half the students fell asleep.\nA. Interesting B. Dull C. Lively D. Engaging"]
    stems = [plain("The lecture was so _______ that half the students fell asleep.")]
    assert M.check_group_source(c, stems, pages).ok
    lost = [pages[0].replace("blank.\n", "blank.\nThe history of the bicycle began in the early nineteenth century when "
                                         "inventors in Europe tried many designs for a human powered machine.\n")]
    assert not M.check_group_source(c, stems, lost).ok


def test_check_wrong_passage():
    from app.review import remediate as M
    hist = [{"t": "p", "c": [{"t": "s", "v": "Hiệp định Giơnevơ năm 1954 về Đông Dương được kí kết đã chấm dứt cuộc "
                                             "chiến tranh xâm lược của thực dân Pháp; cách mạng, kháng chiến, thực dân."}]}]
    c = {"stem": plain("Một con lắc lò xo dao động điều hòa với biên độ 5 cm và tần số 2 Hz. Vận tốc cực đại là"),
         "options": [{"label": l, "content": plain(t)} for l, t in zip("ABCD", ["20π cm/s", "10π cm/s", "5π cm/s", "2π cm/s"])],
         "group": {"key": "g", "header": plain("Dựa vào thông tin được cung cấp sau đây để trả lời"), "passage": hist}}
    assert M.check_wrong_passage(c).ok
    assert not M.check_wrong_passage(dict(c, stem=plain("Theo đoạn trích, hiệp định được kí năm nào?"))).ok
    assert not M.check_wrong_passage(dict(c, group={"key": "g", "header": [], "passage": []})).ok


def test_strip_verified_warns():
    from app.review import remediate as M
    warn = {"t": "warn", "v": "đối tượng không hiển thị được"}
    c = {"stem": [{"t": "p", "c": [{"t": "s", "v": "vấn đề đặt ra đối với lực lượng lao động, cụ thể:"}]},
                  {"t": "p", "c": [warn, {"t": "s", "v": "Một là, lao động phân bổ không đều giữa các vùng"}]}]}
    pages = ["vấn đề đặt ra đối với lực lượng lao động, cụ thể:\\nMột là, lao động phân bổ không đều giữa các vùng"]
    out, n, problem = M.strip_verified_warns(c, pages)
    assert n == 1 and problem is None and all(x.get("t") != "warn" for b in out["stem"] for x in b["c"])
    lost = ["vấn đề đặt ra đối với lực lượng lao động, cụ thể: bảng số liệu năm 2019 gồm nhiều cột\\n"
            "Một là, lao động phân bổ không đều giữa các vùng"]
    assert M.strip_verified_warns(c, lost)[2] is not None


def test_split_inline_options():
    from app.review import remediate as M
    opt = lambda l, tex: [{"m": ["b"], "t": "s", "v": l}, {"t": "m", "tex": ". " + tex}, {"t": "s", "v": "."}, {"t": "tab"}]
    c = {"type": "short_response", "stem": plain("Chiều dài tự nhiên của lò xo là") +
         [{"t": "p", "c": opt("A", "36 cm") + opt("B", "38 cm")}, {"t": "p", "c": opt("C", "40 cm") + opt("D", "42 cm")}]}
    out, ans, why = M.split_inline_options(c, {"kind": "text", "text": "C", "accepted": ["C"]})
    assert why == "" and ans == {"kind": "choice", "labels": ["C"]} and out["type"] == "single_choice"
    assert [o["label"] for o in out["options"]] == list("ABCD") and out["options"][0]["content"][0]["c"][0]["tex"] == "36 cm"
    assert len(out["stem"]) == 1 and M.check_structure(out).ok
    assert M.split_inline_options(c, {"kind": "text", "text": "36 cm"})[0] is None
    bad = dict(c, stem=c["stem"][:2])
    assert M.split_inline_options(bad, {"kind": "text", "text": "A"})[0] is None


def test_docx_passage():
    from app.review import remediate as M
    P = lambda t, kind="p", runs=None: {"kind": kind, "text": t, "runs": runs or [(t, [])]}
    long = "Sleep is food for the brain, so not sleeping well can be a severe problem for anyone. " * 5
    paras = [P("Lời giải"), P("Read the passage carefully"), P(long, runs=[(long[:20], []), (long[20:40], ["u"]), (long[40:], [])]),
             P("(Adapted from Prepare)"),
             P("Choose an option (A, B, C or D) that best answers each question from 53 to 60."), P("Câu 53:"),
             P("Which of the following can be the best title of the passage?")]
    hdr = "Choose an option (A, B, C or D) that best answers each question from 53 to 60."
    stem = plain("Which of the following can be the best title of the passage?")
    blocks, why = M.docx_passage(paras, hdr, stem)
    assert why == "" and len(blocks) == 2 and blocks[0]["c"][1] == {"t": "s", "v": long[20:40], "m": ["u"]}
    after = [P("Read the following passage and answer the questions from 1 to 5."), P(long), P("Câu 1:"),
             P("What is the main idea of the passage?")]
    assert M.docx_passage(after, "Read the following passage and answer the questions from 1 to 5.",
                          plain("What is the main idea of the passage?"))[0]
    bad = paras[:2] + [P("A. achieving B. attempting")] + paras[2:]
    assert M.docx_passage(bad, hdr, stem)[0] is None


def test_docx_passage_vi_marker():
    from app.review import remediate as M
    P = lambda t, kind="p": {"kind": kind, "text": t, "runs": [(t, [])]}
    long = "“Giữa các dân tộc, chúng ta không thể tự hào là nền văn hóa của ta đồ sộ, có những cống hiến lớn lao. " * 4
    paras = [P("Đọc đoạn trích sau đây và trả lời các câu hỏi bên dưới:"), P(long), P("(Trần Đình Hượu, Nhìn về vốn văn hóa dân tộc)"),
             P("Trả lời các câu hỏi: 56 - 60"), P("", "table"), P("Theo tác giả đoạn trích, người Việt Nam có sở trường nhất ở ngành nào?")]
    blocks, why = M.docx_passage(paras, "Trả lời các câu hỏi: 56 - 60", plain("Theo tác giả đoạn trích, người Việt Nam có sở trường nhất ở ngành nào?"))
    assert why == "" and len(blocks) == 2


def test_docx_passage_needs_marks():
    from app.review import remediate as M
    P = lambda t: {"kind": "p", "text": t, "runs": [(t, [])]}
    long = "Khi bị tấn công, cá nóc hớp không khí vào bụng, lúc này mình chúng phồng to như một trái bóng. " * 4
    paras = [P("Đọc đoạn trích sau đây và trả lời các câu hỏi:"), P(long), P("Trả lời các câu hỏi: 1 - 5"),
             P("Từ “lúc này” (gạch chân, in đậm) trong đoạn trích được dùng chỉ khoảng thời gian nào?")]
    out = M.docx_passage(paras, "Trả lời các câu hỏi: 1 - 5", plain("Từ “lúc này” (gạch chân, in đậm) trong đoạn trích được dùng chỉ khoảng thời gian nào?"))
    assert out[0] is None and "marks" in out[1]


def test_strip_section_tails():
    from app.review import remediate as M
    c = {"stem": plain("Cách nào sau đây?"),
         "options": [{"label": "A", "content": plain("Một.")},
                     {"label": "D", "content": plain("Bốn.ĐỀ THI THAM KHẢOKỲ THI ĐÁNH GIÁ NĂNG LỰC")}]}
    out, changed = M.strip_furniture(c)
    assert changed and M.plain_text(out["options"][1]["content"]).strip() == "Bốn."
    e = {"stem": plain("Which best restates?"), "options": [
        {"label": "D", "content": plain("John was eager.Read the passage carefully1. Like many emergency responders, he...")}]}
    out, changed = M.strip_furniture(e)
    assert changed and M.plain_text(out["options"][0]["content"]).strip() == "John was eager."


def test_option_section_header_paragraph():
    from app.review import remediate as M
    c = {"stem": plain("Câu hỏi?"), "options": [
        {"label": "A", "content": plain("Một")},
        {"label": "D", "content": plain("Tất cả các đáp án trên") + plain("1.2. TIẾNG ANH") +
         plain("Choose a suitable word or phrase (marked A, B, C or D) to fill in each blank.")}]}
    assert M.option_section_head(c) == "D"
    out, changed = M.strip_furniture(c)
    assert changed and M.option_section_head(out) is None and len(out["options"][1]["content"]) == 1
    g = {"stem": plain("Hạt được hình thành từ bộ phận nào?"), "options": [
        {"label": "A", "content": plain("Noãn đã thụ tinh.")},
        {"label": "D", "content": plain("Bầu nhuỵ.") + plain("3.4: Địa lí")}]}
    assert M.option_section_head(g) == "D"
    out, changed = M.strip_furniture(g)
    assert changed and M.option_section_head(out) is None and M.plain_text(out["options"][1]["content"]).strip() == "Bầu nhuỵ."
    assert M.option_section_head({"options": [{"label": "A", "content": plain("Địa lí")}]}) is None


def test_pdf_text_plain_guard_and_watermark():
    from app.review import remediate as M
    P = lambda *xs: [{"t": "p", "c": [{"t": "s", "v": x} for x in xs]}]  # noqa: E731
    assert M.flattened_math(P("Có bao nhiêu loại khối đa diện đều?")) is None
    assert M.flattened_math(P("Ông V gửi 200 triệu đồng với lãi suất 7,2% một năm.")) is None
    for bad in ("Cho số phức z thỏa mãn (3 + 2i)z = 4", "A102 .", "10 2.", "Giá trị log2 bằng", "x2"):
        assert M.flattened_math(P(bad)), bad
    assert M.flattened_math([{"t": "p", "c": [{"t": "s", "v": "a"}, {"t": "tab"}, {"t": "s", "v": "b"}]}])
    assert M.content_flattened_math({"stem": P("Nghiệm của phương trình là"), "options": []})
    wm = [{"t": "s", "v": "H"}, {"t": "tab"}, {"t": "s", "v": "S"}, {"t": "tab"}, {"t": "s", "v": "A"}]
    c = {"stem": [{"t": "p", "c": wm + [{"t": "br"}, {"t": "s", "v": "Câu hỏi?"}]}],
         "options": [{"label": "A", "content": [{"t": "p", "c": [{"t": "s", "v": "Một."}, {"t": "br"}] + wm}]}]}
    out, changed = M.strip_furniture(c)
    assert changed and M.plain_text(out["stem"]).strip() == "Câu hỏi?"
    assert M.plain_text(out["options"][0]["content"]).strip() == "Một."
