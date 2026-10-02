"""Admin: operational dashboard, practice plans (FREE/PRO), subject reconciliation and QA queues."""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session

from .. import auth
from ..admin_ops import audit
from ..commerce import access as plans
from ..commerce import service as commerce
from ..db import get_db
from ..exam.blueprint import Pool
from ..exam.selection import _pool_query
from ..models import (EntitlementUsage, ExamItem, ExamSession, PaymentOrder, PaymentTransaction, Plan,
                      PlanSubscription, Question, QuestionBank, QuestionReport, Subject, SubjectAlias, SyncRun, User)
from ..settings_store import get_setting
from .common import err

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(auth.require_admin)])


def iso(t):
    return t.isoformat() if t else None


# ------------------------------------------------------------------------------------------------
# dashboard
# ------------------------------------------------------------------------------------------------
@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    now = commerce.utcnow()
    d1, d7, d30 = (now - dt.timedelta(days=n) for n in (1, 7, 30))
    cnt = lambda *w: db.scalar(select(func.count()).where(*w)) or 0  # noqa: E731

    # question bank
    by_state = dict(db.execute(select(Question.editorial_state, func.count()).group_by(Question.editorial_state)).all())
    total_q = sum(by_state.values())
    served = cnt(Question.is_served.is_(True))
    eligible = cnt(Question.policy_eligible.is_(True))
    bank = {"total": total_q, "ready_to_serve": by_state.get("READY_TO_SERVE", 0), "eligible": eligible,
            "served": served, "excluded": total_q - served, "by_state": by_state,
            "unclassified": cnt(Question.subject_code == "general"),
            "subject_review": cnt(Question.classification_review.is_not(None)),
            "formula_review": by_state.get("NEEDS_FORMULA_REVIEW", 0),
            "visual_review": by_state.get("NEEDS_VISUAL_REVIEW", 0),
            "answer_linking": by_state.get("NEEDS_ANSWER_LINKING", 0),
            "open_reports": cnt(QuestionReport.status == "open")}

    # users
    active_now = select(PlanSubscription.user_id).where(
        PlanSubscription.status == "active", PlanSubscription.starts_at <= now, PlanSubscription.expires_at > now)
    ever = select(PlanSubscription.user_id).where(PlanSubscription.status == "active")
    students = cnt(User.role == "student")
    pro = cnt(User.id.in_(active_now), User.role == "student")
    users = {"total": cnt(User.id.is_not(None)), "students": students, "admins": cnt(User.role == "admin"),
             "pro_active": pro, "free": students - pro,
             "pro_expired": cnt(User.id.in_(ever), User.id.not_in(active_now), User.role == "student"),
             "new_24h": cnt(User.created_at >= d1), "new_7d": cnt(User.created_at >= d7),
             "new_30d": cnt(User.created_at >= d30),
             "active_7d": db.scalar(select(func.count(func.distinct(ExamSession.user_id)))
                                    .where(ExamSession.created_at >= d7)) or 0,
             "active_30d": db.scalar(select(func.count(func.distinct(ExamSession.user_id)))
                                     .where(ExamSession.created_at >= d30)) or 0}

    # practice
    answered = (select(Question.subject_code, func.count()).select_from(ExamItem)
                .join(ExamSession, ExamSession.id == ExamItem.session_id)
                .join(Question, Question.id == ExamItem.question_id)
                .where(ExamSession.mode == "practice", ExamItem.answered_at.is_not(None))
                .group_by(Question.subject_code))
    by_plan = dict(db.execute(select(func.coalesce(ExamSession.access_plan, "?"), func.count())
                              .where(ExamSession.mode == "practice").group_by(ExamSession.access_plan)).all())
    practice = {"sessions": cnt(ExamSession.mode == "practice"),
                "sessions_30d": cnt(ExamSession.mode == "practice", ExamSession.created_at >= d30),
                "answered": db.scalar(select(func.count()).select_from(ExamItem).join(
                    ExamSession, ExamSession.id == ExamItem.session_id).where(
                    ExamSession.mode == "practice", ExamItem.answered_at.is_not(None))) or 0,
                "answered_by_subject": {k or "?": n for k, n in db.execute(answered).all()},
                "sessions_by_plan": by_plan}

    # exams
    paid_usage = select(EntitlementUsage.session_id)
    exams = {"attempts": cnt(ExamSession.mode == "exam"),
             "submitted": cnt(ExamSession.mode == "exam", ExamSession.status == "submitted"),
             "in_progress": cnt(ExamSession.mode == "exam", ExamSession.status == "in_progress"),
             "abandoned": cnt(ExamSession.mode == "exam", ExamSession.status == "abandoned"),
             "paid_attempts": cnt(ExamSession.mode == "exam", ExamSession.id.in_(paid_usage)),
             "attempts_30d": cnt(ExamSession.mode == "exam", ExamSession.created_at >= d30)}
    exams["free_attempts"] = exams["attempts"] - exams["paid_attempts"]
    exams["completion_rate"] = round(exams["submitted"] / exams["attempts"], 3) if exams["attempts"] else None

    # payments (revenue = what was actually confirmed on paid orders)
    by_status = dict(db.execute(select(PaymentOrder.status, func.count()).group_by(PaymentOrder.status)).all())
    rev = lambda *w: db.scalar(select(func.coalesce(func.sum(func.coalesce(  # noqa: E731
        PaymentOrder.paid_amount_vnd, PaymentOrder.amount_vnd)), 0)).where(PaymentOrder.status == "paid", *w)) or 0
    pay = get_setting(db, "payment")
    payments = {"by_status": by_status, "pending": by_status.get("pending", 0), "paid": by_status.get("paid", 0),
                "expired": by_status.get("expired", 0), "cancelled": by_status.get("cancelled", 0),
                "failed": by_status.get("failed", 0), "refunded": by_status.get("refunded", 0),
                "revenue_vnd": rev(), "revenue_30d_vnd": rev(PaymentOrder.paid_at >= d30),
                "revenue_pro_vnd": rev(PaymentOrder.kind == "pro"),
                "revenue_exam_vnd": rev(PaymentOrder.kind.in_(["exam", "product"])),
                "unmatched_transactions": cnt(PaymentTransaction.status.in_(["unmatched", "underpaid", "late"])),
                "bank_configured": bool(pay.get("bank_bin") and pay.get("account_number")),
                "account_holder_configured": bool(pay.get("account_name")),
                "payments_enabled": bool(pay.get("enabled", True))}
    per_day = db.execute(select(cast(ExamSession.created_at, Date), ExamSession.mode, func.count())
                         .where(ExamSession.created_at >= d30)
                         .group_by(cast(ExamSession.created_at, Date), ExamSession.mode)
                         .order_by(cast(ExamSession.created_at, Date))).all()
    last = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    pro_plan = commerce.get_plan(db, "PRO")
    return {"bank": bank, "users": users, "practice": practice, "exams": exams, "payments": payments,
            "activity": [{"day": d.isoformat(), "mode": m, "n": n} for d, m, n in per_day],
            "config": {"free_questions_per_subject": plans.free_limit(db),
                       "pro_price_vnd": pro_plan.price_vnd if pro_plan else None,
                       "pro_duration_days": pro_plan.duration_days if pro_plan else None,
                       "pro_active": bool(pro_plan and pro_plan.is_active),
                       "mock_exams": get_setting(db, "mock_exams")},
            "last_sync": {"id": last.id, "status": last.status, "finished_at": iso(last.finished_at)} if last else None}


# ------------------------------------------------------------------------------------------------
# plans
# ------------------------------------------------------------------------------------------------
def _plan(db: Session, p: Plan) -> dict:
    now = commerce.utcnow()
    active = db.scalar(select(func.count(func.distinct(PlanSubscription.user_id))).where(
        PlanSubscription.plan_code == p.code, PlanSubscription.status == "active",
        PlanSubscription.starts_at <= now, PlanSubscription.expires_at > now)) if p.code != "FREE" else None
    by = db.get(User, p.updated_by) if p.updated_by else None
    return {"code": p.code, "name": p.name, "description": p.description, "price_vnd": p.price_vnd,
            "duration_days": p.duration_days, "is_active": p.is_active,
            "benefits": list((p.features or {}).get("benefits") or []), "sort_order": p.sort_order,
            "updated_at": iso(p.updated_at), "updated_by": by.email if by else None, "active_subscribers": active}


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    return {"items": [_plan(db, p) for p in db.scalars(select(Plan).order_by(Plan.sort_order, Plan.id))],
            "free_questions_per_subject": plans.free_limit(db)}


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price_vnd: int = Field(ge=0, le=100_000_000)
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    is_active: bool = True
    benefits: list[str] = Field(default_factory=list, max_length=12)


@router.put("/plans/{code}")
def update_plan(code: str, body: PlanIn, db: Session = Depends(get_db), admin: User = Depends(auth.require_admin)):
    """Change a plan. New orders use the new values; existing orders keep the amount and duration they were
    created with (order snapshot), and running subscriptions keep their period."""
    p = commerce.get_plan(db, code)
    if p is None:
        raise err(404, "not_found", "Không tìm thấy gói.")
    if p.code == "PRO" and (body.price_vnd < 1000 or not body.duration_days):
        raise err(422, "invalid", "Gói Pro cần giá ≥ 1.000 VND và thời hạn (số ngày).")
    if p.code == "FREE" and body.price_vnd != 0:
        raise err(422, "invalid", "Gói Miễn phí có giá 0.")
    benefits = [b.strip() for b in body.benefits if b.strip()]
    if any(len(b) > 200 for b in benefits):
        raise err(422, "invalid", "Mỗi quyền lợi tối đa 200 ký tự.")
    before = _plan(db, p)
    p.name, p.description, p.price_vnd, p.is_active = body.name, body.description, body.price_vnd, body.is_active
    p.duration_days = body.duration_days if p.code != "FREE" else None
    p.features = dict(p.features or {}, benefits=benefits)
    p.updated_by = admin.id
    changes = {k: {"before": before[k], "after": v} for k, v in
               {"name": p.name, "description": p.description, "price_vnd": p.price_vnd,
                "duration_days": p.duration_days, "is_active": p.is_active, "benefits": benefits}.items()
               if before[k] != v}
    audit(db, admin, "plan_updated", "plan", p.code, {"changes": changes})
    db.commit()
    return _plan(db, p)


# ------------------------------------------------------------------------------------------------
# subject reconciliation: upstream final subject → imported → effective → QA → eligible → FREE/PRO → API
# ------------------------------------------------------------------------------------------------
@router.get("/reconciliation/subjects")
def subject_reconciliation(db: Session = Depends(get_db)):
    alias = select(SubjectAlias.subject_code).where(
        SubjectAlias.bank_id == Question.bank_id,
        SubjectAlias.source_value == func.coalesce(Question.upstream_effective_subject, "")).scalar_subquery()
    up = case((Question.classification_source.is_(None), Question.subject_code), else_=alias).label("up")
    active_bank = select(QuestionBank.id).where(QuestionBank.is_active.is_(True))
    rows = db.execute(select(
        up, Question.subject_code, func.count().label("n"),
        func.count().filter(Question.editorial_state == "READY_TO_SERVE"),
        func.count().filter(Question.policy_eligible.is_(True)),
        func.count().filter(Question.is_served.is_(True)),
        func.count().filter(Question.classification_review.is_not(None)),
        func.count().filter(Question.subject_source == "admin"),
    ).where(Question.bank_id.in_(active_bank)).group_by(up, Question.subject_code)).all()
    subjects = {s.code: s for s in db.scalars(select(Subject).order_by(Subject.sort_order))}
    out: dict[str, dict] = {}

    def row(code):
        if code not in out:
            s = subjects.get(code)
            out[code] = {"subject": code, "name": s.name if s else ("(chưa gán môn)" if code == "_none" else code), "upstream_total": 0, "imported": 0,
                         "effective": 0, "upstream_ready": 0, "eligible": 0, "served": 0, "free_accessible": 0,
                         "pro_accessible": 0, "student_api": 0, "excluded": 0, "review_required": 0,
                         "mismatch_in": 0, "mismatch_out": 0, "admin_overrides": 0}
        return out[code]
    for upc, eff, n, ready, elig, served, review, admin_n in rows:
        upc = upc or "general"  # no final upstream subject = Chưa phân loại
        eff_key = eff or "_none"  # no app subject at all (e.g. an imported bank with an unmapped subject)
        r_up, r_eff = row(upc), row(eff_key)
        r_up["upstream_total"] += n
        r_up["imported"] += n
        r_up["upstream_ready"] += ready
        r_up["review_required"] += review
        r_eff["effective"] += n
        r_eff["eligible"] += elig
        r_eff["served"] += served
        r_eff["admin_overrides"] += admin_n
        if upc != eff_key:
            r_up["mismatch_out"] += n
            r_eff["mismatch_in"] += n
    require_auto = not get_setting(db, "serving_policy")["practice_allow_self_check"]
    count = lambda subj: sum(1 for _ in _pool_query(db, Pool(subjects=subj, count=1), require_auto, None))  # noqa: E731
    for code, r in out.items():
        # what the student-facing practice query actually returns (PRO = whole served bank); questions without a
        # subject are only reachable through "all subjects" practice
        r["student_api"] = count([code]) if code != "_none" else \
            count([]) - sum(count([c]) for c in out if c != "_none")
        r["pro_accessible"] = r["student_api"]
        r["free_accessible"] = len(plans.free_pool(db, code)) if r["student_api"] and code != "_none" else 0
        r["excluded"] = r["effective"] - r["served"]
    order = {c: i for i, c in enumerate(subjects)}
    items = sorted(out.values(), key=lambda r: order.get(r["subject"], 999))
    tot = {k: sum(r[k] for r in items) for k in items[0] if k not in ("subject", "name")} if items else {}
    return {"items": items, "totals": tot, "free_limit": plans.free_limit(db),
            "practice_allow_self_check": not require_auto,
            "note": "upstream_total/imported/upstream_ready/review_required are counted by the bank's final "
                    "subject; effective/eligible/served/student_api by the app subject. mismatch_out = imported "
                    "with this upstream subject but shown under another app subject (admin overrides)."}


# ------------------------------------------------------------------------------------------------
# QA queues
# ------------------------------------------------------------------------------------------------
QUEUES = [
    ("subject_review", "Cần xem lại phân loại môn", {"classification_review": "true"}),
    ("formula", "Cần duyệt công thức", {"state": "NEEDS_FORMULA_REVIEW"}),
    ("visual", "Cần duyệt hình ảnh", {"state": "NEEDS_VISUAL_REVIEW"}),
    ("answer", "Cần liên kết đáp án", {"state": "NEEDS_ANSWER_LINKING"}),
    ("review", "Cần xem lại", {"state": "NEEDS_REVIEW"}),
    ("render", "Lỗi hiển thị (công thức/đối tượng)", {"reason": "render_warning"}),
    ("formula_render", "Công thức lỗi KaTeX", {"reason": "formula_render_error"}),
    ("passage_missing", "Thiếu đoạn văn chung", {"reason": "group_context_missing"}),
    ("passage_suspect", "Gắn nhầm đoạn văn", {"reason": "group_membership_suspect"}),
    ("unsupported", "Dạng câu chưa hỗ trợ", {"reason": "unsupported_for_serving"}),
    ("reported", "Học sinh báo lỗi", {"reported": "true"}),
]


@router.get("/qa-queues")
def qa_queues(db: Session = Depends(get_db)):
    out = []
    for key, label, f in QUEUES:
        st = select(func.count()).select_from(Question)
        if "classification_review" in f:
            st = st.where(Question.classification_review.is_not(None))
        if "state" in f:
            st = st.where(Question.editorial_state == f["state"])
        if "reason" in f:
            st = st.where(Question.policy_reasons.any(f["reason"]))
        if "reported" in f:
            st = st.where(select(QuestionReport.id).where(QuestionReport.question_id == Question.id,
                                                          QuestionReport.status.in_(["open", "triaged"])).exists())
        n = db.scalar(st) or 0
        out.append({"key": key, "label": label, "filter": f, "count": n,
                    "ready_upstream": db.scalar(st.where(Question.editorial_state == "READY_TO_SERVE")) or 0})
    return {"items": out}
