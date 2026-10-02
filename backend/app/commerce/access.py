"""Practice access: FREE accounts practise a fixed, deterministic pool of questions per subject; PRO
accounts (an active plan_subscription) and admins practise the whole served bank.

The free pool of a subject is chosen among questions that are served to students anyway (never a
non-eligible question to reach the limit): units (a shared-passage group or a single question) are
ranked by sha256("free|<subject>|<unit>") and taken greedily while they fit in the limit, so passages
stay whole and the pool is identical for every free user and stable across requests. When questions
are added or withdrawn only the affected units move. The limit is the admin setting
practice.free_questions_per_subject.

Enforcement is server-side: every practice session for a FREE account is restricted to the union of
the free pools of the requested subjects (all practice subjects when none is selected), whatever the
request parameters (subjects, types, source = all/bookmarks/wrong/unseen, count).
"""
import hashlib
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Question, QuestionBank, Subject, User
from ..settings_store import get_setting
from . import service as commerce

_CACHE: dict = {}
_LOCK = threading.Lock()
CACHE_SECONDS = 60


def free_limit(db: Session) -> int:
    return int(get_setting(db, "practice").get("free_questions_per_subject") or 0)


def practice_access(db: Session, user: User | None) -> dict:
    """{"plan": FREE|PRO|ADMIN, "limit": int|None (None = whole bank), "pro_until": iso|None}"""
    if user is not None and user.role == "admin":
        return {"plan": "ADMIN", "limit": None, "pro_until": None}
    if user is not None:
        sub = commerce.active_subscription(db, user.id)
        if sub is not None:
            until = commerce.pro_until(db, user.id)
            return {"plan": "PRO", "limit": None, "pro_until": until.isoformat() if until else None}
    return {"plan": "FREE", "limit": free_limit(db), "pro_until": None}


def _served_units(db: Session, subject: str, require_auto: bool) -> dict[str, list[int]]:
    st = select(Question.id, Question.external_id, Question.group_key).join(
        QuestionBank, QuestionBank.id == Question.bank_id).where(
        Question.is_served.is_(True), Question.current_version_id.is_not(None), QuestionBank.is_active.is_(True),
        Question.subject_code == subject)
    st = st.where(Question.scoring_mode == "auto") if require_auto else \
        st.where(Question.scoring_mode.in_(["auto", "self_check"]))
    units: dict[str, list[int]] = {}
    for qid, ext, group in db.execute(st):
        units.setdefault(f"g:{group}" if group else f"q:{ext}", []).append(qid)
    return units


def free_pool(db: Session, subject: str, limit: int | None = None, require_auto: bool | None = None) -> list[int]:
    """Question ids of the subject's free pool (deterministic; at most `limit`)."""
    limit = free_limit(db) if limit is None else limit
    if require_auto is None:
        require_auto = not get_setting(db, "serving_policy")["practice_allow_self_check"]
    key = (subject, limit, require_auto)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    units = _served_units(db, subject, require_auto)
    ranked = sorted(units.items(), key=lambda kv: hashlib.sha256(f"free|{subject}|{kv[0]}".encode()).hexdigest())
    picked: list[int] = []
    for _, ids in ranked:
        if len(picked) >= limit:
            break
        if len(picked) + len(ids) <= limit:
            picked.extend(sorted(ids))
    with _LOCK:
        _CACHE[key] = (now + CACHE_SECONDS, picked)
    return picked


def clear_cache():
    with _LOCK:
        _CACHE.clear()


def practice_subjects(db: Session) -> list[str]:
    return list(db.scalars(select(Subject.code).where(Subject.is_active.is_(True), Subject.practice_enabled.is_(True))
                           .order_by(Subject.sort_order)))


def allowed_question_ids(db: Session, user: User | None, subjects: list[str]) -> set[int] | None:
    """None = no restriction (PRO/admin); otherwise the ids a FREE account may practise."""
    acc = practice_access(db, user)
    if acc["limit"] is None:
        return None
    out: set[int] = set()
    for s in subjects or practice_subjects(db):
        out.update(free_pool(db, s, acc["limit"]))
    return out
