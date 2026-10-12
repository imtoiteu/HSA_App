"""`hsa-app review-refresh`: incremental validation and queue assignment for the whole bank.

Only questions marked dirty (content/metadata changed by sync, recompute-policy or review actions) or validated
by an older VALIDATOR_VERSION are processed; `--all` re-derives everything. Runs in the scheduler (niced).
"""
import collections
import logging
import time
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, lazyload

from ..models import FormulaCheck, Question, QuestionReview, QuestionVersion, ReviewFinding
from . import content as C
from . import service as S
from . import validators as V

log = logging.getLogger(__name__)


def _dup_index(db: Session) -> dict:
    """In-memory duplicate key index over every question's working version (one pass)."""
    idx = {"exact_duplicate": collections.defaultdict(list), "near_duplicate": collections.defaultdict(list)}
    st = (select(Question.id, QuestionVersion.content)
          .join(QuestionReview, QuestionReview.question_id == Question.id)
          .join(QuestionVersion, QuestionVersion.id == func_coalesce(QuestionReview.draft_version_id,
                                                                     Question.current_version_id))
          .where(Question.removed_upstream.is_(False)))
    for qid, content in db.execute(st.execution_options(yield_per=2000)):
        ex, near = V.duplicate_keys(content)
        if ex:
            idx["exact_duplicate"][C.h(ex)].append(qid)
        if near:
            idx["near_duplicate"][C.h(near)].append(qid)
    return idx


def func_coalesce(a, b):
    from sqlalchemy import func
    return func.coalesce(a, b)


def refresh_all(db: Session, media_root: Path | None, upstream_root: Path | None = None, all_: bool = False,
                limit: int | None = None, batch: int = 500, progress=None) -> dict:
    t0 = time.time()
    cond = [QuestionReview.dirty.is_(True), QuestionReview.validator_version.is_(None),
            QuestionReview.validator_version != V.VALIDATOR_VERSION]
    # rows missing for questions created outside the importer
    db.execute(QuestionReview.__table__.insert().from_select(
        ["question_id"], select(Question.id).where(~select(QuestionReview.question_id)
                                                  .where(QuestionReview.question_id == Question.id).exists())))
    db.commit()
    ids_q = select(QuestionReview.question_id)
    if not all_:
        ids_q = ids_q.where(or_(*cond))
    ids = list(db.scalars(ids_q.order_by(QuestionReview.question_id)))
    if limit:
        ids = ids[:limit]
    stats = collections.Counter(candidates=len(ids))
    if not ids:
        return dict(stats, seconds=round(time.time() - t0, 1))
    failing = set(db.scalars(select(FormulaCheck.tex_sha).where(FormulaCheck.ok.is_(False))))
    dup = None
    if len(ids) > 2000:
        idx = _dup_index(db)
        ext = dict(db.execute(select(Question.id, Question.external_id)).all())

        def dup(code, key):
            if code == "ext":
                return [ext.get(i, "?") for i in key]
            return idx[code].get(key, [])
        stats["dup_index_s"] = round(time.time() - t0, 1)
    pages = {}
    if upstream_root is not None:
        from .sources import Upstream
        pages = Upstream(upstream_root).page_map()
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        qs = {q.id: q for q in db.scalars(select(Question).options(lazyload(Question.current_version))
                                          .where(Question.id.in_(chunk)))}
        rs = {r.question_id: r for r in db.scalars(select(QuestionReview).where(QuestionReview.question_id.in_(chunk)))}
        vids = {r.draft_version_id or (qs[r.question_id].current_version_id if r.question_id in qs else None)
                for r in rs.values()}
        # strong references keep the prefetched rows in the identity map (it is weak-referencing)
        versions = list(db.scalars(select(QuestionVersion).where(QuestionVersion.id.in_([v for v in vids if v]))))
        fnd = collections.defaultdict(list)
        for f in db.scalars(select(ReviewFinding).where(ReviewFinding.question_id.in_(chunk))):
            fnd[f.question_id].append(f)
        for qid in chunk:
            q, r = qs.get(qid), rs.get(qid)
            if q is None or r is None:
                continue
            occ = (q.provenance or {}).get("representative_occurrence_id")
            if occ in pages:
                r.source_page = pages[occ]
            before = r.validated_hash
            S.refresh(db, q, r, media_root=media_root, failing=failing, force=all_, dup_lookup=dup,
                      findings=fnd[qid])
            stats["validated" if r.validated_hash != before or all_ else "skipped_unchanged"] += 1
        db.commit()
        del versions
        db.expunge_all()
        if progress:
            progress(i + len(chunk), len(ids))
    stats["seconds"] = round(time.time() - t0, 1)
    return dict(stats)
