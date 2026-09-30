"""Admin / editorial API. Never writes to the upstream bank: overrides and corrections are app-side."""
import collections
import datetime as dt
import logging
import os
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Date, Text, cast, func, or_, select
from sqlalchemy.orm import Session

from .. import auth
from ..admin_ops import audit, export_corrections
from ..commerce import service as commerce
from ..config import get_settings
from ..db import SessionLocal, get_db
from ..exam.blueprint import parse_config
from ..exam.selection import count_available
from ..models import (AuditLog, Entitlement, ExamBlueprint, ExamItem, ExamSession, PaymentOrder, PaymentTransaction,
                      Product, Question, QuestionBank, QuestionCorrection, QuestionReport, QuestionVersion,
                      SourceDocument, Subject, SubjectAlias, SyncRun, User)
from ..settings_store import get_setting, set_setting
from ..sync.importer import recompute_policy
from ..sync.policy import APP_REASONS, EDITORIAL_STATES, effective_served
from .common import err, page_params, user_public

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(auth.require_admin)])
log = logging.getLogger(__name__)


def iso(t):
    return t.isoformat() if t else None


# ------------------------------------------------------------------------------------------------
# overview & statistics
# ------------------------------------------------------------------------------------------------
@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    now = dt.datetime.now(dt.timezone.utc)
    day = now - dt.timedelta(days=1)
    q_state = dict(db.execute(select(Question.editorial_state, func.count()).group_by(Question.editorial_state)).all())
    served_by_subject = dict(db.execute(select(Question.subject_code, func.count()).where(Question.is_served.is_(True))
                                        .group_by(Question.subject_code)).all())
    revenue = db.scalar(select(func.coalesce(func.sum(PaymentOrder.amount_vnd), 0)).where(PaymentOrder.status == "paid"))
    revenue_30 = db.scalar(select(func.coalesce(func.sum(PaymentOrder.amount_vnd), 0)).where(
        PaymentOrder.status == "paid", PaymentOrder.paid_at >= now - dt.timedelta(days=30)))
    last = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    per_day = db.execute(select(cast(ExamSession.created_at, Date), ExamSession.mode, func.count())
                         .where(ExamSession.created_at >= now - dt.timedelta(days=30))
                         .group_by(cast(ExamSession.created_at, Date), ExamSession.mode)
                         .order_by(cast(ExamSession.created_at, Date))).all()
    return {
        "users": {"total": db.scalar(select(func.count()).select_from(User)),
                  "new_24h": db.scalar(select(func.count()).where(User.created_at >= day)),
                  "active_24h": db.scalar(select(func.count(func.distinct(ExamSession.user_id))).where(
                      ExamSession.created_at >= day))},
        "sessions": {"total": db.scalar(select(func.count()).select_from(ExamSession)),
                     "submitted": db.scalar(select(func.count()).where(ExamSession.status == "submitted")),
                     "in_progress": db.scalar(select(func.count()).where(ExamSession.status == "in_progress")),
                     "last_24h": db.scalar(select(func.count()).where(ExamSession.created_at >= day)),
                     "per_day": [{"day": d.isoformat(), "mode": m, "n": n} for d, m, n in per_day]},
        "questions": {"total": sum(q_state.values()), "by_state": q_state,
                      "served": sum(served_by_subject.values()), "served_by_subject": served_by_subject,
                      "overridden": db.scalar(select(func.count()).where(Question.admin_override.is_not(None)))},
        "reports": {"open": db.scalar(select(func.count()).where(QuestionReport.status == "open"))},
        "payments": {"revenue_vnd": revenue, "revenue_30d_vnd": revenue_30,
                     "paid_orders": db.scalar(select(func.count()).where(PaymentOrder.status == "paid")),
                     "pending_orders": db.scalar(select(func.count()).where(PaymentOrder.status == "pending")),
                     "unmatched_transactions": db.scalar(select(func.count()).where(
                         PaymentTransaction.status.in_(["unmatched", "underpaid", "late"])))},
        "last_sync": _run(last) if last else None,
    }


@router.get("/stats/exams")
def exam_stats(db: Session = Depends(get_db)):
    rows = db.execute(select(ExamBlueprint.id, ExamBlueprint.name, func.count(ExamSession.id),
                             func.avg(ExamSession.score / func.nullif(ExamSession.max_score, 0)))
                      .outerjoin(ExamSession, (ExamSession.blueprint_id == ExamBlueprint.id)
                                 & (ExamSession.status == "submitted"))
                      .group_by(ExamBlueprint.id, ExamBlueprint.name).order_by(ExamBlueprint.sort_order)).all()
    practice = db.execute(select(func.count(), func.avg(ExamSession.score / func.nullif(ExamSession.max_score, 0)))
                          .where(ExamSession.mode == "practice", ExamSession.status == "submitted")).first()
    return {"blueprints": [{"id": i, "name": n, "submitted": c, "avg_ratio": float(a) if a is not None else None}
                           for i, n, c, a in rows],
            "practice": {"submitted": practice[0], "avg_ratio": float(practice[1]) if practice[1] is not None else None}}


# ------------------------------------------------------------------------------------------------
# questions
# ------------------------------------------------------------------------------------------------
@router.get("/questions")
def list_questions(q: str | None = None, subject: str | None = None, state: str | None = None,
                   served: bool | None = None, qtype: str | None = None, bank: str | None = None,
                   reason: str | None = None, reported: bool | None = None, override: bool | None = None,
                   source_subject: str | None = None, inferred_subject: str | None = None,
                   subject_source: str | None = None, has_answer: bool | None = None,
                   has_formula: bool | None = None, has_image: bool | None = None, eligible: bool | None = None,
                   scoring_mode: str | None = None,
                   page: int = 1, size: int = 25, db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 100)
    st = select(Question, QuestionBank.code).join(QuestionBank, QuestionBank.id == Question.bank_id)
    if q:
        qq = q.strip()
        if qq.startswith(("cq_", "qo_", "doc_")) or qq.isalnum() and len(qq) >= 6 and not qq.isdigit():
            st = st.where(or_(Question.external_id == qq, Question.external_id.ilike(qq + "%"),
                              Question.provenance["representative_occurrence_id"].astext == qq,
                              Question.provenance["document_id"].astext == qq))
        elif qq.isdigit():
            st = st.where(Question.id == int(qq))
        else:
            sub = select(QuestionVersion.id).where(
                QuestionVersion.id == Question.current_version_id,
                cast(QuestionVersion.content["stem"], Text).ilike(f"%{qq}%"))
            st = st.where(sub.exists())
    if subject:
        st = st.where(Question.subject_code == subject) if subject != "_none" else st.where(Question.subject_code.is_(None))
    if state:
        st = st.where(Question.editorial_state == state)
    if served is not None:
        st = st.where(Question.is_served.is_(served))
    if qtype:
        st = st.where(Question.question_type == qtype)
    if bank:
        st = st.where(QuestionBank.code == bank)
    if reason:
        st = st.where(Question.policy_reasons.any(reason))
    if override is not None:
        st = st.where(Question.admin_override.is_not(None) if override else Question.admin_override.is_(None))
    if source_subject:
        st = st.where(Question.source_subject == source_subject) if source_subject != "_none" \
            else st.where(Question.source_subject.is_(None))
    if inferred_subject:
        st = st.where(Question.inferred_subject == inferred_subject) if inferred_subject != "_none" \
            else st.where(Question.inferred_subject.is_(None))
    if subject_source:
        st = st.where(Question.subject_source == subject_source)
    if has_answer is not None:
        st = st.where(Question.answer_kind.is_not(None) if has_answer else Question.answer_kind.is_(None))
    if has_formula is not None:
        st = st.where(Question.has_formula.is_(has_formula))
    if has_image is not None:
        st = st.where(Question.has_image.is_(has_image))
    if eligible is not None:
        st = st.where(Question.policy_eligible.is_(eligible))
    if scoring_mode:
        st = st.where(Question.scoring_mode == scoring_mode)
    if reported:
        st = st.where(select(QuestionReport.id).where(QuestionReport.question_id == Question.id,
                                                      QuestionReport.status.in_(["open", "triaged"])).exists())
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    rows = db.execute(st.order_by(Question.external_id).offset(off).limit(lim)).all()
    from ..content.render import plain_text
    items = []
    for qu, bcode in rows:
        v = qu.current_version
        items.append({"id": qu.id, "external_id": qu.external_id, "bank": bcode, "subject": qu.subject_code,
                      "source_subject": qu.source_subject, "inferred_subject": qu.inferred_subject,
                      "inference_confidence": qu.inference_confidence, "subject_source": qu.subject_source,
                      "has_answer": qu.answer_kind is not None,
                      "type": qu.question_type, "state": qu.editorial_state, "state_source": qu.state_source,
                      "served": qu.is_served, "eligible": qu.policy_eligible, "reasons": qu.policy_reasons,
                      "override": qu.admin_override, "scoring_mode": qu.scoring_mode,
                      "removed": qu.removed_upstream,
                      "preview": plain_text(v.content["stem"])[:220] if v else ""})
    return {"total": total, "items": items}


def _q(db, qid) -> Question:
    q = db.get(Question, qid)
    if q is None:
        raise err(404, "not_found", "Không tìm thấy câu hỏi.")
    return q


@router.get("/questions/{qid}")
def question_detail(qid: int, db: Session = Depends(get_db)):
    q = _q(db, qid)
    bank = db.get(QuestionBank, q.bank_id)
    versions = db.execute(select(QuestionVersion.id, QuestionVersion.version_no, QuestionVersion.content_hash,
                                 QuestionVersion.created_at, QuestionVersion.source_revision)
                          .where(QuestionVersion.question_id == q.id).order_by(QuestionVersion.version_no.desc())).all()
    usage = db.execute(select(ExamItem.outcome, func.count()).where(ExamItem.question_id == q.id)
                       .group_by(ExamItem.outcome)).all()
    reports = db.scalars(select(QuestionReport).where(QuestionReport.question_id == q.id)
                         .order_by(QuestionReport.id.desc()).limit(50)).all()
    corrections = db.scalars(select(QuestionCorrection).where(QuestionCorrection.question_id == q.id)
                             .order_by(QuestionCorrection.id.desc())).all()
    v = q.current_version
    return {
        "id": q.id, "external_id": q.external_id, "bank": bank.code, "bank_name": bank.name,
        "type": q.question_type, "subject": q.subject_code, "source_subject": q.source_subject, "topic": q.topic,
        "subject_source": q.subject_source, "inferred_subject": q.inferred_subject,
        "inference_confidence": q.inference_confidence, "inference_evidence": q.inference_evidence,
        "subject_override": q.subject_override, "subject_override_note": q.subject_override_note,
        "upstream_effective_subject": q.upstream_effective_subject, "classification_source": q.classification_source,
        "classification_confidence": q.classification_confidence, "classification_evidence": q.classification_evidence,
        "classification_review": q.classification_review,
        "subtopic": q.subtopic, "cognitive_level": q.cognitive_level, "language": q.language,
        "group_key": q.group_key, "exam_systems": q.exam_systems,
        "review_status": q.review_status, "review_flags": q.review_flags,
        "answer_source_type": q.answer_source_type, "editorial_state": q.editorial_state,
        "editorial_states": q.editorial_states, "state_source": q.state_source, "state_notes": q.state_notes,
        "scoring_mode": q.scoring_mode, "content_flags": q.content_flags,
        "content_flag_labels": {k: APP_REASONS.get(k, k) for k in q.content_flags},
        "policy_eligible": q.policy_eligible, "policy_reasons": q.policy_reasons, "served": q.is_served,
        "override": q.admin_override, "override_note": q.admin_override_note, "override_at": iso(q.admin_override_at),
        "removed_upstream": q.removed_upstream, "provenance": q.provenance,
        "first_synced_at": iso(q.first_synced_at), "last_synced_at": iso(q.last_synced_at),
        "content_changed_at": iso(q.content_changed_at),
        "current_version": {"id": v.id, "version_no": v.version_no, "content": v.content, "answer": v.answer,
                            "content_hash": v.content_hash} if v else None,
        "versions": [{"id": i, "version_no": n, "content_hash": h, "created_at": iso(c), "source_revision": r}
                     for i, n, h, c, r in versions],
        "usage": {o or "pending": n for o, n in usage},
        "reports": [_report(r) for r in reports],
        "corrections": [_corr(c) for c in corrections],
    }


@router.get("/questions/{qid}/versions/{vid}")
def question_version(qid: int, vid: int, db: Session = Depends(get_db)):
    v = db.get(QuestionVersion, vid)
    if v is None or v.question_id != qid:
        raise err(404, "not_found", "Không tìm thấy phiên bản.")
    return {"id": v.id, "version_no": v.version_no, "content": v.content, "answer": v.answer,
            "content_hash": v.content_hash, "created_at": iso(v.created_at), "source_revision": v.source_revision}


class OverrideIn(BaseModel):
    action: Literal["enable", "disable", "clear"]
    note: str | None = Field(default=None, max_length=1000)


@router.post("/questions/{qid}/override")
def override(qid: int, body: OverrideIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    q = _q(db, qid)
    if body.action == "enable" and (q.current_version_id is None or q.removed_upstream):
        raise err(409, "cannot_enable", "Không thể bật câu hỏi đã bị gỡ hoặc chưa có nội dung.")
    q.admin_override = None if body.action == "clear" else body.action
    q.admin_override_note = body.note
    q.admin_override_by, q.admin_override_at = admin.id, dt.datetime.now(dt.timezone.utc)
    q.is_served = effective_served(q.policy_eligible, q.admin_override)
    audit(db, admin, f"question_{body.action}", "question", q.external_id, {"note": body.note})
    db.commit()
    return {"served": q.is_served, "override": q.admin_override}


class SubjectOverrideIn(BaseModel):
    subject_code: str | None = None  # null clears the override
    note: str | None = Field(default=None, max_length=1000)


@router.post("/questions/{qid}/subject")
def override_subject(qid: int, body: SubjectOverrideIn, db: Session = Depends(get_db),
                     admin: User = Depends(auth.require_admin)):
    """App-level editorial layer: force the effective subject (upstream data is never modified)."""
    from ..sync.importer import bank_alias
    from ..sync.subjects import resolve_subject, upstream_tuple
    q = _q(db, qid)
    if body.subject_code and db.get(Subject, body.subject_code) is None:
        raise err(422, "invalid", "Môn học không tồn tại.")
    before = q.subject_code
    q.subject_override = body.subject_code or None
    q.subject_override_note = body.note
    q.subject_code, q.subject_source = resolve_subject(q.source_subject, q.inferred_subject, q.inference_confidence,
                                                       q.subject_override, bank_alias(db, q.bank_id),
                                                       get_setting(db, "serving_policy"), upstream_tuple(q))
    audit(db, admin, "question_subject_override", "question", q.external_id,
          {"before": before, "after": q.subject_code, "note": body.note})
    db.commit()
    return {"subject": q.subject_code, "subject_source": q.subject_source}


class CorrectionIn(BaseModel):
    field: str = Field(max_length=60, pattern=r"^(stem|option:[A-H]|answer|solution|explanation|subject|topic|"
                                              r"status|formula:fm_[0-9a-f]+|figure:as_[0-9a-f]+|other)$")
    new_value: str = Field(max_length=20000)
    old_value: str | None = Field(default=None, max_length=20000)
    evidence: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=2000)
    report_id: int | None = None


@router.post("/questions/{qid}/corrections")
def add_correction(qid: int, body: CorrectionIn, db: Session = Depends(get_db),
                   admin: User = Depends(auth.require_admin)):
    q = _q(db, qid)
    c = QuestionCorrection(question_id=q.id, external_id=q.external_id, field=body.field, old_value=body.old_value,
                           new_value=body.new_value, evidence=body.evidence, note=body.note, report_id=body.report_id,
                           created_by=admin.id)
    db.add(c)
    audit(db, admin, "correction_proposed", "question", q.external_id, {"field": body.field})
    db.commit()
    return _corr(c)


def _corr(c: QuestionCorrection) -> dict:
    return {"id": c.id, "question_id": c.question_id, "external_id": c.external_id, "field": c.field,
            "old_value": c.old_value, "new_value": c.new_value, "evidence": c.evidence, "note": c.note,
            "status": c.status, "report_id": c.report_id, "created_at": iso(c.created_at),
            "exported_at": iso(c.exported_at)}


@router.get("/corrections")
def corrections(status: str | None = None, page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 200)
    st = select(QuestionCorrection)
    if status:
        st = st.where(QuestionCorrection.status == status)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    return {"total": total, "items": [_corr(c) for c in db.scalars(
        st.order_by(QuestionCorrection.id.desc()).offset(off).limit(lim))]}


@router.post("/corrections/export", response_class=PlainTextResponse)
def corrections_export(mark: bool = True, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        export_corrections(db, path, mark=mark)
        audit(db, admin, "corrections_exported", "question_correction", None, {"mark": mark})
        db.commit()
        return PlainTextResponse(open(path, encoding="utf-8").read(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": "attachment; filename=hsa_app_corrections.jsonl"})
    finally:
        os.unlink(path)


# ------------------------------------------------------------------------------------------------
# reports
# ------------------------------------------------------------------------------------------------
def _report(r: QuestionReport) -> dict:
    return {"id": r.id, "question_id": r.question_id, "external_id": r.external_id,
            "question_version_id": r.question_version_id, "user_id": r.user_id,
            "session_id": str(r.session_id) if r.session_id else None, "category": r.category, "message": r.message,
            "status": r.status, "admin_note": r.admin_note, "created_at": iso(r.created_at),
            "resolved_at": iso(r.resolved_at)}


@router.get("/reports")
def reports(status: str | None = "open", category: str | None = None, page: int = 1, size: int = 50,
            db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 200)
    st = select(QuestionReport)
    if status:
        st = st.where(QuestionReport.status == status)
    if category:
        st = st.where(QuestionReport.category == category)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    return {"total": total, "items": [_report(r) for r in db.scalars(
        st.order_by(QuestionReport.id.desc()).offset(off).limit(lim))]}


class ReportUpdate(BaseModel):
    status: Literal["open", "triaged", "resolved", "rejected"]
    admin_note: str | None = Field(default=None, max_length=2000)


@router.patch("/reports/{rid}")
def update_report(rid: int, body: ReportUpdate, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    r = db.get(QuestionReport, rid)
    if r is None:
        raise err(404, "not_found", "Không tìm thấy báo lỗi.")
    r.status, r.admin_note = body.status, body.admin_note
    if body.status in ("resolved", "rejected"):
        r.resolved_by, r.resolved_at = admin.id, dt.datetime.now(dt.timezone.utc)
    audit(db, admin, "report_" + body.status, "question_report", r.id)
    db.commit()
    return _report(r)


# ------------------------------------------------------------------------------------------------
# blueprints
# ------------------------------------------------------------------------------------------------
class BlueprintIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    kind: Literal["random", "fixed"] = "random"
    config: dict
    is_published: bool = False
    price_vnd: int = Field(default=0, ge=0, le=100_000_000)
    sort_order: int = 100


def _bp(db, bp: ExamBlueprint, with_availability=False) -> dict:
    d = {"id": bp.id, "code": bp.code, "name": bp.name, "description": bp.description, "kind": bp.kind,
         "config": bp.config, "version": bp.version, "is_published": bp.is_published, "price_vnd": bp.price_vnd,
         "sort_order": bp.sort_order, "updated_at": iso(bp.updated_at),
         "attempts": db.scalar(select(func.count()).where(ExamSession.blueprint_id == bp.id))}
    if with_availability:
        d["availability"] = availability(db, bp.config)
    return d


def availability(db: Session, config: dict) -> dict:
    cfg = parse_config(config)
    out, ok = [], True
    for s in cfg.sections:
        for i, p in enumerate(s.pools):
            n = count_available(db, p, cfg.require_auto_scoring)
            out.append({"section": s.key, "pool": i, "required": p.count, "available": n, "ok": n >= p.count})
            ok &= n >= p.count
        for it in s.items:
            row = db.execute(select(Question.is_served).join(QuestionBank).where(
                QuestionBank.code == it.bank, Question.external_id == it.external_id)).first()
            good = bool(row and row[0])
            out.append({"section": s.key, "fixed": it.external_id, "ok": good})
            ok &= good
    return {"ok": ok, "pools": out, "total_questions": cfg.total_questions, "total_minutes": cfg.total_minutes}


@router.get("/blueprints")
def blueprints(db: Session = Depends(get_db)):
    return {"items": [_bp(db, b, with_availability=True) for b in db.scalars(
        select(ExamBlueprint).order_by(ExamBlueprint.sort_order, ExamBlueprint.id))]}


class ValidateIn(BaseModel):
    config: dict


@router.post("/blueprints/validate")
def validate_blueprint(body: ValidateIn, db: Session = Depends(get_db)):
    try:
        return {"valid": True, **availability(db, body.config)}
    except ValidationError as e:
        return {"valid": False, "errors": [{"loc": ".".join(str(x) for x in er["loc"]), "msg": er["msg"]}
                                           for er in e.errors()]}


def _check_cfg(config):
    try:
        parse_config(config)
    except ValidationError as e:
        raise err(422, "invalid_config", "Cấu hình đề thi không hợp lệ.",
                  errors=[{"loc": ".".join(str(x) for x in er["loc"]), "msg": er["msg"]} for er in e.errors()])


@router.post("/blueprints")
def create_blueprint(body: BlueprintIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    _check_cfg(body.config)
    if db.scalar(select(ExamBlueprint).where(ExamBlueprint.code == body.code)):
        raise err(409, "code_taken", "Mã đề đã tồn tại.")
    bp = ExamBlueprint(**body.model_dump())
    db.add(bp)
    db.flush()
    audit(db, admin, "blueprint_created", "exam_blueprint", bp.id, {"code": bp.code, "price": bp.price_vnd})
    db.commit()
    return _bp(db, bp, True)


@router.put("/blueprints/{bid}")
def update_blueprint(bid: int, body: BlueprintIn, db: Session = Depends(get_db),
                     admin: User = Depends(auth.require_admin)):
    bp = db.get(ExamBlueprint, bid)
    if bp is None:
        raise err(404, "not_found", "Không tìm thấy đề.")
    _check_cfg(body.config)
    changed_cfg = bp.config != body.config
    before = {"price": bp.price_vnd, "published": bp.is_published}
    for k, v in body.model_dump().items():
        setattr(bp, k, v)
    if changed_cfg:
        bp.version += 1  # sessions keep the snapshot of the version they used
    audit(db, admin, "blueprint_updated", "exam_blueprint", bp.id,
          {"before": before, "after": {"price": bp.price_vnd, "published": bp.is_published},
           "config_changed": changed_cfg})
    db.commit()
    return _bp(db, bp, True)


# ------------------------------------------------------------------------------------------------
# products, orders, transactions
# ------------------------------------------------------------------------------------------------
class ProductIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price_vnd: int = Field(gt=0, le=100_000_000)
    attempts: int | None = Field(default=None, gt=0)
    duration_days: int | None = Field(default=None, gt=0)
    blueprint_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    sort_order: int = 100


def _product(p: Product) -> dict:
    return {"id": p.id, "code": p.code, "name": p.name, "description": p.description, "price_vnd": p.price_vnd,
            "attempts": p.attempts, "duration_days": p.duration_days, "blueprint_ids": p.blueprint_ids,
            "is_active": p.is_active, "sort_order": p.sort_order}


@router.get("/products")
def list_products(db: Session = Depends(get_db)):
    return {"items": [_product(p) for p in db.scalars(select(Product).order_by(Product.sort_order, Product.id))]}


@router.post("/products")
def create_product(body: ProductIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    if db.scalar(select(Product).where(Product.code == body.code)):
        raise err(409, "code_taken", "Mã gói đã tồn tại.")
    p = Product(**body.model_dump())
    db.add(p)
    db.flush()
    audit(db, admin, "product_created", "product", p.id, body.model_dump())
    db.commit()
    return _product(p)


@router.put("/products/{pid}")
def update_product(pid: int, body: ProductIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    p = db.get(Product, pid)
    if p is None:
        raise err(404, "not_found", "Không tìm thấy gói.")
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    audit(db, admin, "product_updated", "product", p.id, body.model_dump())
    db.commit()
    return _product(p)


def _order(o: PaymentOrder, email=None) -> dict:
    return {"id": o.id, "code": o.code, "user_id": o.user_id, "user_email": email, "status": o.status,
            "amount_vnd": o.amount_vnd, "paid_amount_vnd": o.paid_amount_vnd, "name": o.product_snapshot.get("name"),
            "provider": o.provider, "created_at": iso(o.created_at), "expires_at": iso(o.expires_at),
            "paid_at": iso(o.paid_at), "note": o.note, "product_id": o.product_id, "blueprint_id": o.blueprint_id}


@router.get("/orders")
def list_orders(status: str | None = None, q: str | None = None, page: int = 1, size: int = 50,
                db: Session = Depends(get_db)):
    commerce.expire_orders(db)
    off, lim = page_params(page, size, 200)
    st = select(PaymentOrder, User.email).join(User, User.id == PaymentOrder.user_id)
    if status:
        st = st.where(PaymentOrder.status == status)
    if q:
        st = st.where(or_(PaymentOrder.code.ilike(f"%{q.strip()}%"), User.email.ilike(f"%{q.strip()}%")))
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    return {"total": total, "items": [_order(o, e) for o, e in db.execute(
        st.order_by(PaymentOrder.id.desc()).offset(off).limit(lim)).all()]}


class ConfirmIn(BaseModel):
    amount_vnd: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=1000)


def _order_by_code(db, code) -> PaymentOrder:
    o = db.scalar(select(PaymentOrder).where(PaymentOrder.code == code.upper()))
    if o is None:
        raise err(404, "not_found", "Không tìm thấy đơn hàng.")
    return o


@router.post("/orders/{code}/confirm")
def confirm_order(code: str, body: ConfirmIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    o = _order_by_code(db, code)
    if o.status not in ("pending", "expired"):
        raise err(409, "not_confirmable", f"Đơn đang ở trạng thái {o.status}.")
    done = commerce.admin_confirm(db, o, admin, body.amount_vnd, body.note)
    db.refresh(o)
    return {"confirmed": done, "order": _order(o)}


@router.post("/orders/{code}/refund")
def refund_order(code: str, body: ConfirmIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    o = _order_by_code(db, code)
    try:
        commerce.refund(db, o, admin, body.note)
    except commerce.CommerceError as e:
        raise err(e.status, e.code, str(e))
    return {"order": _order(o)}


@router.get("/transactions")
def list_transactions(status: str | None = None, page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 200)
    st = select(PaymentTransaction)
    if status:
        st = st.where(PaymentTransaction.status == status)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    return {"total": total, "items": [
        {"id": t.id, "provider": t.provider, "provider_txn_id": t.provider_txn_id, "amount_vnd": t.amount_vnd,
         "content": t.content, "occurred_at": iso(t.occurred_at), "received_at": iso(t.received_at),
         "order_id": t.order_id, "status": t.status, "note": t.note}
        for t in db.scalars(st.order_by(PaymentTransaction.id.desc()).offset(off).limit(lim))]}


class AssignIn(BaseModel):
    order_code: str
    note: str | None = None


@router.post("/transactions/{tid}/assign")
def assign_transaction(tid: int, body: AssignIn, db: Session = Depends(get_db),
                       admin: User = Depends(auth.require_admin)):
    """Reconcile an unmatched/underpaid transfer with an order (e.g. customer mistyped the code)."""
    t = db.get(PaymentTransaction, tid)
    if t is None:
        raise err(404, "not_found", "Không tìm thấy giao dịch.")
    if t.status == "matched":
        raise err(409, "already_matched", "Giao dịch đã được đối soát.")
    o = _order_by_code(db, body.order_code)
    done = commerce.mark_paid(db, o, provider=t.provider, amount=t.amount_vnd, admin_id=admin.id,
                              note=body.note or f"assigned from transaction {t.id}")
    if not done:
        raise err(409, "not_confirmable", f"Đơn đang ở trạng thái {o.status}.")
    t.status, t.order_id, t.note = "matched", o.id, (t.note or "") + f" | assigned by admin {admin.email}"
    audit(db, admin, "transaction_assigned", "payment_transaction", t.id, {"order": o.code})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# users
# ------------------------------------------------------------------------------------------------
@router.get("/users")
def users(q: str | None = None, role: str | None = None, page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 200)
    st = select(User)
    if q:
        st = st.where(or_(User.email.ilike(f"%{q.strip()}%"), User.display_name.ilike(f"%{q.strip()}%")))
    if role:
        st = st.where(User.role == role)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    rows = db.scalars(st.order_by(User.id.desc()).offset(off).limit(lim)).all()
    counts = dict(db.execute(select(ExamSession.user_id, func.count()).where(
        ExamSession.user_id.in_([u.id for u in rows])).group_by(ExamSession.user_id)).all())
    return {"total": total, "items": [dict(user_public(u), is_active=u.is_active, last_login_at=iso(u.last_login_at),
                                           sessions=counts.get(u.id, 0)) for u in rows]}


@router.get("/users/{uid}")
def user_detail(uid: int, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u is None:
        raise err(404, "not_found", "Không tìm thấy người dùng.")
    ents = db.scalars(select(Entitlement).where(Entitlement.user_id == uid).order_by(Entitlement.id.desc())).all()
    orders = db.scalars(select(PaymentOrder).where(PaymentOrder.user_id == uid).order_by(PaymentOrder.id.desc())
                        .limit(50)).all()
    from ..exam.service import session_summary
    sessions = db.scalars(select(ExamSession).where(ExamSession.user_id == uid).order_by(ExamSession.created_at.desc())
                          .limit(50)).all()
    return {"user": dict(user_public(u), is_active=u.is_active, last_login_at=iso(u.last_login_at)),
            "entitlements": [{"id": e.id, "note": e.note, "blueprint_ids": e.blueprint_ids,
                              "attempts_total": e.attempts_total, "attempts_used": e.attempts_used,
                              "valid_until": iso(e.valid_until), "status": e.status} for e in ents],
            "orders": [_order(o) for o in orders], "sessions": [session_summary(s) for s in sessions]}


class UserUpdate(BaseModel):
    role: Literal["student", "admin"] | None = None
    is_active: bool | None = None
    display_name: str | None = Field(default=None, max_length=120)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserUpdate, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise err(404, "not_found", "Không tìm thấy người dùng.")
    if u.id == admin.id and (body.role == "student" or body.is_active is False):
        raise err(409, "self_lockout", "Không thể tự hạ quyền hoặc khoá chính mình.")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(u, k, v)
    if body.is_active is False:
        auth.revoke_all_sessions(db, u.id)
    audit(db, admin, "user_updated", "app_user", u.id, body.model_dump(exclude_none=True))
    db.commit()
    return dict(user_public(u), is_active=u.is_active)


@router.post("/users/{uid}/reset-link")
def reset_link(uid: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise err(404, "not_found", "Không tìm thấy người dùng.")
    token = auth.create_reset_token(db, u, hours=24)
    audit(db, admin, "reset_link_issued", "app_user", u.id)
    db.commit()
    return {"link": f"{get_settings().public_base_url.rstrip('/')}/dat-lai-mat-khau?token={token}", "valid_hours": 24}


class GrantIn(BaseModel):
    blueprint_ids: list[int] = Field(default_factory=list)
    attempts: int | None = Field(default=None, gt=0)
    days: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=500)


@router.post("/users/{uid}/grant")
def grant(uid: int, body: GrantIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise err(404, "not_found", "Không tìm thấy người dùng.")
    e = commerce.admin_grant(db, admin, u, blueprint_ids=body.blueprint_ids, attempts=body.attempts, days=body.days,
                             note=body.note)
    return {"id": e.id}


@router.post("/entitlements/{eid}/revoke")
def revoke(eid: int, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    e = db.get(Entitlement, eid)
    if e is None:
        raise err(404, "not_found", "Không tìm thấy quyền.")
    e.status = "revoked"
    audit(db, admin, "entitlement_revoked", "entitlement", e.id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# settings, subjects, banks, sync
# ------------------------------------------------------------------------------------------------
SETTING_KEYS = {"serving_policy", "payment", "practice", "site"}


@router.get("/settings/{key}")
def get_settings_key(key: str, db: Session = Depends(get_db)):
    if key not in SETTING_KEYS:
        raise err(404, "not_found", "Không có cấu hình này.")
    out = {"key": key, "value": get_setting(db, key)}
    if key == "serving_policy":
        out["reference"] = {"states": EDITORIAL_STATES, "reasons": APP_REASONS}
    if key == "payment":
        s = get_settings()
        out["secrets_configured"] = {"sepay": bool(s.sepay_api_key), "casso": bool(s.casso_secure_token),
                                     "generic": bool(s.generic_webhook_secret)}
        out["webhook_urls"] = {p: f"{s.public_base_url.rstrip('/')}/api/payments/webhook/{p}"
                               for p in ("sepay", "casso", "generic")}
    return out


class SettingIn(BaseModel):
    value: dict


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    if key not in SETTING_KEYS:
        raise err(404, "not_found", "Không có cấu hình này.")
    v = body.value
    if key == "payment":
        if v.get("bank_bin") and not str(v["bank_bin"]).isdigit():
            raise err(422, "invalid", "Mã BIN ngân hàng phải là 6 chữ số.")
        bad = set(v.get("providers") or []) - {"manual", "sepay", "casso", "generic"}
        if bad:
            raise err(422, "invalid", f"Kênh thanh toán không hỗ trợ: {', '.join(bad)}")
    if key == "serving_policy":
        unknown = set(v.get("allowed_states") or []) - set(EDITORIAL_STATES)
        if unknown:
            raise err(422, "invalid", f"Trạng thái không hợp lệ: {', '.join(unknown)}")
    value = set_setting(db, key, v, admin.id)
    audit(db, admin, "setting_updated", "app_setting", key, v)
    result = {"key": key, "value": value}
    if key == "serving_policy":
        result["recomputed"] = recompute_policy(db)
    db.commit()
    return result


class SubjectIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    name: str = Field(min_length=1, max_length=120)
    short_name: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    sort_order: int = 100
    is_active: bool = True
    practice_enabled: bool = True


@router.get("/subjects")
def subjects(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Question.subject_code, func.count()).group_by(Question.subject_code)).all())
    served = dict(db.execute(select(Question.subject_code, func.count()).where(Question.is_served.is_(True))
                             .group_by(Question.subject_code)).all())
    aliases = db.execute(select(SubjectAlias, QuestionBank.code).join(QuestionBank)).all()
    return {"items": [{"code": s.code, "name": s.name, "short_name": s.short_name, "color": s.color,
                       "sort_order": s.sort_order, "is_active": s.is_active, "practice_enabled": s.practice_enabled,
                       "questions": counts.get(s.code, 0), "served": served.get(s.code, 0),
                       "aliases": [{"bank": b, "source_value": a.source_value} for a, b in aliases
                                   if a.subject_code == s.code]}
                      for s in db.scalars(select(Subject).order_by(Subject.sort_order))]}


@router.put("/subjects/{code}")
def upsert_subject(code: str, body: SubjectIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    s = db.get(Subject, code)
    if s is None:
        s = Subject(code=body.code)
        db.add(s)
    for k, v in body.model_dump().items():
        if k != "code":
            setattr(s, k, v)
    audit(db, admin, "subject_saved", "subject", code, body.model_dump())
    db.commit()
    return {"ok": True}


class AliasIn(BaseModel):
    bank: str
    source_value: str = Field(max_length=80)
    subject_code: str


@router.put("/subject-aliases")
def put_alias(body: AliasIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == body.bank))
    if bank is None or db.get(Subject, body.subject_code) is None:
        raise err(404, "not_found", "Ngân hàng hoặc môn học không tồn tại.")
    a = db.get(SubjectAlias, (bank.id, body.source_value))
    if a is None:
        db.add(SubjectAlias(bank_id=bank.id, source_value=body.source_value, subject_code=body.subject_code))
    else:
        a.subject_code = body.subject_code
    # re-map existing questions immediately
    from sqlalchemy import update
    db.execute(update(Question).where(Question.bank_id == bank.id,
                                      Question.source_subject == (body.source_value or None) if body.source_value
                                      else Question.source_subject.is_(None))
               .values(subject_code=body.subject_code))
    audit(db, admin, "subject_alias_saved", "subject_alias", f"{body.bank}:{body.source_value}", body.model_dump())
    db.commit()
    return {"ok": True}


def _bank(db: Session, b: QuestionBank) -> dict:
    counts = db.execute(select(func.count(), func.count().filter(Question.is_served.is_(True)))
                        .where(Question.bank_id == b.id)).first()
    subjects = dict(db.execute(select(Question.subject_code, func.count()).where(
        Question.bank_id == b.id, Question.is_served.is_(True)).group_by(Question.subject_code)).all())
    last = db.scalar(select(SyncRun).where(SyncRun.bank_id == b.id).order_by(SyncRun.id.desc()).limit(1))
    compatible = []
    for bp in db.scalars(select(ExamBlueprint)):
        pools = [p for sec in (bp.config.get("sections") or []) for p in sec.get("pools") or []]
        items = [i for sec in (bp.config.get("sections") or []) for i in sec.get("items") or []]
        if any(not p.get("banks") or b.code in p["banks"] for p in pools) or any(i.get("bank", "hsa") == b.code for i in items):
            compatible.append({"id": bp.id, "code": bp.code, "name": bp.name})
    return {"id": b.id, "code": b.code, "name": b.name, "source_kind": b.source_kind, "description": b.description,
            "version": b.version, "source_uri": b.source_uri, "settings": b.settings, "is_active": b.is_active,
            "created_at": iso(b.created_at), "last_synced_at": iso(b.last_synced_at),
            "questions": counts[0], "served": counts[1], "served_by_subject": subjects,
            "deferred_documents": db.scalar(select(func.count()).where(SourceDocument.bank_id == b.id)),
            "compatible_blueprints": compatible, "last_sync": _run(last) if last else None}


@router.get("/banks")
def banks(db: Session = Depends(get_db)):
    return {"items": [_bank(db, b) for b in db.scalars(select(QuestionBank).order_by(QuestionBank.id))]}


class BankIn(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    version: str | None = Field(default=None, max_length=80)
    source_kind: Literal["manual", "jsonl_import"] = "manual"
    is_active: bool = True
    settings: dict = Field(default_factory=dict)


@router.post("/banks")
def create_bank(body: BankIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    if db.scalar(select(QuestionBank).where(QuestionBank.code == body.code)):
        raise err(409, "code_taken", "Mã ngân hàng đã tồn tại.")
    b = QuestionBank(**body.model_dump())
    db.add(b)
    db.flush()
    db.add(SubjectAlias(bank_id=b.id, source_value="", subject_code="general"))
    audit(db, admin, "bank_created", "question_bank", b.code, body.model_dump())
    db.commit()
    return _bank(db, b)


class BankUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    version: str | None = Field(default=None, max_length=80)
    is_active: bool = True
    settings: dict = Field(default_factory=dict)


@router.put("/banks/{code}")
def update_bank(code: str, body: BankUpdate, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    b = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    if b is None:
        raise err(404, "not_found", "Không tìm thấy ngân hàng.")
    active_changed = b.is_active != body.is_active
    b.name, b.description, b.is_active, b.settings = body.name, body.description, body.is_active, body.settings
    if b.source_kind != "hsa_upstream":  # the upstream bank's version is its upstream revision
        b.version = body.version
    audit(db, admin, "bank_updated", "question_bank", code, body.model_dump())
    result = {}
    if active_changed:
        db.flush()  # the recompute reads the bank's new is_active from the database
        result = recompute_policy(db, b.id)
    db.commit()
    return dict(_bank(db, b), recomputed=result)


@router.get("/banks/{code}/documents")
def bank_documents(code: str, db: Session = Depends(get_db)):
    b = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    if b is None:
        raise err(404, "not_found", "Không tìm thấy ngân hàng.")
    return {"items": [{"external_id": d.external_id, "path": d.path, "status": d.status, "pages": d.pages,
                       "last_synced_at": iso(d.last_synced_at)}
                      for d in db.scalars(select(SourceDocument).where(SourceDocument.bank_id == b.id)
                                          .order_by(SourceDocument.path))]}


@router.get("/banks/{code}/sync-runs")
def bank_sync_runs(code: str, db: Session = Depends(get_db)):
    b = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    if b is None:
        raise err(404, "not_found", "Không tìm thấy ngân hàng.")
    return {"items": [_run(r) for r in db.scalars(select(SyncRun).where(SyncRun.bank_id == b.id)
                                                   .order_by(SyncRun.id.desc()).limit(50))]}


@router.post("/banks/{code}/questions")
def upsert_manual_question(code: str, record: dict, db: Session = Depends(get_db),
                           admin: User = Depends(auth.require_admin)):
    """Create/update one question in a manual or imported bank (same record format as JSONL import)."""
    from ..sync.importer import apply_built, bank_alias
    from ..sync.jsonl_import import ImportError_, build_record
    from ..sync.media import MediaStore
    b = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    if b is None:
        raise err(404, "not_found", "Không tìm thấy ngân hàng.")
    if b.source_kind == "hsa_upstream":
        raise err(422, "invalid", "Ngân hàng HSA được đồng bộ từ nguồn; hãy dùng ngân hàng thủ công.")
    if record.get("subject") and db.get(Subject, record["subject"]) is None:
        raise err(422, "invalid", "Môn học không tồn tại.")
    import hashlib as _h
    import json as _json
    try:
        built = build_record(record, get_settings().media_root, MediaStore(get_settings().media_root))
    except (ImportError_, KeyError, ValueError) as e:
        raise err(422, "invalid", str(e))
    alias = {s.code: s.code for s in db.scalars(select(Subject))}
    alias.update(bank_alias(db, b.id))
    alias.setdefault("", "general")
    stats = collections.Counter()
    apply_built(db, b.id, built, _h.sha256(_json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                alias, get_setting(db, "serving_policy"), None, "manual", stats)
    q = db.scalar(select(Question).where(Question.bank_id == b.id, Question.external_id == built.external_id))
    audit(db, admin, "manual_question_saved", "question", built.external_id, {"bank": code, **dict(stats)})
    db.commit()
    return {"id": q.id, "external_id": q.external_id, "served": q.is_served, "reasons": q.policy_reasons,
            "created": bool(stats["created"]), "new_version": bool(stats["versions_created"])}


@router.get("/audit/question-bank")
def question_bank_audit(db: Session = Depends(get_db)):
    return {"report": get_setting(db, "question_bank_audit") or None,
            "queued": bool(get_setting(db, "audit_request"))}


@router.post("/audit/question-bank")
def queue_question_bank_audit(deep: bool = False, db: Session = Depends(get_db),
                              admin: User = Depends(auth.require_admin)):
    set_setting(db, "audit_request", {"requested_at": dt.datetime.now(dt.timezone.utc).isoformat(), "deep": deep,
                                      "by": admin.email}, admin.id)
    db.commit()
    return {"queued": True}


@router.post("/banks/{code}/import")
def import_bank(code: str, file: UploadFile = File(...), name: str | None = Form(None), db: Session = Depends(get_db),
                admin: User = Depends(auth.require_admin)):
    """Upload app-format JSONL (docs/QUESTION_IMPORT_FORMAT.md). Images must be referenced by URL-less
    relative paths only when importing from the CLI; uploaded files may contain text/formulas/tables."""
    from ..sync.jsonl_import import ImportError_, import_jsonl
    import re as _re
    if not _re.fullmatch(r"[a-z0-9_]{2,40}", code):
        raise err(422, "invalid", "Mã ngân hàng chỉ gồm chữ thường, số, gạch dưới.")
    with tempfile.TemporaryDirectory(prefix="hsa_import_") as d:
        p = os.path.join(d, "bank.jsonl")
        size = 0
        with open(p, "wb") as f:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise err(413, "too_large", "Tệp quá lớn (tối đa 50 MB).")
                f.write(chunk)
        try:
            stats = import_jsonl(db, code, p, get_settings().media_root, name=name)
        except ImportError_ as e:
            raise err(422, "invalid", str(e))
    audit(db, admin, "bank_imported", "question_bank", code, {k: v for k, v in stats.items() if k != "errors"})
    db.commit()
    stats["errors"] = stats["errors"][:100]
    return stats


def _run(r: SyncRun) -> dict:
    return {"id": r.id, "status": r.status, "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
            "source_fingerprint": r.source_fingerprint, "checkpoint": r.checkpoint, "stats": r.stats,
            "error": r.error, "triggered_by": r.triggered_by}


@router.get("/sync-runs")
def sync_runs(db: Session = Depends(get_db)):
    return {"items": [_run(r) for r in db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(30))],
            "upstream_available": (get_settings().upstream_root / "question-bank" / "sqlite" /
                                   "hsa_question_bank.sqlite").exists()}


@router.post("/sync")
def trigger_sync(full: bool = False, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    """Queue a synchronisation. The scheduler container picks it up within a few minutes and runs it
    at low priority outside the API workers (a sync is CPU-heavy and must not starve requests)."""
    running = db.scalar(select(SyncRun).where(SyncRun.status == "running").limit(1))
    set_setting(db, "sync_request", {"requested_at": dt.datetime.now(dt.timezone.utc).isoformat(), "full": full,
                                     "by": admin.email}, admin.id)
    audit(db, admin, "sync_requested", "sync_run", None, {"full": full})
    db.commit()
    return {"queued": True, "already_running": bool(running)}


@router.post("/recompute-policy")
def recompute(db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    r = recompute_policy(db)
    audit(db, admin, "policy_recomputed", "question", None, r)
    db.commit()
    return r


@router.get("/audit")
def audit_log(page: int = 1, size: int = 50, db: Session = Depends(get_db)):
    off, lim = page_params(page, size, 200)
    rows = db.execute(select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_user_id)
                      .order_by(AuditLog.id.desc()).offset(off).limit(lim)).all()
    return {"total": db.scalar(select(func.count()).select_from(AuditLog)), "items": [{"id": a.id, "actor": e, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
                       "data": a.data, "created_at": iso(a.created_at)} for a, e in rows]}
