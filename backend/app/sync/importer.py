"""Idempotent, incremental, restartable import of the HSA upstream bank.

* keyed by (bank, external_id = cq_…); never creates duplicates (unique constraint + upsert)
* a question is rebuilt only when its raw upstream inputs changed (`source_hash`) or `--full`
* a changed render-ready content (`content_hash`) inserts a NEW immutable question_version and
  moves `question.current_version_id`; old versions — and every exam that used them — stay intact
* batches of BATCH questions per transaction; `sync_run.checkpoint` allows resuming
* questions missing upstream are flagged `removed_upstream` (never deleted)
* a Postgres advisory lock prevents concurrent runs
"""
import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Asset, Question, QuestionBank, QuestionVersion, SubjectAlias, SyncRun
from ..settings_store import get_setting
from .hsa_upstream import BUILDER_VERSION, BuiltQuestion, UpstreamSource, build_question
from .media import MediaStore
from .policy import PolicyInput, effective_served, evaluate

log = logging.getLogger(__name__)
BATCH = 500
LOCK_KEY = 72_110_001
REF_RE = re.compile(r"\{\{(f|img):((?:fm|as)_[0-9a-z_]+)\}\}")


def now():
    return dt.datetime.now(dt.timezone.utc)


def ensure_bank(db: Session, code="hsa") -> QuestionBank:
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    if bank is None:
        bank = QuestionBank(code=code, name="Ngân hàng HSA (HSA-question-bank)", source_kind="hsa_upstream",
                            description="Canonical bank synchronised read-only from HSA-question-bank")
        db.add(bank)
        db.flush()
    return bank


def source_hash(src: UpstreamSource, q: dict) -> str:
    """Hash of every raw upstream input that can influence the built question."""
    cid = q["canonical_question_id"]
    mds = [q.get("stem_md"), q.get("solution_md"), q.get("explanation_md")] + [o["content_md"] for o in q["options"]]
    if q["group"]:
        mds += [q["group"]["header_md"], q["group"]["content_md"], json.dumps(q["group"]["tables"])]
    mds.append(json.dumps(q["tables"]))
    refs = sorted(set(REF_RE.findall("\n".join(m or "" for m in mds))))
    deps = []
    for kind, rid in refs:
        if kind == "f":
            f = src.formulas.get(rid) or {}
            deps.append([rid, f.get("latex"), f.get("latex_confidence"), f.get("issues"), f.get("preview_asset_id"),
                         (src.assets.get(f.get("preview_asset_id") or "") or {}).get("sha256"),
                         src.f_over.get(rid)])
        else:
            deps.append([rid, (src.assets.get(rid) or {}).get("sha256"), src.asset_repl.get(rid),
                         src.fig_by_asset.get(rid)])
    payload = {
        "q": {k: v for k, v in q.items() if k not in ("rep", "doc", "group")},
        "rep": q["rep"], "doc": q["doc"], "group": q["group"],
        "ov": src.q_over.get(cid), "fig": src.fig_over.get(cid), "man": src.manifest_states.get(cid),
        "deps": deps, "builder": BUILDER_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _upsert_assets(db: Session, built: list[BuiltQuestion]):
    rows = {}
    for b in built:
        for sha, sf in b.media.items():
            rows[sha] = {"sha256": sha, "ext": sf.ext, "mime": {"jpg": "image/jpeg", "gif": "image/gif",
                                                                "svg": "image/svg+xml"}.get(sf.ext, "image/png"),
                         "bytes": sf.bytes, "width": sf.width, "height": sf.height, "derived_from": sf.derived_from}
    if rows:
        db.execute(pg_insert(Asset).values(list(rows.values())).on_conflict_do_nothing(index_elements=["sha256"]))


def apply_built(db: Session, bank_id: int, b: BuiltQuestion, shash: str, alias: dict, policy: dict,
                run_id: int | None, fingerprint: str, stats: dict):
    q = db.scalar(select(Question).where(Question.bank_id == bank_id, Question.external_id == b.external_id))
    created = q is None
    if created:
        q = Question(bank_id=bank_id, external_id=b.external_id, first_synced_at=now())
        db.add(q)
    q.question_type = b.question_type
    q.source_subject = b.source_subject
    q.subject_code = alias.get(b.source_subject or "", alias.get("", None))
    q.topic, q.subtopic, q.cognitive_level, q.language = b.topic, b.subtopic, b.cognitive_level, b.language
    q.group_key, q.exam_systems = b.group_key, list(b.exam_systems)
    q.has_image, q.has_formula, q.has_table = b.has_image, b.has_formula, b.has_table
    q.review_status, q.review_flags = b.review_status, list(b.review_flags)
    q.answer_source_type = b.answer_source_type
    q.editorial_state, q.editorial_states = b.states[0], list(b.states)
    q.state_source, q.state_notes = b.state_source, list(b.state_notes)
    q.answer_kind = b.answer["kind"] if b.answer else None
    q.content_flags = list(b.app_reasons)
    q.scoring_mode = _mode(b.scoring_mode, q.answer_kind, policy)
    q.provenance = b.provenance
    q.removed_upstream = False
    q.source_hash = shash
    q.last_synced_at = now()
    ok, reasons = evaluate(PolicyInput(q.editorial_state, q.question_type, q.scoring_mode, q.content_flags), policy)
    q.policy_eligible, q.policy_reasons = ok, reasons
    q.is_served = effective_served(ok, q.admin_override)
    db.flush()
    chash = b.content_hash
    cur = db.get(QuestionVersion, q.current_version_id) if q.current_version_id else None
    if cur is None or cur.content_hash != chash:
        existing = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == q.id,
                                                           QuestionVersion.content_hash == chash))
        if existing is None:
            vno = (db.scalar(select(QuestionVersion.version_no).where(QuestionVersion.question_id == q.id)
                             .order_by(QuestionVersion.version_no.desc()).limit(1)) or 0) + 1
            existing = QuestionVersion(question_id=q.id, version_no=vno, content_hash=chash, content=b.content,
                                       answer=b.answer, source_revision=fingerprint, sync_run_id=run_id)
            db.add(existing)
            db.flush()
            stats["versions_created"] += 1
            if not created:
                stats["content_changed"] += 1
                q.content_changed_at = now()
        q.current_version_id = existing.id
    stats["created" if created else "updated"] += 1


def _mode(built_mode: str, answer_kind: str | None, policy: dict) -> str:
    if answer_kind == "text" and policy.get("text_answers_auto_scored"):
        return "auto"
    return built_mode


def recompute_policy(db: Session, bank_id: int | None = None) -> dict:
    """Re-evaluate the serving policy for stored questions (after an admin policy change)."""
    policy = get_setting(db, "serving_policy")
    stmt = select(Question)
    if bank_id:
        stmt = stmt.where(Question.bank_id == bank_id)
    n = changed = 0
    for q in db.scalars(stmt.execution_options(yield_per=2000)):
        mode = q.scoring_mode
        if q.answer_kind == "text":
            mode = "auto" if policy.get("text_answers_auto_scored") else "self_check"
        ok, reasons = evaluate(PolicyInput(q.editorial_state, q.question_type, mode, q.content_flags,
                                           q.removed_upstream), policy)
        served = effective_served(ok, q.admin_override)
        if (ok, reasons, served, mode) != (q.policy_eligible, q.policy_reasons, q.is_served, q.scoring_mode):
            q.policy_eligible, q.policy_reasons, q.is_served, q.scoring_mode = ok, reasons, served, mode
            changed += 1
        n += 1
    db.flush()
    return {"evaluated": n, "changed": changed}


def sync_hsa(db: Session, upstream_root: Path, media_root: Path, full: bool = False, only: list | None = None,
             limit: int | None = None, triggered_by: str = "cli", progress=None) -> SyncRun:
    if not db.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}):
        raise RuntimeError("another synchronisation is running")
    try:
        return _sync(db, upstream_root, media_root, full, only, limit, triggered_by, progress)
    finally:
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        db.commit()


def _sync(db, upstream_root, media_root, full, only, limit, triggered_by, progress):
    t0 = time.time()
    bank = ensure_bank(db)
    db.commit()
    src = UpstreamSource(Path(upstream_root), MediaStore(Path(media_root)))
    fp = src.fingerprint()
    policy = get_setting(db, "serving_policy")
    alias = {a.source_value: a.subject_code for a in db.scalars(select(SubjectAlias).where(
        SubjectAlias.bank_id == bank.id))}

    # resume an interrupted run of the same snapshot
    last = db.scalar(select(SyncRun).where(SyncRun.bank_id == bank.id).order_by(SyncRun.id.desc()).limit(1))
    resume_from = None
    if last and last.status in ("interrupted", "failed", "running") and last.source_fingerprint == fp \
            and last.checkpoint and not only:
        run = last
        resume_from = last.checkpoint
        run.status, run.error = "running", None
        stats = dict(run.stats or {})
        log.info("resuming sync run %s after %s", run.id, resume_from)
    else:
        run = SyncRun(bank_id=bank.id, status="running", source_fingerprint=fp, triggered_by=triggered_by, stats={})
        db.add(run)
        stats = {}
    for k in ("seen", "created", "updated", "unchanged", "versions_created", "content_changed", "removed",
              "restored", "errors"):
        stats.setdefault(k, 0)
    stats["upstream_total"] = src.count()
    stats["manifest_states"] = len(src.manifest_states)
    db.commit()

    known = {eid: (qid, sh, rm) for qid, eid, sh, rm in db.execute(
        select(Question.id, Question.external_id, Question.source_hash, Question.removed_upstream)
        .where(Question.bank_id == bank.id))}
    batch_built, batch_hashes, last_id, n = [], [], resume_from, 0
    try:
        for q in src.iter_questions(after=resume_from, only=only):
            cid = q["canonical_question_id"]
            stats["seen"] += 1
            sh = source_hash(src, q)
            prev = known.get(cid)
            if prev and prev[1] == sh and not full and not prev[2]:
                stats["unchanged"] += 1
            else:
                if prev and prev[2]:
                    stats["restored"] += 1  # back upstream: re-apply (clears removed_upstream)
                try:
                    b = build_question(src, q, text_auto=False)
                    batch_built.append(b)
                    batch_hashes.append(sh)
                except Exception as ex:  # noqa: BLE001  one bad record must not stop the run
                    stats["errors"] += 1
                    stats.setdefault("error_ids", []).append(cid)
                    log.exception("building %s failed: %s", cid, ex)
            last_id = cid
            n += 1
            if len(batch_built) >= BATCH or (n % 5000 == 0):
                _flush(db, bank.id, batch_built, batch_hashes, alias, policy, run, fp, stats, last_id)
                batch_built, batch_hashes = [], []
                if progress:
                    progress(stats)
            if limit and n >= limit:
                break
        _flush(db, bank.id, batch_built, batch_hashes, alias, policy, run, fp, stats, last_id)

        if not only and not limit:
            up_ids = src.all_ids()
            removed = [eid for eid in known if eid not in up_ids]
            if removed:
                r = db.execute(update(Question).where(Question.bank_id == bank.id, Question.external_id.in_(removed),
                                                      Question.removed_upstream.is_(False))
                               .values(removed_upstream=True, is_served=False, policy_eligible=False,
                                       policy_reasons=["removed_upstream"]))
                stats["removed"] += r.rowcount
            db.execute(update(Question).where(Question.bank_id == bank.id, Question.removed_upstream.is_(False))
                       .values(last_synced_at=now()))
        run.status = "ok"
        run.finished_at = now()
        stats["seconds"] = round(time.time() - t0, 1) + float(stats.get("seconds_before_resume", 0))
        run.stats = stats
        db.commit()
    except BaseException as ex:
        db.rollback()
        run = db.merge(run)
        run.status = "interrupted" if isinstance(ex, KeyboardInterrupt) else "failed"
        run.error = f"{type(ex).__name__}: {ex}"[:2000]
        stats["seconds_before_resume"] = round(time.time() - t0, 1)
        run.stats = stats
        db.commit()
        raise
    return run


def _flush(db, bank_id, built, hashes, alias, policy, run, fp, stats, last_id):
    if built:
        _upsert_assets(db, built)
        for b, sh in zip(built, hashes):
            apply_built(db, bank_id, b, sh, alias, policy, run.id, fp, stats)
    run.checkpoint = last_id
    run.stats = dict(stats)
    db.commit()
