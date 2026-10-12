"""Review Studio API (/api/review). Staff only: admin, editor, reviewer, approver (see review/service.PERMS)."""
import datetime as dt
import io
import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, Text, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session, lazyload

from .. import auth
from ..config import get_settings
from ..content.render import plain_text
from ..db import get_db
from ..exam.service import DISPLAY
from ..models import (Asset, ExamItem, Question, QuestionBank, QuestionReview, QuestionVersion, ReviewAssignment,
                      ReviewEvent, ReviewFinding, SourceCache, Subject, User)
from ..review import ai as AI
from ..review import content as C
from ..review import service as S
from ..review import sources as SRC
from ..review import validators as V
from ..sync.policy import APP_REASONS
from .common import err, page_params

router = APIRouter(prefix="/api/review", tags=["review"])
log = logging.getLogger(__name__)

QUEUE_LABELS = {
    "NEEDS_FORMULA_REVIEW": "Cần duyệt công thức", "NEEDS_ANSWER_LINKING": "Cần gắn đáp án",
    "NEEDS_VISUAL_REVIEW": "Cần duyệt hình", "SUBJECT_REVIEW": "Cần duyệt môn", "UNCLASSIFIED": "Chưa phân loại môn",
    "GENERAL_REVIEW": "Duyệt chung", "ANSWER_CONFLICT": "Đáp án mâu thuẫn", "INCOMPLETE": "Thiếu nội dung",
    "VALIDATOR": "Lỗi kiểm tra tự động", "UPSTREAM_CHANGED": "Nguồn đã thay đổi", "DRAFT": "Có bản nháp",
    "RETURNED": "Bị trả lại", "READY": "READY (đủ điều kiện)", "NOT_READY": "Chưa READY",
}
ROLE_LABELS = {"admin": "Quản trị", "editor": "Biên tập viên", "reviewer": "Người duyệt", "approver": "Người phê duyệt"}


def staff(user: User = Depends(auth.current_user)) -> User:
    if not S.can(user, "view"):
        raise HTTPException(403, detail={"code": "forbidden", "message": "Bạn không có quyền truy cập Review Studio."})
    return user


def need(user: User, perm: str):
    if not S.can(user, perm):
        raise err(403, "forbidden", "Vai trò của bạn không được thực hiện thao tác này.")


def iso(t):
    return t.isoformat() if t else None


def _media():
    return get_settings().media_root


def _q(db, qid) -> Question:
    q = db.get(Question, qid)
    if q is None:
        raise err(404, "not_found", "Không tìm thấy câu hỏi.")
    return q


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except S.ReviewError as ex:
        raise HTTPException(ex.status, detail={"code": ex.code, "message": str(ex), **(ex.data or {})}) from ex


# ------------------------------------------------------------------------------------------------
# meta
# ------------------------------------------------------------------------------------------------
@router.get("/meta")
def meta(db: Session = Depends(get_db), user: User = Depends(staff)):
    subjects = [{"code": s.code, "name": s.name} for s in db.scalars(select(Subject).order_by(Subject.sort_order))]
    reviewers = [{"id": u.id, "name": u.display_name, "email": u.email, "role": u.role} for u in db.scalars(
        select(User).where(User.role.in_(list(S.PERMS["view"])), User.is_active.is_(True)).order_by(User.display_name))]
    return {"me": {"id": user.id, "role": user.role, "role_label": ROLE_LABELS.get(user.role),
                   "perms": [p for p in S.PERMS if S.can(user, p)]},
            "subjects": subjects, "reviewers": reviewers, "queues": QUEUE_LABELS, "dimensions": C.DIM_LABELS,
            "validators": V.LABELS, "validator_version": V.VALIDATOR_VERSION, "ai": AI.status(),
            "types": C.QTYPES, "roles": ROLE_LABELS}


# ------------------------------------------------------------------------------------------------
# queue
# ------------------------------------------------------------------------------------------------
class Filters(BaseModel):
    queue: str | None = None
    subject: str | None = None
    document: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    ready: Literal["yes", "no"] | None = None
    answer: Literal["has", "none", "ok", "pending"] | None = None
    formula: Literal["ok", "na", "fail", "pending", "stale"] | None = None
    visual: Literal["ok", "na", "fail", "pending", "stale"] | None = None
    findings: Literal["critical", "any", "none"] | None = None
    assignee: str | None = None  # me | none | <user id>
    status: Literal["none", "draft", "reviewed", "approved", "returned"] | None = None
    modified_from: dt.date | None = None
    modified_to: dt.date | None = None
    q: str | None = None
    qtype: str | None = None
    served: bool | None = None
    assignment: int | None = None
    sort: Literal["priority", "oldest", "document", "subject", "cq_id", "updated"] = "priority"


R = QuestionReview


def _filtered(f: Filters, user: User):
    """Filtered selection over question_review only (indexed, narrow rows)."""
    st = select(R.question_id).where(R.active.is_(True))
    if f.queue:
        st = st.where(R.queues.contains([f.queue]))
    if f.ready:
        st = st.where(R.queues.contains(["READY" if f.ready == "yes" else "NOT_READY"]))
    if f.subject:
        st = st.where(R.subject_code == ("" if f.subject == "_none" else f.subject))
    if f.document:
        d = f.document.strip()
        st = st.where(R.doc_id == d) if d.startswith("doc_") else st.where(R.doc_path.ilike(f"%{d}%"))
    if f.page_from is not None:
        st = st.where(R.source_page >= f.page_from)
    if f.page_to is not None:
        st = st.where(R.source_page <= f.page_to)
    no_answer = select(ReviewFinding.id).where(ReviewFinding.question_id == R.question_id,
                                               ReviewFinding.code == "answer_missing",
                                               ReviewFinding.status != "fixed").exists()
    if f.answer == "has":
        st = st.where(~no_answer)
    elif f.answer == "none":
        st = st.where(no_answer)
    elif f.answer:
        st = st.where(R.dim_state["answer"].astext == ("ok" if f.answer == "ok" else "pending"))
    if f.formula:
        st = st.where(R.dim_state["formula"].astext == f.formula)
    if f.visual:
        st = st.where(R.dim_state["visual"].astext == f.visual)
    if f.findings == "critical":
        st = st.where(R.findings_critical > 0)
    elif f.findings == "any":
        st = st.where(R.findings_open > 0)
    elif f.findings == "none":
        st = st.where(R.findings_open == 0)
    if f.assignee == "me":
        st = st.where(R.assignee_id == user.id)
    elif f.assignee == "none":
        st = st.where(R.assignee_id.is_(None))
    elif f.assignee and f.assignee.isdigit():
        st = st.where(R.assignee_id == int(f.assignee))
    if f.assignment:
        st = st.where(R.assignment_id == f.assignment)
    if f.status:
        st = st.where(R.status == f.status)
    if f.modified_from:
        st = st.where(cast(R.updated_at, Date) >= f.modified_from)
    if f.modified_to:
        st = st.where(cast(R.updated_at, Date) <= f.modified_to)
    if f.qtype:
        st = st.where(R.qtype == f.qtype)
    if f.served is not None:
        st = st.where(R.served.is_(f.served))
    if f.q:
        qq = f.q.strip()
        if qq.startswith(("cq_", "qo_")):
            st = st.where(R.external_id.like(qq.replace("%", "") + "%")) if qq.startswith("cq_") else \
                st.where(R.question_id.in_(select(Question.id).where(
                    Question.provenance["representative_occurrence_id"].astext == qq)))
        elif qq.isdigit():
            st = st.where(R.question_id == int(qq))
        else:
            st = st.where(R.search_text.contains(qq.lower(), autoescape=True))
    return st


EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
# sort keys: (expression, descending?) — the last key is unique so keyset navigation is exact
SORTS = {
    "priority": [(R.priority, True), (R.question_id, False)],
    "oldest": [(R.question_id, False)],
    "document": [(R.doc_path, False), (func.coalesce(R.source_page, 0), False), (R.source_number, False),
                 (R.question_id, False)],
    "subject": [(R.subject_code, False), (R.external_id, False)],
    "cq_id": [(R.external_id, False)],
    "updated": [(func.coalesce(R.updated_at, EPOCH), True), (R.question_id, False)],
}


def _order(f: Filters):
    return [e.desc() if d else e.asc() for e, d in SORTS[f.sort]]


def _after(f: Filters, key: list, before: bool = False):
    """Keyset condition: rows strictly after (or before) `key` in the sort order."""
    cols = SORTS[f.sort]
    conds = []
    for i, (e, desc) in enumerate(cols):
        later = (e < key[i]) if desc != before else (e > key[i])
        conds.append(and_(*[c == key[j] for j, (c, _) in enumerate(cols[:i])], later))
    return or_(*conds)


def _key_of(db, f: Filters, qid: int) -> list | None:
    row = db.execute(select(*[e for e, _ in SORTS[f.sort]]).where(R.question_id == qid)).first()
    if row is None:
        return None
    return [v.isoformat() if isinstance(v, dt.datetime) else v for v in row]


def _key_vals(f: Filters, key: list) -> list:
    out = []
    for (e, _), v in zip(SORTS[f.sort], key):
        out.append(dt.datetime.fromisoformat(v) if isinstance(v, str) and f.sort == "updated" and e is not R.question_id
                   else v)
    return out


@router.get("/queue")
def queue(f: Filters = Depends(), page: int = 1, size: int = 50, db: Session = Depends(get_db),
          user: User = Depends(staff)):
    off, lim = page_params(page, size, 200)
    ids_st = _filtered(f, user)
    total = db.scalar(select(func.count()).select_from(ids_st.subquery()))
    page_ids = list(db.scalars(ids_st.order_by(*_order(f)).offset(off).limit(lim)))
    qs_ = {q.id: q for q in db.scalars(select(Question).options(lazyload(Question.current_version))
                                       .where(Question.id.in_(page_ids)))}
    rs = {r.question_id: r for r in db.scalars(select(R).where(R.question_id.in_(page_ids)))}
    vids = {rs[i].draft_version_id or qs_[i].current_version_id for i in page_ids if i in qs_ and i in rs}
    stems = dict(db.execute(select(QuestionVersion.id, QuestionVersion.content["stem"])
                            .where(QuestionVersion.id.in_([v for v in vids if v]))).all())
    names = dict(db.execute(select(User.id, User.display_name).where(
        User.id.in_({r.assignee_id for r in rs.values() if r.assignee_id}))).all())
    items = []
    for i, qid in enumerate(page_ids):
        q, r = qs_.get(qid), rs.get(qid)
        if q is None or r is None:
            continue
        items.append({
            "rank": off + i, "id": q.id, "external_id": q.external_id, "subject": q.subject_code,
            "type": q.question_type, "state": q.editorial_state, "served": q.is_served, "gate": q.review_gate,
            "queues": r.queues, "status": r.status, "priority": r.priority, "dims": r.dim_state,
            "findings_open": r.findings_open, "findings_critical": r.findings_critical,
            "assignee": {"id": r.assignee_id, "name": names.get(r.assignee_id)} if r.assignee_id else None,
            "document": r.doc_path.split("▸")[-1].strip()[-80:], "document_id": r.doc_id or None,
            "source_page": r.source_page, "source_number": r.source_number or None,
            "updated_at": iso(r.updated_at), "draft": bool(r.draft_version_id),
            "preview": plain_text(stems.get(r.draft_version_id or q.current_version_id) or [])[:200]})
    return {"total": total, "items": items}


_cache: dict = {}


def cached(key: str, ttl: float, fn):
    """Small in-process cache for bank-wide aggregates (single API worker; ~1 minute staleness is fine)."""
    import time
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def _queue_counts(db) -> dict:
    rows = db.execute(text("SELECT x, count(*) FROM question_review, unnest(queues) x WHERE active GROUP BY x")).all()
    pending = db.scalar(select(func.count()).select_from(R).where(R.dirty.is_(True)))
    return {"queues": dict(rows), "pending_refresh": pending}


@router.get("/queue/counts")
def queue_counts(db: Session = Depends(get_db), user: User = Depends(staff)):
    c = cached("counts", 60, lambda: _queue_counts(db))
    mine = db.execute(select(R.status, func.count()).where(R.assignee_id == user.id).group_by(R.status)).all()
    return {**c, "mine": dict(mine)}


@router.get("/queue/neighbors")
def neighbors(id: int, key: str | None = None, f: Filters = Depends(), db: Session = Depends(get_db),
              user: User = Depends(staff)):
    """Previous/next question of the current filter (Save & Next), by keyset on the sort order. `key` is the sort
    key the client saw for `id` (JSON); with it, "next" stays correct after the question left the filter or its
    priority changed."""
    import json as _json
    k = _json.loads(key) if key else _key_of(db, f, id)
    if k is None:
        return {"prev": None, "next": None, "key": None, "total": None, "in_filter": False}
    vals = _key_vals(f, k)
    ids_st = _filtered(f, user)
    nxt = db.scalar(ids_st.where(_after(f, vals)).order_by(*_order(f)).limit(1))
    rev = [e.asc() if d else e.desc() for e, d in SORTS[f.sort]]
    prv = db.scalar(ids_st.where(_after(f, vals, before=True)).order_by(*rev).limit(1))
    in_filter = db.scalar(select(func.count()).select_from(ids_st.where(R.question_id == id).subquery())) > 0
    return {"prev": prv, "next": nxt, "key": k, "in_filter": in_filter,
            "next_key": _key_of(db, f, nxt) if nxt else None, "prev_key": _key_of(db, f, prv) if prv else None}


class AssignIn(BaseModel):
    reviewer_id: int | None = None  # null = unassign
    ids: list[int] | None = Field(default=None, max_length=5000)
    filters: Filters | None = None  # assign the first `limit` questions of a filter instead of explicit ids
    limit: int | None = Field(default=None, ge=1, le=5000)
    only_unassigned: bool = True
    label: str | None = Field(default=None, max_length=200)


@router.post("/assign")
def assign(body: AssignIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "assign")
    if body.ids:
        ids = body.ids
    elif body.filters is not None and body.limit:
        st = _filtered(body.filters, user)
        if body.only_unassigned:
            st = st.where(R.assignee_id.is_(None))
        ids = list(db.scalars(st.order_by(*_order(body.filters)).limit(body.limit)))
    else:
        raise err(422, "invalid", "Chọn câu hỏi hoặc bộ lọc và số lượng.")
    if not ids:
        return {"assigned": 0}
    assignment_id = None
    if body.reviewer_id is not None:
        fl = body.filters
        a = ReviewAssignment(label=body.label or f"{len(ids)} câu", reviewer_id=body.reviewer_id,
                             queue=fl.queue if fl else None, subject_code=fl.subject if fl else None,
                             filter=fl.model_dump(mode="json", exclude_none=True) if fl else {"ids": len(ids)},
                             total=len(ids), status="active", created_by=user.id)
        db.add(a)
        db.flush()
        assignment_id = a.id
    n = _run(S.assign, db, user, ids, body.reviewer_id, assignment_id)
    db.commit()
    return {"assigned": n, "assignment_id": assignment_id}


@router.get("/assignments")
def assignments(db: Session = Depends(get_db), user: User = Depends(staff)):
    rows = db.execute(select(ReviewAssignment, User.display_name).join(User, User.id == ReviewAssignment.reviewer_id)
                      .order_by(ReviewAssignment.id.desc()).limit(200)).all()
    prog = dict(db.execute(select(QuestionReview.assignment_id, func.count()).where(
        QuestionReview.status.in_(["reviewed", "approved"])).group_by(QuestionReview.assignment_id)).all())
    remaining = dict(db.execute(select(QuestionReview.assignment_id, func.count()).group_by(
        QuestionReview.assignment_id)).all())
    return {"items": [{"id": a.id, "label": a.label, "reviewer": {"id": a.reviewer_id, "name": n}, "queue": a.queue,
                       "subject": a.subject_code, "total": a.total, "done": prog.get(a.id, 0),
                       "still_assigned": remaining.get(a.id, 0), "status": a.status, "created_at": iso(a.created_at)}
                      for a, n in rows]}


# ------------------------------------------------------------------------------------------------
# question
# ------------------------------------------------------------------------------------------------
def preview_payload(v: QuestionVersion | None) -> dict | None:
    """Exactly the student payload of exam/service.item_payload (no shuffling), answer revealed."""
    if v is None:
        return None
    c = v.content
    opts = [{"key": o["label"], "display": DISPLAY[i] if i < len(DISPLAY) else o["label"], "content": o["content"]}
            for i, o in enumerate(c.get("options") or [])]
    return {"type": c["type"], "language": c.get("language"), "input": c["input"], "group": c.get("group"),
            "stem": c["stem"], "options": opts, "answer": v.answer, "solution": c.get("solution"),
            "explanation": c.get("explanation")}


def _version(v: QuestionVersion | None, users: dict) -> dict | None:
    if v is None:
        return None
    return {"id": v.id, "version_no": v.version_no, "origin": v.origin, "content": v.content, "answer": v.answer,
            "content_hash": v.content_hash, "created_at": iso(v.created_at), "note": v.note,
            "created_by": users.get(v.created_by)}


@router.get("/questions/{qid}")
def detail(qid: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    q = _q(db, qid)
    r = S.get_review(db, q)
    if r.dirty or r.validator_version != V.VALIDATOR_VERSION:
        S.refresh(db, q, r, media_root=_media())
        db.commit()
    v = S.working_version(db, q, r)
    cur = db.get(QuestionVersion, q.current_version_id) if q.current_version_id else None
    view = S.dim_view(q, r, v)
    versions = db.scalars(select(QuestionVersion).where(QuestionVersion.question_id == q.id)
                          .order_by(QuestionVersion.version_no.desc())).all()
    events = db.scalars(select(ReviewEvent).where(ReviewEvent.question_id == q.id)
                        .order_by(ReviewEvent.id.desc()).limit(200)).all()
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.question_id == q.id)
                          .order_by(ReviewFinding.status, ReviewFinding.severity, ReviewFinding.code)).all()
    uids = {e.actor_id for e in events} | {x.created_by for x in versions} | \
        {(r.dims or {}).get(d, {}).get("by") for d in C.DIMENSIONS} | {r.assignee_id, r.approved_by, r.reviewed_by}
    users = dict(db.execute(select(User.id, User.display_name).where(User.id.in_([u for u in uids if u]))).all())
    usage = db.scalar(select(func.count()).select_from(ExamItem).where(ExamItem.question_id == q.id))
    p = q.provenance or {}
    dims = {}
    for d in C.DIMENSIONS:
        rec = (r.dims or {}).get(d) or {}
        dims[d] = {"state": view["status"][d], "required": view["required"][d], "confirmed": rec.get("s"),
                   "by": users.get(rec.get("by")), "at": rec.get("at"), "note": rec.get("note"),
                   "label": C.DIM_LABELS[d]}
    return {
        "id": q.id, "external_id": q.external_id, "bank_id": q.bank_id, "type": q.question_type,
        "subject": q.subject_code, "subject_source": q.subject_source, "subject_override": q.subject_override,
        "upstream_subject": q.upstream_effective_subject, "classification_review": q.classification_review,
        "classification_confidence": q.classification_confidence, "topic": q.topic, "subtopic": q.subtopic,
        "group_key": q.group_key, "editorial_state": q.editorial_state, "editorial_states": q.editorial_states,
        "state_notes": q.state_notes, "review_flags": q.review_flags, "content_flags": q.content_flags,
        "content_flag_labels": {k: APP_REASONS.get(k, k) for k in (q.content_flags or [])},
        "policy_eligible": q.policy_eligible, "policy_reasons": q.policy_reasons, "served": q.is_served,
        "admin_override": q.admin_override, "gate": q.review_gate, "ready": S.effective_ready(q),
        "answer_source_type": q.answer_source_type, "used_in_attempts": usage,
        "provenance": p,
        "review": {"status": r.status, "queues": r.queues, "priority": r.priority, "content_hash": r.content_hash,
                   "validator_version": r.validator_version, "validated_at": iso(r.validated_at),
                   "upstream_changed": r.upstream_changed, "draft_version_id": r.draft_version_id,
                   "assignee": {"id": r.assignee_id, "name": users.get(r.assignee_id)} if r.assignee_id else None,
                   "reviewed_by": users.get(r.reviewed_by), "reviewed_at": iso(r.reviewed_at),
                   "approved_by": users.get(r.approved_by), "approved_at": iso(r.approved_at),
                   "verification_method": r.verification_method, "source_page": r.source_page,
                   "complete": view["complete"]},
        "dims": dims,
        "working": _version(v, users), "current_version_id": q.current_version_id,
        "upstream_version_id": q.upstream_version_id,
        "preview": preview_payload(v), "published_preview": preview_payload(cur) if v is not cur else None,
        "findings": [{"id": f.id, "code": f.code, "label": V.LABELS.get(f.code, f.code), "severity": f.severity,
                      "dimension": f.dimension, "detail": f.detail, "status": f.status, "reason": f.reason,
                      "resolved_by": users.get(f.resolved_by), "resolved_at": iso(f.resolved_at)} for f in findings],
        "versions": [{"id": x.id, "version_no": x.version_no, "origin": x.origin, "created_at": iso(x.created_at),
                      "created_by": users.get(x.created_by), "note": x.note, "content_hash": x.content_hash,
                      "is_current": x.id == q.current_version_id, "is_draft": x.id == r.draft_version_id,
                      "is_upstream": x.id == q.upstream_version_id} for x in versions],
        "history": [{"id": e.id, "action": e.action, "changes": e.changes, "note": e.note, "source_ref": e.source_ref,
                     "actor": users.get(e.actor_id), "at": iso(e.created_at), "from_version_id": e.from_version_id,
                     "to_version_id": e.to_version_id} for e in events],
    }


@router.get("/questions/{qid}/versions/{vid}")
def version(qid: int, vid: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    v = db.get(QuestionVersion, vid)
    if v is None or v.question_id != qid:
        raise err(404, "not_found", "Không tìm thấy phiên bản.")
    return {**_version(v, {}), "preview": preview_payload(v)}


class EditIn(BaseModel):
    type: str | None = None
    stem: list | None = None
    options: list | None = None
    answer: dict | None = None
    clear_answer: bool = False
    solution: list | None = None
    explanation: list | None = None
    group: dict | None = None
    detach_group: bool = False
    apply_group: bool = False  # also update the shared passage of the other questions of the group
    note: str | None = Field(default=None, max_length=2000)
    source_ref: dict | None = None
    tex_checks: dict | None = None  # {tex: {"ok": bool, "error": str}} from the editor's KaTeX


@router.put("/questions/{qid}")
def save(qid: int, body: EditIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "edit")
    q = _q(db, qid)
    edit = body.model_dump(exclude_unset=True, exclude={"note", "source_ref", "tex_checks", "apply_group",
                                                         "clear_answer", "detach_group"})
    if body.clear_answer:
        edit["answer"] = None
    if body.detach_group:
        edit["group"] = None
    for k in ("stem", "options", "solution", "explanation", "group", "type"):
        if k in edit and edit[k] is None and k not in ("solution", "explanation", "group"):
            edit.pop(k)
    res = _run(S.save_edit, db, q, user, edit, body.note, body.source_ref, _media(), body.tex_checks,
               body.apply_group)
    db.commit()
    return res


class SubjectIn(BaseModel):
    subject_code: str | None = None
    topic: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/questions/{qid}/subject")
def subject(qid: int, body: SubjectIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "edit")
    q = _q(db, qid)
    res = _run(S.set_subject, db, q, user, body.subject_code, body.note, _media())
    if "topic" in body.model_fields_set:
        res.update(S.set_topic(db, q, user, body.topic, body.note))
    db.commit()
    return res


class ConfirmIn(BaseModel):
    dim: str
    state: Literal["ok", "na", "fail"] | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.post("/questions/{qid}/confirm")
def confirm(qid: int, body: ConfirmIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "confirm")
    res = _run(S.confirm, db, _q(db, qid), user, body.dim, body.state, body.note, _media())
    db.commit()
    return res


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


@router.post("/questions/{qid}/approve")
def approve(qid: int, body: NoteIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "approve")
    res = _run(S.approve, db, _q(db, qid), user, body.note, _media())
    db.commit()
    return res


class StatusIn(BaseModel):
    action: Literal["return", "reopen", "discard_draft"]
    note: str | None = Field(default=None, max_length=2000)


@router.post("/questions/{qid}/status")
def status(qid: int, body: StatusIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "edit" if body.action == "discard_draft" else ("approve" if body.action == "reopen" else "confirm"))
    res = _run(S.set_status, db, _q(db, qid), user, body.action, body.note, _media())
    db.commit()
    return res


class RestoreIn(BaseModel):
    version_id: int
    note: str | None = Field(default=None, max_length=2000)


@router.post("/questions/{qid}/restore")
def restore(qid: int, body: RestoreIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "edit")
    res = _run(S.restore, db, _q(db, qid), user, body.version_id, body.note, _media())
    db.commit()
    return res


class FindingIn(BaseModel):
    action: Literal["accept", "dismiss", "reopen"]
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/questions/{qid}/findings/{fid}")
def finding(qid: int, fid: int, body: FindingIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "confirm" if body.action != "accept" else "view")
    res = _run(S.resolve_finding, db, _q(db, qid), user, fid, body.action, body.reason, _media())
    db.commit()
    return res


@router.post("/questions/{qid}/revalidate")
def revalidate(qid: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    q = _q(db, qid)
    S.refresh(db, q, S.get_review(db, q), media_root=_media(), force=True)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# source documents
# ------------------------------------------------------------------------------------------------
def _cache_row(db, doc_id) -> SourceCache | None:
    return db.get(SourceCache, doc_id)


@router.get("/questions/{qid}/source")
def question_source(qid: int, db: Session = Depends(get_db), user: User = Depends(staff)):
    q = _q(db, qid)
    p = q.provenance or {}
    store = SRC.default_store()
    occs = store.up.occurrences(q.external_id)
    rep = p.get("representative_occurrence_id")
    occs.sort(key=lambda o: (o["occurrence_id"] != rep, o["document_id"] or ""))
    docs = []
    for doc_id in dict.fromkeys(o["document_id"] for o in occs if o["document_id"]):
        d = store.up.document(doc_id) or {}
        row = _cache_row(db, doc_id)
        docs.append({"document_id": doc_id, "path": d.get("representative_path"), "file_kind": d.get("file_kind"),
                     "extraction_method": d.get("extraction_method"), "document_role": d.get("document_role"),
                     "blob_sha256": d.get("blob_sha256"), "exam_name": d.get("exam_name"), "year": d.get("year"),
                     "cache": SRC.cache_payload(row) if row else None})
    if docs and (docs[0]["cache"] is None):
        docs[0]["cache"] = SRC.request(db, docs[0]["document_id"], user.id)  # prepare the main source on first view
        db.commit()
    return {"available": bool(docs), "status": "ok" if docs else "SOURCE_UNAVAILABLE",
            "representative_occurrence_id": rep, "occurrences": occs, "documents": docs,
            "provenance": {k: p.get(k) for k in ("document_id", "document_path", "extraction_method",
                                                  "source_question_number", "section", "exam_name", "year",
                                                  "representative_occurrence_id")}}


def _doc(doc_id: str):
    if not SRC.DOC_RE.match(doc_id):
        raise err(404, "not_found", "Tài liệu không hợp lệ.")


@router.post("/sources/{doc_id}/prepare")
def prepare(doc_id: str, db: Session = Depends(get_db), user: User = Depends(staff)):
    _doc(doc_id)
    res = SRC.request(db, doc_id, user.id)
    db.commit()
    return res


@router.get("/sources/{doc_id}")
def source_status(doc_id: str, db: Session = Depends(get_db), user: User = Depends(staff)):
    _doc(doc_id)
    row = _cache_row(db, doc_id)
    return SRC.cache_payload(row) if row else {"document_id": doc_id, "status": "none"}


def _ready(db, doc_id) -> SourceCache:
    _doc(doc_id)
    row = _cache_row(db, doc_id)
    if row is None or row.status != "ready" or not row.blob_sha256:
        raise err(409, "source_not_ready", "Tài liệu gốc chưa sẵn sàng.")
    return row


@router.get("/sources/{doc_id}/page/{page}")
def source_page(doc_id: str, page: int, dpi: int = 110, db: Session = Depends(get_db), user: User = Depends(staff)):
    row = _ready(db, doc_id)
    if page < 1 or (row.pages and page > row.pages):
        raise err(404, "not_found", "Trang không tồn tại.")
    db.close()  # rendering can take a moment; do not hold a pooled connection
    out = SRC.default_store().render(row.blob_sha256, page, dpi)
    if out is None:
        raise err(500, "render_failed", "Không hiển thị được trang này.")
    return FileResponse(out, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/sources/{doc_id}/locate")
def locate(doc_id: str, q: str = Query(max_length=2000), db: Session = Depends(get_db), user: User = Depends(staff)):
    row = _ready(db, doc_id)
    db.close()
    return {"pages": SRC.default_store().locate(row.blob_sha256, q)}


# ------------------------------------------------------------------------------------------------
# images
# ------------------------------------------------------------------------------------------------
def _store_image(db, data: bytes, source_ref: str | None) -> dict:
    from PIL import Image, ImageOps

    from ..sync.media import MediaStore
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as ex:  # noqa: BLE001
        raise err(422, "invalid_image", "Tệp không phải là ảnh hợp lệ.") from ex
    if im.width * im.height > 40_000_000:
        raise err(422, "invalid_image", "Ảnh quá lớn.")
    im = ImageOps.exif_transpose(im)
    fmt_jpeg = im.mode in ("RGB", "L") and (im.format == "JPEG")
    buf = io.BytesIO()
    if fmt_jpeg:
        im.save(buf, "JPEG", quality=90)
        ext = "jpg"
    else:
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA")
        im.save(buf, "PNG", optimize=True)
        ext = "png"
    sf = MediaStore(_media()).put_bytes(buf.getvalue(), ext)
    if db.get(Asset, sf.sha256) is None:
        db.add(Asset(sha256=sf.sha256, ext=sf.ext, mime="image/jpeg" if ext == "jpg" else "image/png",
                     bytes=sf.bytes, width=im.width, height=im.height, source_ref=(source_ref or "review:upload")[:80]))
        db.flush()
    return {"src": f"{sf.sha256[:2]}/{sf.sha256}.{sf.ext}", "w": im.width, "h": im.height}


@router.post("/assets")
async def upload(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "edit")
    data = await file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise err(413, "too_large", "Ảnh tối đa 8 MB.")
    res = _store_image(db, data, f"review:upload:{user.id}")
    db.commit()
    return res


class CropIn(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


@router.post("/questions/{qid}/crop")
def crop(qid: int, body: CropIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """Cut a region of a source page into an image asset (provenance kept in the review history)."""
    need(user, "edit")
    q = _q(db, qid)
    row = _ready(db, body.document_id)
    png = SRC.default_store().render(row.blob_sha256, body.page, 220)
    if png is None:
        raise err(500, "render_failed", "Không hiển thị được trang này.")
    from PIL import Image
    with Image.open(png) as im:
        W, H = im.size
        box = (int(body.x * W), int(body.y * H), int(min(1, body.x + body.w) * W), int(min(1, body.y + body.h) * H))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            raise err(422, "invalid", "Vùng cắt quá nhỏ.")
        buf = io.BytesIO()
        im.crop(box).save(buf, "PNG")
    res = _store_image(db, buf.getvalue(), f"src:{body.document_id}:p{body.page}")
    ref = {"document_id": body.document_id, "page": body.page, "crop": [body.x, body.y, body.w, body.h],
           "dpi": 220, "blob_sha256": row.blob_sha256, "asset": res["src"]}
    S.event(db, q, user, "asset_from_source", [{"field": "asset", "old": None, "new": res["src"]}],
            None, ref)
    db.commit()
    return {**res, "source_ref": ref}


# ------------------------------------------------------------------------------------------------
# AI assist (on demand, one question)
# ------------------------------------------------------------------------------------------------
class AIIn(BaseModel):
    action: str
    document_id: str | None = None
    page: int | None = None


@router.post("/questions/{qid}/ai")
def ai_assist(qid: int, body: AIIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    need(user, "ai")
    from ..auth import rate_limit
    q = _q(db, qid)
    if AI.provider() is None:
        raise err(503, "ai_not_configured", "Trợ lý AI chưa được cấu hình (HSA_AI_PROVIDER / HSA_AI_API_KEY).")
    rate_limit(db, f"ai:{user.id}", get_settings().ai_max_per_user_hour, 3600)
    r = S.get_review(db, q)
    v = S.working_version(db, q, r)
    image = None
    if body.document_id and body.page:
        row = _cache_row(db, body.document_id)
        if row and row.status == "ready":
            p = SRC.default_store().render(row.blob_sha256, body.page, 160)
            image = p.read_bytes() if p else None
    try:
        out = AI.run(body.action, v.content, v.answer, q.subject_code, image)
    except AI.AIError as ex:
        raise HTTPException(ex.status, detail={"code": ex.code, "message": str(ex)}) from ex
    S.event(db, q, user, "ai_suggestion", [], out[:8000],
            {"action": body.action, "document_id": body.document_id, "page": body.page, "applied": False})
    db.commit()
    return {"action": body.action, "suggestion": out, "note": "Gợi ý của AI — chỉ áp dụng khi bạn đã kiểm tra."}


# ------------------------------------------------------------------------------------------------
# statistics & personal dashboard
# ------------------------------------------------------------------------------------------------
def _bank_stats(db) -> dict:
    row = db.execute(text("""
        SELECT count(*),
               count(*) FILTER (WHERE q.editorial_state = 'READY_TO_SERVE'),
               count(*) FILTER (WHERE q.review_gate = 'ready'
                                OR (q.editorial_state = 'READY_TO_SERVE' AND q.review_gate IS NULL)),
               count(*) FILTER (WHERE q.review_gate = 'ready' AND q.editorial_state <> 'READY_TO_SERVE'),
               count(*) FILTER (WHERE q.review_gate = 'blocked'),
               count(*) FILTER (WHERE q.is_served)
        FROM question q JOIN question_bank b ON b.id = q.bank_id
        WHERE b.is_active AND NOT q.removed_upstream""")).one()
    findings = dict(db.execute(select(ReviewFinding.code, func.count()).where(ReviewFinding.status == "open")
                               .group_by(ReviewFinding.code)).all())
    return {"total": row[0], "upstream_ready": row[1], "ready": row[2], "ready_gained": row[3], "blocked": row[4],
            "serving": row[5], "not_ready": row[0] - row[2], "findings": findings, **_queue_counts(db)}


@router.get("/stats")
def stats(db: Session = Depends(get_db), user: User = Depends(staff)):
    now = dt.datetime.now(dt.timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - dt.timedelta(days=7)
    bank = cached("bank_stats", 60, lambda: _bank_stats(db))
    reviewed_actions = ("confirm", "approve", "edit")
    per_day = db.execute(select(cast(ReviewEvent.created_at, Date), func.count(func.distinct(ReviewEvent.question_id)))
                         .where(ReviewEvent.created_at >= now - dt.timedelta(days=30),
                                ReviewEvent.action.in_(reviewed_actions))
                         .group_by(cast(ReviewEvent.created_at, Date)).order_by(cast(ReviewEvent.created_at, Date))).all()
    by_user = db.execute(select(User.display_name, func.count(func.distinct(ReviewEvent.question_id)))
                         .join(User, User.id == ReviewEvent.actor_id)
                         .where(ReviewEvent.created_at >= week, ReviewEvent.action.in_(reviewed_actions))
                         .group_by(User.display_name)).all()
    approved_week = db.scalar(select(func.count(func.distinct(ReviewEvent.question_id))).where(
        ReviewEvent.action == "approve", ReviewEvent.created_at >= week))
    reviewed_today = db.scalar(select(func.count(func.distinct(ReviewEvent.question_id))).where(
        ReviewEvent.created_at >= today, ReviewEvent.action.in_(reviewed_actions)))
    reviewed_7 = db.scalar(select(func.count(func.distinct(ReviewEvent.question_id))).where(
        ReviewEvent.created_at >= week, ReviewEvent.action.in_(reviewed_actions)))
    return {**bank, "approved_week": approved_week, "reviewed_today": reviewed_today, "reviewed_7d": reviewed_7,
            "avg_per_day_7d": round(reviewed_7 / 7, 1), "per_day": [{"day": d.isoformat(), "n": n} for d, n in per_day],
            "by_reviewer_7d": [{"name": n, "questions": c} for n, c in by_user], "cached_for_s": 60}


@router.get("/me")
def me(db: Session = Depends(get_db), user: User = Depends(staff)):
    today = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    mine = dict(db.execute(select(QuestionReview.status, func.count()).where(QuestionReview.assignee_id == user.id)
                           .group_by(QuestionReview.status)).all())
    assigned = sum(mine.values())
    done_today = db.scalar(select(func.count(func.distinct(ReviewEvent.question_id))).where(
        ReviewEvent.actor_id == user.id, ReviewEvent.created_at >= today,
        ReviewEvent.action.in_(("confirm", "approve", "edit"))))
    return {"assigned": assigned, "in_progress": mine.get("draft", 0),
            "reviewed": mine.get("reviewed", 0) + mine.get("approved", 0), "returned": mine.get("returned", 0),
            "remaining": mine.get("none", 0) + mine.get("draft", 0) + mine.get("returned", 0),
            "reviewed_today": done_today}
