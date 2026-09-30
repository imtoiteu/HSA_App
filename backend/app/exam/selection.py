"""Deterministic question selection and option permutation.

Order keys are SHA-256 digests of (seed, context, id) — independent of any PRNG implementation, so
the same seed and candidate pool always give the same exam. Sessions additionally store the exact
items, so reviewing never depends on re-running this code.
"""
import hashlib
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Question, QuestionBank
from .blueprint import BlueprintConfig, Pool

GENERATOR_VERSION = "sel-1"


class SelectionError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or {}


def hkey(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def option_permutation(seed: str, external_id: str, labels: list[str]) -> list[str]:
    return sorted(labels, key=lambda l: hkey(seed, "opt", external_id, l))


@dataclass
class Candidate:
    question_id: int
    external_id: str
    version_id: int
    group_key: str | None
    order_hint: tuple


@dataclass
class Picked:
    section_index: int
    question_id: int
    external_id: str
    version_id: int
    group_key: str | None
    points: float
    scoring_mode: str = "auto"


@dataclass
class Selection:
    items: list[Picked] = field(default_factory=list)
    pool_fingerprint: str = ""


def _pool_query(db: Session, pool: Pool, require_auto: bool, extra_filter=None):
    stmt = select(Question.id, Question.external_id, Question.current_version_id, Question.group_key,
                  Question.provenance["source_question_number"].astext, Question.scoring_mode) \
        .join(QuestionBank, QuestionBank.id == Question.bank_id) \
        .where(Question.is_served.is_(True), Question.current_version_id.is_not(None),
               QuestionBank.is_active.is_(True))
    if require_auto:
        stmt = stmt.where(Question.scoring_mode == "auto")
    else:
        stmt = stmt.where(Question.scoring_mode.in_(["auto", "self_check"]))
    if pool.subjects:
        stmt = stmt.where(Question.subject_code.in_(pool.subjects))
    if pool.types:
        stmt = stmt.where(Question.question_type.in_(pool.types))
    if pool.exam_systems:
        stmt = stmt.where(Question.exam_systems.overlap(pool.exam_systems))
    if pool.topics:
        stmt = stmt.where(Question.topic.in_(pool.topics))
    if pool.banks:
        stmt = stmt.where(QuestionBank.code.in_(pool.banks))
    if pool.cognitive_levels:
        stmt = stmt.where(Question.cognitive_level.in_(pool.cognitive_levels))
    if extra_filter is not None:
        stmt = extra_filter(stmt)
    return db.execute(stmt).all()


def _num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 10**6


def count_available(db: Session, pool: Pool, require_auto: bool) -> int:
    return len(_pool_query(db, pool, require_auto))


def select_questions(db: Session, cfg: BlueprintConfig, seed: str, extra_filter=None) -> Selection:
    sel = Selection()
    used: set[int] = set()
    fp = hashlib.sha256()
    for si, section in enumerate(cfg.sections):
        section_items: list[list[Picked]] = []
        if section.items:
            section_items = [[p] for p in _fixed_items(db, section, si)]
            used.update(p.question_id for unit in section_items for p in unit)
        for pi, pool in enumerate(section.pools):
            rows = _pool_query(db, pool, cfg.require_auto_scoring, extra_filter)
            modes = {}
            cands = []
            for qid, eid, vid, gk, num, mode in rows:
                if qid in used:
                    continue
                cands.append(Candidate(qid, eid, vid, gk, (_num(num), eid)))
                modes[qid] = mode
            fp.update(f"{si}:{pi}:".encode() + ",".join(sorted(c.external_id for c in cands)).encode())
            units: dict[str, list[Candidate]] = {}
            for c in cands:
                key = f"g:{c.group_key}" if (c.group_key and cfg.keep_groups_together) else f"q:{c.external_id}"
                units.setdefault(key, []).append(c)
            ordered = sorted(units.items(), key=lambda kv: hkey(seed, section.key, pi, kv[0]))
            need = pool.count
            chosen: list[list[Picked]] = []
            for key, members in ordered:
                if need <= 0:
                    break
                members.sort(key=lambda c: c.order_hint)
                if len(members) > need or len(members) > cfg.max_group_size:
                    continue
                chosen.append([Picked(si, c.question_id, c.external_id, c.version_id, c.group_key,
                                      section.points_per_question, modes[c.question_id]) for c in members])
                need -= len(members)
                used.update(c.question_id for c in members)
            if need > 0:
                raise SelectionError(
                    f"Không đủ câu hỏi cho phần “{section.title}” (thiếu {need}/{pool.count}).",
                    {"section": section.key, "pool": pi, "missing": need, "available": len(cands)})
            section_items += chosen
        if section.order == "shuffled":
            section_items.sort(key=lambda unit: hkey(seed, section.key, "order", unit[0].external_id))
        for unit in section_items:
            sel.items.extend(unit)
    sel.pool_fingerprint = fp.hexdigest()
    return sel


def _fixed_items(db: Session, section, si) -> list[Picked]:
    out = []
    for it in section.items:
        row = db.execute(select(Question.id, Question.external_id, Question.current_version_id, Question.group_key,
                                Question.scoring_mode, Question.is_served)
                         .join(QuestionBank, QuestionBank.id == Question.bank_id)
                         .where(QuestionBank.code == it.bank, Question.external_id == it.external_id,
                                QuestionBank.is_active.is_(True))).first()
        if not row or not row.current_version_id:
            raise SelectionError(f"Câu hỏi {it.external_id} không tồn tại trong ngân hàng {it.bank}.",
                                 {"external_id": it.external_id})
        if not row.is_served:
            raise SelectionError(f"Câu hỏi {it.external_id} đang bị tắt phục vụ.", {"external_id": it.external_id})
        out.append(Picked(si, row.id, row.external_id, row.current_version_id, row.group_key,
                          section.points_per_question, row.scoring_mode))
    return out
