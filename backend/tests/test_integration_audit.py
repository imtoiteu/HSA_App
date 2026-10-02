"""Question-bank integration: subject inference, deferred documents, unsupported types, formula checks,
reconciliation report, bank management, manual questions and curated exam sets."""
import hashlib
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import FormulaCheck, Question, QuestionBank, SourceDocument
from app.sync.audit import run_audit, to_markdown
from app.sync.importer import apply_formula_checks, recompute_policy
from app.sync.subjects import resolve_subject

UP = lambda: Path(os.environ["HSA_UPSTREAM_ROOT"])  # noqa: E731
MEDIA = lambda: Path(os.environ["HSA_MEDIA_ROOT"])  # noqa: E731
ALIAS = {"math": "math", "physics": "physics", "chemistry": "chemistry", "science": "science", "": "general"}
POLICY = {"subject_inference_confidence": ["high", "medium"], "generic_source_subjects": ["", "science", "logic_reasoning"]}


def q_by(db, ext):
    return db.scalar(select(Question).where(Question.external_id == ext))


# ---------------------------------------------------------------------------------------------- pure
def test_subject_resolution_rules():
    r = lambda *a: resolve_subject(*a, ALIAS, POLICY)  # noqa: E731
    assert r("math", "physics", "high", None) == ("math", "original")          # specific original wins
    assert r(None, "physics", "medium", None) == ("physics", "inferred")
    assert r(None, "physics", "low", None) == ("general", "unclassified")      # low confidence never used
    assert r("science", "chemistry", "high", None) == ("chemistry", "inferred")  # generic original refined
    assert r("science", None, None, None) == ("science", "original")           # generic kept when no inference
    assert r(None, None, None, None) == ("general", "unclassified")
    assert r(None, "physics", "high", "history") == ("history", "admin")        # admin override wins
    assert r(None, "physics", "high", None)[0] == "physics"


def test_collect_tex_walks_whole_content_document():
    from app.sync.subjects import collect_tex
    content = {"stem": [{"t": "p", "c": [{"t": "m", "tex": "a"}]}],
               "options": [{"label": "A", "content": [{"t": "p", "c": [{"t": "m", "tex": "b"}]}]}],
               "group": {"header": [], "passage": [{"t": "table", "rows": [[{"c": [{"t": "p", "c": [{"t": "m", "tex": "c"}]}]}]]}]},
               "solution": [{"t": "p", "c": [{"t": "m", "tex": "d"}]}], "explanation": None}
    assert collect_tex(content) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------------------------- sync
def test_inference_applied_and_kept_separately(synced, db):
    ids = synced["ids"]
    q = q_by(db, ids["inf_phys"])
    assert (q.source_subject, q.inferred_subject, q.inference_confidence) == (None, "physics", "medium")
    assert q.subject_code == "physics" and q.subject_source == "inferred" and q.inference_evidence
    low = q_by(db, ids["inf_low"])
    assert low.subject_code == "general" and low.subject_source == "unclassified" and low.inferred_subject == "chemistry"
    sci = q_by(db, ids["inf_sci"])
    assert sci.source_subject == "science" and sci.subject_code == "chemistry"
    assert q_by(db, ids["m1"]).subject_source == "original"


def test_upstream_effective_subject_takes_precedence(synced, db):
    ids = synced["ids"]
    q = q_by(db, ids["m4"])  # validated correction of a mislabelled original
    assert (q.source_subject, q.upstream_effective_subject, q.classification_source) == ("math", "chemistry",
                                                                                         "semantic_correction")
    assert q.subject_code == "chemistry" and q.subject_source == "upstream_effective"
    v = q_by(db, ids["v2"])  # flagged for review upstream: keep the original subject
    assert v.subject_code == "literature" and v.subject_source == "original" and v.classification_review
    n = q_by(db, ids["inf_none"])
    assert n.subject_code == "history" and n.subject_source == "upstream_effective"
    assert q_by(db, ids["inf_phys"]).subject_source == "inferred"  # no upstream record: second pass


def test_inference_threshold_is_configurable(admin, synced):
    ids = synced["ids"]
    cur = admin.get("/api/admin/settings/serving_policy").json()["value"]
    admin.put("/api/admin/settings/serving_policy", {"value": dict(cur, subject_inference_confidence=["high"])})
    db = SessionLocal()
    assert q_by(db, ids["inf_phys"]).subject_code == "general"   # medium no longer accepted
    assert q_by(db, ids["inf_sci"]).subject_code == "chemistry"  # high still accepted
    db.close()
    admin.put("/api/admin/settings/serving_policy", {"value": cur})
    db = SessionLocal()
    assert q_by(db, ids["inf_phys"]).subject_code == "physics"
    db.close()


def test_admin_subject_override_survives_resync(admin, synced):
    from app.sync.importer import sync_hsa
    ids = synced["ids"]
    qid = admin.get(f"/api/admin/questions?q={ids['inf_none']}").json()["items"][0]["id"]
    r = admin.post(f"/api/admin/questions/{qid}/subject", {"subject_code": "logic", "note": "đã kiểm tra"})
    assert r.json() == {"subject": "logic", "subject_source": "admin"}
    assert admin.post(f"/api/admin/questions/{qid}/subject", {"subject_code": "nope"}).status_code == 422
    db = SessionLocal()
    sync_hsa(db, UP(), MEDIA(), full=True)  # rebuild everything: the app-level override must stay
    db.expire_all()
    assert q_by(db, ids["inf_none"]).subject_code == "logic"
    db.close()
    assert admin.post(f"/api/admin/questions/{qid}/subject", {"subject_code": None}).json()["subject"] == "history"
    d = admin.get(f"/api/admin/questions/{qid}").json()
    assert d["subject_override"] is None and "inferred_subject" in d
    filt = admin.get("/api/admin/questions?subject_source=inferred").json()
    assert filt["total"] >= 2 and all(i["subject_source"] == "inferred" for i in filt["items"])


def test_unsupported_type_imported_but_not_served(synced, db):
    q = q_by(db, synced["ids"]["unknown_type"])
    assert q is not None and not q.is_served
    assert "unsupported_for_serving" in q.policy_reasons


def test_passage_attached_to_wrong_question_is_not_served(synced, db):
    ok, bad = q_by(db, synced["ids"]["g3_in"]), q_by(db, synced["ids"]["g3_out"])
    assert ok.is_served and "group_membership_suspect" not in ok.policy_reasons
    assert not bad.is_served and "group_membership_suspect" in bad.policy_reasons
    header = ok.current_version.content["group"]["header"][0]["c"]
    text = "".join(n.get("v", "") for n in header)
    assert {"t": "range"} in header and not any(ch.isdigit() for ch in text)


def test_placeholder_in_shared_passage_blocks_serving(synced):
    db = SessionLocal()
    q = q_by(db, synced["ids"]["g4_warn"])
    assert q.editorial_state == "READY_TO_SERVE"
    assert not q.is_served and "render_warning" in q.policy_reasons
    # the stored-content refresh restores the flag on versions built before the check covered passages
    q.content_flags = [f for f in q.content_flags if f != "render_warning"]
    db.commit()
    recompute_policy(db)
    db.commit()
    assert q_by(db, synced["ids"]["g4_warn"]).is_served
    apply_formula_checks(db)
    db.commit()
    db.expire_all()
    q = q_by(db, synced["ids"]["g4_warn"])
    assert "render_warning" in q.content_flags and not q.is_served
    db.close()


def test_key_printed_in_stem_is_removed(synced, db):
    q = q_by(db, synced["ids"]["key_in_stem"])
    stem = q.current_version.content["stem"]
    assert len(stem) == 1 and "4,5" not in json.dumps(stem, ensure_ascii=False)
    assert q.provenance["answer_removed_from_stem"] == "4,5" and q.current_version.answer["value"] == 4.5
    assert q.is_served


def test_deferred_source_documents_and_bank_metadata(synced, db):
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == "hsa"))
    docs = db.scalars(select(SourceDocument).where(SourceDocument.bank_id == bank.id)).all()
    assert len(docs) == 1 and docs[0].status == "NEEDS_MATH_AWARE_OCR" and docs[0].pages == 120
    assert docs[0].path == "Đề scan/Tập 2.pdf"
    assert bank.version and bank.last_synced_at and bank.source_uri


# ---------------------------------------------------------------------------------------------- formulas
def test_formula_check_flags_and_excludes(synced):
    ids = synced["ids"]
    db = SessionLocal()
    q = q_by(db, ids["m7"])
    assert q.is_served
    tex = [n["tex"] for n in q.current_version.content["stem"][0]["c"] if n["t"] == "m"][0]
    sha = hashlib.sha256(tex.encode()).hexdigest()
    db.merge(FormulaCheck(tex_sha=sha, tex=tex, ok=False, error="ParseError", renderer="katex-test"))
    db.commit()
    r = apply_formula_checks(db)
    db.commit()
    db.expire_all()
    q = q_by(db, ids["m7"])
    assert r["flags_changed"] >= 1 and "formula_render_error" in q.content_flags and not q.is_served
    db.merge(FormulaCheck(tex_sha=sha, tex=tex, ok=True, error=None, renderer="katex-test"))
    db.commit()
    apply_formula_checks(db)
    db.commit()
    db.expire_all()
    assert q_by(db, ids["m7"]).is_served
    db.close()


# ---------------------------------------------------------------------------------------------- audit
def test_reconciliation_report(synced):
    db = SessionLocal()
    r = run_audit(db, UP(), MEDIA(), deep=True)
    rec = r["reconciliation"]
    n = len(synced["ids"])
    assert rec["UPSTREAM CANONICAL total"] == n and rec["APP IMPORTED UNIQUE total"] == n
    assert rec["MISSING FROM APP"] == 0 and rec["EXTRA/STALE IN APP"] == 0 and rec["DUPLICATES"] == 0
    assert rec["CHANGED UPSTREAM SINCE SYNC"] == 0
    assert rec["QUESTIONS MISSING RESOLVABLE ANSWERS (served auto-scored)"] == 0
    assert rec["UNSUPPORTED QUESTION TYPES (imported, not served)"] == {"open_or_unknown": 1}
    assert rec["DEFERRED SOURCE DOCUMENTS (scanned PDFs, NEEDS_MATH_AWARE_OCR)"] == 1
    up = r["upstream"]
    assert up["subject_inference"]["inferred"] == 3 and up["canonical_questions"] == n
    app = r["app"]
    assert app["served_missing_assets"] == 0 and app["counts"].get("group_snapshot_missing", 0) == 0
    assert app["subject_source"]["inferred"] >= 2
    assert "physics" in app["effective_subject"] and app["subject_without_inference"]["general"] >= 3
    assert "# Question-bank audit" in to_markdown(r)
    db.close()


def test_audit_detects_missing_and_changed(synced, tmp_path):
    import shutil
    import sqlite3
    root = tmp_path / "up"
    shutil.copytree(UP(), root)
    con = sqlite3.connect(root / "question-bank/sqlite/hsa_question_bank.sqlite")
    con.execute("INSERT INTO canonical_question (canonical_question_id, question_type, stem_md, review_status, "
                "review_flags, representative_occurrence_id, n_occurrences, answer_source_type) "
                "VALUES ('cq_ffffffffffffffff','single_choice','Câu mới','auto_accepted','[]','qo_x',1,'UNRESOLVED')")
    con.execute("UPDATE canonical_question SET stem_md = stem_md || '!' WHERE canonical_question_id = ?",
                (synced["ids"]["m9"],))
    con.commit()
    con.close()
    db = SessionLocal()
    r = run_audit(db, root, MEDIA(), deep=True)
    assert r["reconciliation"]["MISSING FROM APP"] == 1
    assert r["app"]["missing_from_app"] == ["cq_ffffffffffffffff"]
    assert r["reconciliation"]["CHANGED UPSTREAM SINCE SYNC"] >= 2  # the new one and the edited one
    db.close()


def test_admin_audit_queue_and_report(admin, synced):
    from app.cli import run_queued_audit
    assert admin.post("/api/admin/audit/question-bank").json()["queued"]
    db = SessionLocal()
    run_queued_audit(db)
    db.close()
    rep = admin.get("/api/admin/audit/question-bank").json()
    assert rep["queued"] is False and rep["report"]["reconciliation"]["DUPLICATES"] == 0


# ---------------------------------------------------------------------------------------------- banks & sets
def test_bank_management_and_manual_questions(admin, student):
    code = f"manual_{uuid.uuid4().hex[:5]}"
    r = admin.post("/api/admin/banks", {"code": code, "name": "Câu hỏi tự soạn", "description": "Giáo viên nhập tay",
                                        "version": "2026.1"})
    assert r.status_code == 200 and r.json()["source_kind"] == "manual"
    rec = {"external_id": "tu-soan-1", "type": "single_choice", "subject": "physics", "status": "READY_TO_SERVE",
           "stem_md": "Đơn vị của lực là", "options": [{"label": "A", "md": "N"}, {"label": "B", "md": "J"}],
           "answer": {"kind": "labels", "labels": ["A"]}, "solution_md": "Newton (N)."}
    r = admin.post(f"/api/admin/banks/{code}/questions", rec)
    assert r.status_code == 200 and r.json()["served"] and r.json()["created"]
    r2 = admin.post(f"/api/admin/banks/{code}/questions", dict(rec, stem_md="Đơn vị đo lực trong hệ SI là"))
    assert not r2.json()["created"] and r2.json()["new_version"]
    assert admin.post("/api/admin/banks/hsa/questions", rec).status_code == 422
    bank = [b for b in admin.get("/api/admin/banks").json()["items"] if b["code"] == code][0]
    assert bank["served"] == 1 and bank["version"] == "2026.1"
    # an exam set mixing a fixed question with a random pool, restricted to this bank
    cfg = {"sections": [{"key": "a", "title": "A", "items": [{"external_id": "tu-soan-1", "bank": code}]}],
           "timing": "none"}
    bid = admin.post("/api/admin/blueprints", {"code": f"set_{uuid.uuid4().hex[:5]}", "name": "Đề tự soạn",
                                              "kind": "fixed", "config": cfg, "is_published": True}).json()["id"]
    sid = student.post("/api/sessions", {"blueprint_id": bid}).json()["session"]["id"]
    assert len(student.get(f"/api/sessions/{sid}").json()["items"]) == 1
    student.post(f"/api/sessions/{sid}/submit")  # otherwise a new start would resume this attempt
    # deactivating the bank removes its questions from serving and from exam generation
    r = admin.put(f"/api/admin/banks/{code}", {"name": bank["name"], "description": None, "version": "2026.1",
                                               "is_active": False, "settings": {}})
    assert r.json()["is_active"] is False and r.json()["served"] == 0
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 409


def test_mixed_section_fixed_items_then_pool(admin, student, synced):
    ids = synced["ids"]
    cfg = {"sections": [{"key": "mix", "title": "Trộn", "items": [{"external_id": ids["m1"]}, {"external_id": ids["m2"]}],
                         "pools": [{"subjects": ["math"], "types": ["single_choice"], "count": 3}]}],
           "timing": "none", "shuffle_options": False}
    v = admin.post("/api/admin/blueprints/validate", {"config": cfg}).json()
    assert v["valid"] and v["ok"]
    bid = admin.post("/api/admin/blueprints", {"code": f"mix_{uuid.uuid4().hex[:5]}", "name": "Trộn", "config": cfg,
                                              "is_published": True}).json()["id"]
    sid = student.post("/api/sessions", {"blueprint_id": bid}).json()["session"]["id"]
    items = student.get(f"/api/sessions/{sid}").json()["items"]
    db = SessionLocal()
    ext = [db.get(Question, i["question_ref"]).external_id for i in items]
    db.close()
    assert ext[:2] == [ids["m1"], ids["m2"]] and len(ext) == 5 and len(set(ext)) == 5
    bad = dict(cfg, sections=[dict(cfg["sections"][0], items=[{"external_id": ids["m1"]}, {"external_id": ids["m1"]}])])
    assert admin.post("/api/admin/blueprints/validate", {"config": bad}).json()["valid"] is False
