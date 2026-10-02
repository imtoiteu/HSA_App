"""Student API: catalog, practice/exam sessions, history, bookmarks, reports, dashboard stats."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.orm import Session

from .. import auth
from ..commerce import access as plans
from ..commerce import service as commerce
from ..db import get_db
from ..exam import service as ex
from ..exam.blueprint import BlueprintConfig, Pool, parse_config
from ..exam.selection import SelectionError, count_available
from ..models import (Bookmark, ExamBlueprint, ExamItem, ExamSession, Question, QuestionReport, QuestionVersion,
                      Subject, User)
from ..settings_store import get_setting
from .common import err, page_params

router = APIRouter(prefix="/api", tags=["student"])


# ------------------------------------------------------------------------------------------------
# catalog
# ------------------------------------------------------------------------------------------------
def blueprint_public(db: Session, bp: ExamBlueprint, user: User | None) -> dict:
    cfg = parse_config(bp.config)
    pr = commerce.exam_price(db, bp)
    return {"id": bp.id, "code": bp.code, "name": bp.name, "description": bp.description, "kind": bp.kind,
            "paid": pr["paid"], "price_vnd": pr["price_vnd"], "list_price_vnd": pr["list_price_vnd"],
            "promo": pr["promo"], "attempts_per_purchase": bp.attempts_per_purchase,
            "total_questions": cfg.total_questions, "total_minutes": cfg.total_minutes,
            "timing": cfg.timing, "sections": [{"key": s.key, "title": s.title, "description": s.description,
                                                "duration_minutes": s.duration_minutes,
                                                "count": sum(p.count for p in s.pools) + len(s.items)}
                                               for s in cfg.sections],
            "access": commerce.access_status(db, user, bp)}


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), user: User | None = Depends(auth.optional_user)):
    counts = dict(db.execute(select(Question.subject_code, func.count()).where(
        Question.is_served.is_(True)).group_by(Question.subject_code)).all())
    acc = plans.practice_access(db, user)
    subjects = []
    for s in db.scalars(select(Subject).where(Subject.is_active.is_(True), Subject.practice_enabled.is_(True))
                        .order_by(Subject.sort_order)):
        total = counts.get(s.code, 0)
        free_n = len(plans.free_pool(db, s.code)) if total else 0
        # "available" = what this account can practise; "total" = the whole served bank of the subject
        subjects.append({"code": s.code, "name": s.name, "short_name": s.short_name, "color": s.color,
                         "available": total if acc["limit"] is None else free_n, "total": total,
                         "free_available": free_n})
    pro = commerce.get_plan(db, "PRO")
    paid_on = get_setting(db, "mock_exams").get("paid_enabled", True)
    bps = []
    for bp in db.scalars(select(ExamBlueprint).where(ExamBlueprint.is_published.is_(True))
                         .order_by(ExamBlueprint.sort_order, ExamBlueprint.id)):
        pub = blueprint_public(db, bp, user)
        if pub["paid"] and not paid_on and not pub["access"].get("allowed"):
            continue  # paid exams switched off: only shown to accounts that still hold attempts
        bps.append(pub)
    policy = get_setting(db, "serving_policy")
    served = sum(counts.values())
    with_topic = db.scalar(select(func.count()).where(Question.is_served.is_(True), Question.topic.is_not(None))) or 0
    topics_enabled = served > 0 and with_topic / served >= policy["topic_min_coverage"]
    topics = []
    if topics_enabled:
        topics = [{"subject": s, "topic": t, "available": n} for s, t, n in db.execute(
            select(Question.subject_code, Question.topic, func.count()).where(
                Question.is_served.is_(True), Question.topic.is_not(None))
            .group_by(Question.subject_code, Question.topic).order_by(Question.subject_code, Question.topic))]
    practice = get_setting(db, "practice")
    site = get_setting(db, "site")
    return {"subjects": subjects, "blueprints": bps, "served_total": served, "topics_enabled": topics_enabled,
            "access": {"plan": acc["plan"], "free_limit": plans.free_limit(db), "pro_until": acc["pro_until"],
                       "pro_available": bool(pro and pro.is_active and pro.price_vnd > 0),
                       "pro_price_vnd": pro.price_vnd if pro else None,
                       "pro_duration_days": pro.duration_days if pro else None},
            "topics": topics, "practice": {"max_questions": practice["max_questions"],
                                           "default_questions": practice["default_questions"]},
            "site": {"name": site.get("name"), "announcement": site.get("announcement"),
                     "support_contact": site.get("support_contact")}}


# ------------------------------------------------------------------------------------------------
# sessions
# ------------------------------------------------------------------------------------------------
class CreateSessionIn(BaseModel):
    blueprint_id: int | None = None
    # practice options
    subjects: list[str] = Field(default_factory=list, max_length=20)
    topics: list[str] = Field(default_factory=list, max_length=50)
    types: list[str] = Field(default_factory=list, max_length=10)
    count: int = Field(default=20, ge=1, le=200)
    time_limit_minutes: int | None = Field(default=None, ge=1, le=300)
    feedback: Literal["immediate", "end"] = "immediate"
    source: Literal["all", "bookmarks", "wrong", "unseen"] = "all"
    shuffle_options: bool = True


def _load(db: Session, sid: str, user: User) -> ExamSession:
    try:
        u = uuid.UUID(sid)
    except ValueError:
        raise err(404, "not_found", "Không tìm thấy bài làm.")
    s = db.get(ExamSession, u)
    if s is None or (s.user_id != user.id and user.role != "admin"):
        raise err(404, "not_found", "Không tìm thấy bài làm.")
    return s


def _practice_filter(user: User, source: str):
    if source == "bookmarks":
        sub = select(Bookmark.question_id).where(Bookmark.user_id == user.id)
        return lambda st: st.where(Question.id.in_(sub))
    if source == "wrong":
        sub = select(ExamItem.question_id).join(ExamSession, ExamSession.id == ExamItem.session_id).where(
            ExamSession.user_id == user.id, ExamItem.outcome.in_(["incorrect", "partial"]))
        return lambda st: st.where(Question.id.in_(sub))
    if source == "unseen":
        sub = select(ExamItem.question_id).join(ExamSession, ExamSession.id == ExamItem.session_id).where(
            ExamSession.user_id == user.id)
        return lambda st: st.where(Question.id.not_in(sub))
    return None


@router.post("/sessions")
def create(body: CreateSessionIn, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    auth.rate_limit(db, f"session-create:{user.id}", 30, 600)
    if body.blueprint_id:
        bp = db.get(ExamBlueprint, body.blueprint_id)
        if bp is None or not (bp.is_published or user.role == "admin"):
            raise err(404, "not_found", "Không tìm thấy đề thi.")
        running = db.scalar(select(ExamSession).where(ExamSession.user_id == user.id,
                                                      ExamSession.blueprint_id == bp.id,
                                                      ExamSession.status == "in_progress"))
        if running:
            ex.refresh_timing(db, running)
            if running.status == "in_progress":
                return {"session": ex.session_summary(running), "resumed": True}
        cfg = parse_config(bp.config)
        acc = commerce.access_status(db, user, bp)
        if not acc["allowed"]:
            raise err(402, "payment_required", "Bạn cần mua lượt thi cho đề này.", price_vnd=acc.get("price_vnd"),
                      blueprint_id=bp.id)

        exam_plan = plans.practice_access(db, user)["plan"]

        def spend(s):
            s.access_plan = exam_plan
            ent = commerce.consume(db, user, bp, s.id)
            if ent:
                s.entitlement_id = ent.id

        try:
            s = ex.create_session(db, user, cfg, mode="exam", title=bp.name, blueprint=bp, before_commit=spend)
        except SelectionError as e:
            db.rollback()
            raise err(409, "not_enough_questions", str(e), **e.detail)
        except commerce.CommerceError as e:
            db.rollback()
            raise err(e.status, e.code, str(e))
        return {"session": ex.session_summary(s), "resumed": False}

    # practice
    practice = get_setting(db, "practice")
    if not practice.get("free", True):
        raise err(402, "payment_required", "Chế độ luyện tập hiện không mở miễn phí.")
    n_open = db.scalar(select(func.count()).where(ExamSession.user_id == user.id, ExamSession.mode == "practice",
                                                  ExamSession.status == "in_progress")) or 0
    if n_open >= 10:
        raise err(409, "too_many_open", "Bạn có quá nhiều bài luyện tập đang dở. Hãy hoàn thành hoặc bỏ bớt.")
    count = min(body.count, int(practice.get("max_questions", 100)))
    subjects = body.subjects
    pool = Pool(subjects=subjects, types=body.types, topics=body.topics, count=1)
    policy = get_setting(db, "serving_policy")
    require_auto = not policy["practice_allow_self_check"]
    flt = _practice_filter(user, body.source)
    acc = plans.practice_access(db, user)
    allowed = plans.allowed_question_ids(db, user, subjects)
    if allowed is not None:  # FREE plan: only the free pool of the requested subjects, whatever the request
        src_flt = flt
        flt = (lambda st: src_flt(st).where(Question.id.in_(allowed))) if src_flt else \
            (lambda st: st.where(Question.id.in_(allowed)))
    avail = len([1 for _ in _available(db, pool, require_auto, flt)])
    if avail == 0:
        if allowed is not None and any(True for _ in _available(db, pool, require_auto, _practice_filter(user, body.source))):
            raise err(402, "free_limit", f"Gói Miễn phí cho phép luyện {acc['limit']} câu mỗi môn. Nâng cấp Pro để "
                      "luyện toàn bộ ngân hàng câu hỏi.", limit=acc["limit"], plan="FREE")
        raise err(409, "not_enough_questions", "Không có câu hỏi phù hợp với lựa chọn này.")
    pool.count = min(count, avail)
    names = {s.code: s.short_name or s.name for s in db.scalars(select(Subject))}
    title = "Luyện tập " + (", ".join(names.get(x, x) for x in subjects) if subjects else "tổng hợp")
    if body.source == "bookmarks":
        title = "Ôn câu đã lưu"
    elif body.source == "wrong":
        title = "Luyện lại câu làm sai"
    cfg = BlueprintConfig(sections=[{"key": "practice", "title": title, "pools": [pool.model_dump()],
                                     "order": "shuffled"}],
                          timing="global" if body.time_limit_minutes else "none",
                          duration_minutes=body.time_limit_minutes, shuffle_options=body.shuffle_options,
                          require_auto_scoring=require_auto, feedback=body.feedback, max_group_size=8)
    tag = lambda sess: setattr(sess, "access_plan", acc["plan"])  # noqa: E731
    try:
        s = ex.create_session(db, user, cfg, mode="practice", title=title, extra_filter=flt, before_commit=tag)
    except SelectionError:
        # groups may not fit exactly; retry without keeping passages whole
        cfg.keep_groups_together = False
        s = ex.create_session(db, user, cfg, mode="practice", title=title, extra_filter=flt, before_commit=tag)
    return {"session": ex.session_summary(s), "resumed": False}


def _available(db, pool, require_auto, flt):
    from ..exam.selection import _pool_query
    return _pool_query(db, pool, require_auto, flt)


@router.get("/sessions")
def my_sessions(status: str | None = None, mode: str | None = None, page: int = 1, size: int = 20,
                db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    off, lim = page_params(page, size, 50)
    st = select(ExamSession).where(ExamSession.user_id == user.id)
    if status:
        st = st.where(ExamSession.status == status)
    if mode:
        st = st.where(ExamSession.mode == mode)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    rows = db.scalars(st.order_by(ExamSession.created_at.desc()).offset(off).limit(lim)).all()
    for s in rows:
        if s.status == "in_progress":
            ex.refresh_timing(db, s)
    return {"total": total, "items": [ex.session_summary(s) for s in rows]}


@router.get("/sessions/{sid}")
def get_session(sid: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    ex.refresh_timing(db, s)
    out = ex.session_payload(s)
    bm = set(db.scalars(select(Bookmark.question_id).where(Bookmark.user_id == user.id,
                                                          Bookmark.question_id.in_([it["question_ref"] for it in out["items"]]))))
    for it in out["items"]:
        it["bookmarked"] = it["question_ref"] in bm
    return out


class Change(BaseModel):
    position: int
    response: dict | None = None
    flagged: bool | None = None
    time_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class SaveIn(BaseModel):
    changes: list[Change] = Field(max_length=200)


@router.patch("/sessions/{sid}/answers")
def save(sid: str, body: SaveIn, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    if s.user_id != user.id:
        raise err(403, "forbidden", "Không thể sửa bài làm của người khác.")
    changes = []
    for c in body.changes:
        d = {"position": c.position, "flagged": c.flagged, "time_ms": c.time_ms}
        if "response" in c.model_fields_set:  # an explicit null clears the answer
            d["response"] = c.response
        changes.append(d)
    try:
        saved = ex.save_answers(db, s, changes)
    except ex.SessionError as e:
        db.rollback()
        raise err(e.status, e.code, str(e))
    return {"saved": saved, "status": s.status, "server_now": ex.utcnow().isoformat(),
            "deadline_at": (ex.effective_deadline(s).isoformat() if ex.effective_deadline(s) else None),
            "current_section": s.current_section}


def _own(s, user):
    if s.user_id != user.id:
        raise err(403, "forbidden", "Không thể thao tác trên bài làm của người khác.")


@router.post("/sessions/{sid}/check/{position}")
def check(sid: str, position: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    _own(s, user)
    try:
        it = ex.check_item(db, s, position)
    except ex.SessionError as e:
        raise err(e.status, e.code, str(e))
    return ex.item_payload(s, it, reveal=True)


class SelfIn(BaseModel):
    verdict: Literal["correct", "incorrect"] | None


@router.post("/sessions/{sid}/self-assess/{position}")
def self_assess(sid: str, position: int, body: SelfIn, db: Session = Depends(get_db),
                user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    _own(s, user)
    try:
        it = ex.self_assess(db, s, position, body.verdict)
    except ex.SessionError as e:
        raise err(e.status, e.code, str(e))
    return {"position": it.position, "self_assessment": it.self_assessment}


@router.post("/sessions/{sid}/next-section")
def next_section(sid: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    _own(s, user)
    try:
        ex.advance_section(db, s)
    except ex.SessionError as e:
        raise err(e.status, e.code, str(e))
    return ex.session_payload(s)


@router.post("/sessions/{sid}/submit")
def submit(sid: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    _own(s, user)
    ex.refresh_timing(db, s)
    if s.status == "in_progress":
        ex.submit(db, s, reason="user")
    if s.status != "submitted":
        raise err(409, "closed", "Bài làm đã bị huỷ.")
    return ex.session_payload(s)


@router.post("/sessions/{sid}/abandon")
def abandon(sid: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    s = _load(db, sid, user)
    _own(s, user)
    try:
        ex.abandon(db, s)
    except ex.SessionError as e:
        raise err(e.status, e.code, str(e))
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# bookmarks, reports, reviewed questions
# ------------------------------------------------------------------------------------------------
def _seen_version(db: Session, user: User, qid: int):
    """Latest version of a question the user has legitimately seen with its key (submitted session or
    checked practice item). Returns (version, option_order) or (None, None)."""
    row = db.execute(select(ExamItem.question_version_id, ExamItem.option_order).join(
        ExamSession, ExamSession.id == ExamItem.session_id).where(
        ExamSession.user_id == user.id, ExamItem.question_id == qid,
        (ExamSession.status == "submitted") | (ExamItem.checked.is_(True)))
        .order_by(ExamSession.created_at.desc()).limit(1)).first()
    if not row:
        return None, None
    return db.get(QuestionVersion, row[0]), row[1]


class BookmarkIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.put("/bookmarks/{qid}")
def add_bookmark(qid: int, body: BookmarkIn, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    q = db.get(Question, qid)
    if q is None:
        raise err(404, "not_found", "Không tìm thấy câu hỏi.")
    shown = db.scalar(select(ExamItem.question_version_id).join(ExamSession).where(
        ExamSession.user_id == user.id, ExamItem.question_id == qid).limit(1))
    if shown is None:
        raise err(403, "forbidden", "Chỉ lưu được câu hỏi bạn đã làm.")
    b = db.get(Bookmark, (user.id, qid))
    if b is None:
        db.add(Bookmark(user_id=user.id, question_id=qid, question_version_id=shown, note=body.note))
    else:
        b.note = body.note
    db.commit()
    return {"ok": True, "bookmarked": True}


@router.delete("/bookmarks/{qid}")
def del_bookmark(qid: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    b = db.get(Bookmark, (user.id, qid))
    if b:
        db.delete(b)
        db.commit()
    return {"ok": True, "bookmarked": False}


@router.get("/bookmarks")
def list_bookmarks(page: int = 1, size: int = 20, subject: str | None = None, db: Session = Depends(get_db),
                   user: User = Depends(auth.current_user)):
    off, lim = page_params(page, size, 50)
    st = select(Bookmark, Question).join(Question, Question.id == Bookmark.question_id).where(
        Bookmark.user_id == user.id)
    if subject:
        st = st.where(Question.subject_code == subject)
    total = db.scalar(select(func.count()).select_from(st.subquery()))
    out = []
    for b, q in db.execute(st.order_by(Bookmark.created_at.desc()).offset(off).limit(lim)).all():
        v, order = _seen_version(db, user, q.id)
        if v is not None:
            question, revealed = _reviewed_question(v, order), True
        else:  # seen only in an unfinished attempt: show the question, never its key
            bv = db.get(QuestionVersion, b.question_version_id)
            question = {k: x for k, x in _reviewed_question(bv, None).items()
                        if k not in ("answer", "answer_display", "solution", "explanation")} if bv else None
            revealed = False
        out.append({"question_ref": q.id, "subject": q.subject_code, "note": b.note, "revealed": revealed,
                    "created_at": b.created_at.isoformat(), "question": question})
    return {"total": total, "items": out}


def _reviewed_question(v: QuestionVersion, order) -> dict:
    from ..exam.service import DISPLAY, _options_for
    c = v.content
    out = {"type": c["type"], "language": c.get("language"), "input": c["input"], "group": c.get("group"),
           "stem": c["stem"], "options": _options_for(c, order), "answer": v.answer, "solution": c.get("solution"),
           "explanation": c.get("explanation")}
    a = v.answer
    if a and a["kind"] == "choice":
        o = order or [x["label"] for x in c.get("options") or []]
        out["answer_display"] = [DISPLAY[o.index(l)] for l in a["labels"] if l in o]
    return out


class ReportIn(BaseModel):
    question_ref: int
    category: Literal["wrong_answer", "broken_formula", "broken_image", "typo", "unclear", "other"]
    message: str | None = Field(default=None, max_length=2000)
    session_id: str | None = None
    position: int | None = None


@router.post("/reports")
def report(body: ReportIn, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    auth.rate_limit(db, f"report:{user.id}", 30, 3600)
    q = db.get(Question, body.question_ref)
    if q is None:
        raise err(404, "not_found", "Không tìm thấy câu hỏi.")
    vid, sid = q.current_version_id, None
    if body.session_id:
        s = _load(db, body.session_id, user)
        sid = s.id
        for it in s.items:
            if it.question_id == q.id:
                vid = it.question_version_id  # the exact version the student saw
    db.add(QuestionReport(question_id=q.id, external_id=q.external_id, question_version_id=vid, user_id=user.id,
                          session_id=sid, category=body.category, message=(body.message or "").strip() or None))
    db.commit()
    return {"ok": True, "message": "Cảm ơn bạn! Báo lỗi đã được ghi nhận."}


# ------------------------------------------------------------------------------------------------
# dashboard
# ------------------------------------------------------------------------------------------------
@router.get("/me/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    base = select(ExamSession).where(ExamSession.user_id == user.id)
    in_progress = db.scalars(base.where(ExamSession.status == "in_progress").order_by(
        ExamSession.created_at.desc()).limit(10)).all()
    for s in in_progress:
        ex.refresh_timing(db, s)
    in_progress = [s for s in in_progress if s.status == "in_progress"]
    recent = db.scalars(base.where(ExamSession.status == "submitted").order_by(
        ExamSession.submitted_at.desc()).limit(8)).all()
    n_sub = db.scalar(select(func.count()).where(ExamSession.user_id == user.id,
                                                 ExamSession.status == "submitted")) or 0
    by_subject = db.execute(
        select(Question.subject_code,
               func.count().label("n"),
               func.sum(case((ExamItem.outcome == "correct", 1), else_=0)).label("ok"),
               func.sum(case((ExamItem.outcome.in_(["incorrect", "partial"]), 1), else_=0)).label("bad"))
        .select_from(ExamItem).join(ExamSession, ExamSession.id == ExamItem.session_id)
        .join(Question, Question.id == ExamItem.question_id)
        .where(ExamSession.user_id == user.id, ExamItem.outcome.in_(["correct", "incorrect", "partial", "unanswered"]))
        .group_by(Question.subject_code)).all()
    subjects = {s.code: s for s in db.scalars(select(Subject))}
    totals = {"answered": 0, "correct": 0}
    subj = []
    for code, n, ok, bad in by_subject:
        ok, bad = int(ok or 0), int(bad or 0)
        totals["answered"] += ok + bad
        totals["correct"] += ok
        subj.append({"code": code, "name": subjects[code].short_name if code in subjects else code,
                     "color": subjects[code].color if code in subjects else None,
                     "questions": int(n), "correct": ok, "attempted": ok + bad,
                     "accuracy": round(ok / (ok + bad), 3) if ok + bad else None})
    n_bm = db.scalar(select(func.count()).where(Bookmark.user_id == user.id)) or 0
    return {"in_progress": [ex.session_summary(s) for s in in_progress],
            "recent": [ex.session_summary(s) for s in recent], "submitted_total": n_sub,
            "accuracy": round(totals["correct"] / totals["answered"], 3) if totals["answered"] else None,
            "answered_total": totals["answered"], "by_subject": subj, "bookmarks": n_bm}
