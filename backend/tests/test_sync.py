"""Question synchronisation: import, eligibility, overlays, idempotency, versioning, restartability."""
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.models import Asset, Question, QuestionBank, QuestionVersion, SyncRun
from app.sync.importer import sync_hsa

UP = lambda: Path(os.environ["HSA_UPSTREAM_ROOT"])  # noqa: E731
MEDIA = lambda: Path(os.environ["HSA_MEDIA_ROOT"])  # noqa: E731


def q_by(db, ext):
    return db.scalar(select(Question).where(Question.external_id == ext))


def test_import_counts_and_no_duplicates(synced, db):
    ids = synced["ids"]
    hsa = db.scalar(select(QuestionBank.id).where(QuestionBank.code == "hsa"))
    n = db.scalar(select(func.count()).where(Question.bank_id == hsa))
    assert n == len(ids)
    assert db.scalar(select(func.count(func.distinct(Question.external_id))).where(Question.bank_id == hsa)) == n
    assert synced["run_stats"]["created"] == n and synced["run_stats"]["errors"] == 0


def test_eligibility_rules(synced, db):
    ids = synced["ids"]
    served = lambda k: q_by(db, ids[k]).is_served  # noqa: E731
    state = lambda k: q_by(db, ids[k]).editorial_state  # noqa: E731
    assert served("m1") and served("v1") and served("n1") and served("img_tbl") and served("g1_0")
    assert state("noanswer") == "NEEDS_ANSWER_LINKING" and not served("noanswer")
    assert state("candidate") == "NEEDS_ANSWER_LINKING" and not served("candidate")
    assert q_by(db, ids["candidate"]).current_version.answer is None  # never inferred
    assert state("badformula") == "NEEDS_FORMULA_REVIEW" and not served("badformula")
    assert state("lowres") == "NEEDS_VISUAL_REVIEW" and not served("lowres")
    assert state("pdf_math") == "NEEDS_FORMULA_REVIEW"
    assert state("rejected") == "REJECTED" and not served("rejected")
    g2 = q_by(db, ids["g2_0"])
    assert "group_context_missing" in g2.policy_reasons and not g2.is_served
    si = q_by(db, ids["short_inline"])
    assert "inline_options_in_short_response" in si.policy_reasons and not si.is_served
    st = q_by(db, ids["short_text"])
    assert st.scoring_mode == "self_check" and st.is_served  # practice only (self-check)


def test_upstream_manifest_state_is_authoritative(synced, db):
    q = q_by(db, synced["ids"]["manifest_flagged"])
    assert q.state_source == "upstream_manifest" and q.editorial_state == "NEEDS_VISUAL_REVIEW" and not q.is_served
    assert q_by(db, synced["ids"]["m1"]).state_source == "derived"


def test_overlays_applied(synced, db):
    q = q_by(db, synced["ids"]["overlay"])
    assert q.editorial_state == "READY_TO_SERVE" and q.is_served  # reconstructed PDF item becomes servable
    stem = q.current_version.content["stem"][0]["c"]
    assert {"t": "m", "tex": "(P): x+y=0"} in stem
    opt_b = q.current_version.content["options"][1]["content"][0]["c"]
    assert opt_b == [{"t": "m", "tex": "(1;1)"}]
    deco = q_by(db, synced["ids"]["deco"]).current_version.content
    assert not any(b["t"] == "img" for b in deco["stem"])  # decorative badge removed


def test_render_content_formulas_images_tables_groups(synced, db):
    c = q_by(db, synced["ids"]["img_tbl"]).current_version.content
    img = [b for b in c["stem"] if b["t"] == "img"][0]
    assert (MEDIA() / img["src"]).exists() and img["w"] == 420
    assert db.get(Asset, img["src"].split("/")[1].split(".")[0]) is not None
    tbl = [b for b in c["stem"] if b["t"] == "table"][0]
    assert tbl["rows"][0][1]["rowspan"] == 2
    m = q_by(db, synced["ids"]["m1"]).current_version.content
    assert any(n["t"] == "m" for n in m["stem"][0]["c"]) and m["shuffle_safe"]
    g = q_by(db, synced["ids"]["g1_0"]).current_version.content["group"]
    assert g["passage"][0]["c"][0]["v"].startswith("Hà Nội") and {"t": "range"} in g["header"][0]["c"]
    assert not q_by(db, synced["ids"]["all_above"]).current_version.content["shuffle_safe"]
    assert not q_by(db, synced["ids"]["err_id"]).current_version.content["shuffle_safe"]
    assert q_by(db, synced["ids"]["tf_count"]).current_version.answer == \
        {"kind": "numeric", "value": 3.0, "text": "3", "unit": None}


def test_second_sync_is_a_noop(synced):
    db = SessionLocal()
    before = db.scalar(select(func.count()).select_from(QuestionVersion))
    run = sync_hsa(db, UP(), MEDIA())
    assert run.stats["unchanged"] == run.stats["seen"] and run.stats["versions_created"] == 0
    assert db.scalar(select(func.count()).select_from(QuestionVersion)) == before
    db.close()


@pytest.fixture()
def mutable_upstream(synced, tmp_path):
    """Work on a copy of the fixture upstream so other tests keep the original."""
    root = tmp_path / "up"
    shutil.copytree(UP(), root)
    return root


def test_changed_content_creates_new_version_and_keeps_old(synced, mutable_upstream):
    cid = synced["ids"]["m2"]
    db = SessionLocal()
    q = q_by(db, cid)
    old_vid, old_hash = q.current_version_id, q.current_version.content_hash
    con = sqlite3.connect(mutable_upstream / "question-bank/sqlite/hsa_question_bank.sqlite")
    con.execute("UPDATE canonical_question SET stem_md = stem_md || ' (đã sửa)' WHERE canonical_question_id=?", (cid,))
    con.commit()
    con.close()
    run = sync_hsa(db, mutable_upstream, MEDIA())
    assert run.stats["content_changed"] == 1 and run.stats["versions_created"] == 1
    db.expire_all()
    q = q_by(db, cid)
    assert q.current_version_id != old_vid and q.current_version.version_no == 2
    old = db.get(QuestionVersion, old_vid)
    assert old is not None and old.content_hash == old_hash  # history preserved
    # versions are immutable at the database level
    with pytest.raises(Exception):
        db.execute(text("UPDATE question_version SET content = '{}'::jsonb WHERE id = :i"), {"i": old_vid})
        db.commit()
    db.rollback()
    # restore the original upstream so later tests see version 1 content again (new pointer, same old row)
    run = sync_hsa(db, UP(), MEDIA())
    db.expire_all()
    q = q_by(db, cid)
    assert q.current_version_id == old_vid and run.stats["versions_created"] == 0
    db.close()


def test_removed_upstream_is_flagged_not_deleted(synced, mutable_upstream):
    cid = synced["ids"]["m3"]
    con = sqlite3.connect(mutable_upstream / "question-bank/sqlite/hsa_question_bank.sqlite")
    con.execute("DELETE FROM canonical_question WHERE canonical_question_id=?", (cid,))
    con.commit()
    con.close()
    db = SessionLocal()
    run = sync_hsa(db, mutable_upstream, MEDIA())
    assert run.stats["removed"] == 1
    db.expire_all()
    q = q_by(db, cid)
    assert q.removed_upstream and not q.is_served and q.current_version_id is not None
    sync_hsa(db, UP(), MEDIA())  # comes back
    db.expire_all()
    q = q_by(db, cid)
    assert not q.removed_upstream and q.is_served
    db.close()


def test_interrupted_sync_resumes_from_checkpoint(synced, mutable_upstream, monkeypatch):
    import app.sync.importer as imp
    # force every question to be rebuilt by changing the builder fingerprint input
    monkeypatch.setattr(imp, "BATCH", 5)
    calls = {"n": 0}

    def boom(stats):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt

    db = SessionLocal()
    with pytest.raises(KeyboardInterrupt):
        sync_hsa(db, mutable_upstream, MEDIA(), full=True, progress=boom)
    run = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    assert run.status == "interrupted" and run.checkpoint
    cp = run.checkpoint
    run2 = sync_hsa(db, mutable_upstream, MEDIA(), full=True)
    assert run2.id == run.id and run2.status == "ok"
    assert run2.stats["seen"] == len(synced["ids"])  # nothing processed twice, nothing skipped
    assert cp < max(synced["ids"].values())
    assert db.scalar(select(func.count(func.distinct(Question.external_id)))) == \
        db.scalar(select(func.count()).select_from(Question))
    db.close()


def test_concurrent_sync_is_refused(synced):
    a, b = SessionLocal(), SessionLocal()
    a.execute(text("SELECT pg_advisory_lock(72110001)"))
    try:
        with pytest.raises(RuntimeError):
            sync_hsa(b, UP(), MEDIA())
    finally:
        a.execute(text("SELECT pg_advisory_unlock(72110001)"))
        a.close()
        b.close()
