"""Exam & practice sessions: creation (frozen items), answer persistence, timing, submission, review."""
import datetime as dt
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExamBlueprint, ExamItem, ExamSession, QuestionVersion, User
from .blueprint import BlueprintConfig, parse_config
from .scoring import score_item, score_session
from .selection import GENERATOR_VERSION, option_permutation, select_questions

GRACE = dt.timedelta(seconds=20)  # network latency allowance after a deadline


class SessionError(Exception):
    def __init__(self, message, status=400, code="invalid"):
        super().__init__(message)
        self.status, self.code = status, code


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------------------------------------------
# creation
# ------------------------------------------------------------------------------------------------
def create_session(db: Session, user: User, cfg: BlueprintConfig, *, mode: str, title: str,
                   blueprint: ExamBlueprint | None = None, seed: str | None = None, extra_filter=None,
                   before_commit=None, now: dt.datetime | None = None) -> ExamSession:
    now = now or utcnow()
    seed = seed or secrets.token_hex(8)
    sel = select_questions(db, cfg, seed, extra_filter)
    versions = {v.id: v for v in db.scalars(select(QuestionVersion).where(
        QuestionVersion.id.in_([p.version_id for p in sel.items])))}
    s = ExamSession(id=uuid.uuid4(), user_id=user.id, mode=mode, title=title,
                    blueprint_id=blueprint.id if blueprint else None,
                    blueprint_version=blueprint.version if blueprint else None,
                    blueprint_snapshot=cfg.model_dump(mode="json"),
                    scoring_config=cfg.scoring.model_dump(mode="json"), seed=seed,
                    generator_version=GENERATOR_VERSION, pool_fingerprint=sel.pool_fingerprint,
                    feedback=cfg.feedback, status="in_progress", started_at=now, current_section=0,
                    section_started_at=now if cfg.timing == "per_section" else None,
                    deadline_at=now + dt.timedelta(minutes=cfg.duration_minutes) if cfg.timing == "global" else None)
    db.add(s)
    db.flush()
    for pos, p in enumerate(sel.items, start=1):
        v = versions[p.version_id]
        labels = [o["label"] for o in v.content.get("options") or []]
        order = None
        if labels:
            order = option_permutation(seed, p.external_id, labels) \
                if cfg.shuffle_options and v.content.get("shuffle_safe") else labels
        db.add(ExamItem(session_id=s.id, position=pos, section_index=p.section_index, question_id=p.question_id,
                        question_version_id=p.version_id, option_order=order, group_key=p.group_key,
                        points=p.points, scoring_mode=p.scoring_mode))
    db.flush()
    if before_commit:
        before_commit(s)
    db.commit()
    db.refresh(s)
    return s


# ------------------------------------------------------------------------------------------------
# timing
# ------------------------------------------------------------------------------------------------
def cfg_of(s: ExamSession) -> BlueprintConfig:
    return parse_config(s.blueprint_snapshot)


def section_deadline(s: ExamSession, cfg: BlueprintConfig | None = None):
    cfg = cfg or cfg_of(s)
    if cfg.timing == "per_section" and s.section_started_at and s.current_section < len(cfg.sections):
        return s.section_started_at + dt.timedelta(minutes=cfg.sections[s.current_section].duration_minutes)
    return None


def effective_deadline(s: ExamSession, cfg=None):
    return s.deadline_at if s.deadline_at else section_deadline(s, cfg)


def refresh_timing(db: Session, s: ExamSession, now=None) -> ExamSession:
    """Apply expired deadlines: advance timed-out sections, auto-submit timed-out exams."""
    if s.status != "in_progress":
        return s
    now = now or utcnow()
    cfg = cfg_of(s)
    if cfg.timing == "global" and s.deadline_at and now > s.deadline_at + GRACE:
        return submit(db, s, reason="timeout", now=now)
    if cfg.timing == "per_section":
        dl = section_deadline(s, cfg)
        if dl and now > dl + GRACE:
            if s.current_section + 1 >= len(cfg.sections):
                return submit(db, s, reason="timeout", now=now)
            # sections are sequential: the next one starts when the student continues (now)
            s.current_section += 1
            s.section_started_at = now
            db.commit()
    return s


def advance_section(db: Session, s: ExamSession, now=None) -> ExamSession:
    now = now or utcnow()
    _require_open(s)
    cfg = cfg_of(s)
    if cfg.timing != "per_section":
        raise SessionError("Bài này không chia thời gian theo phần.")
    if s.current_section + 1 >= len(cfg.sections):
        return submit(db, s, reason="user", now=now)
    s.current_section += 1
    s.section_started_at = now
    db.commit()
    return s


def _require_open(s: ExamSession):
    if s.status != "in_progress":
        raise SessionError("Bài làm đã kết thúc.", 409, "closed")


# ------------------------------------------------------------------------------------------------
# answers
# ------------------------------------------------------------------------------------------------
def validate_response(inp: dict, resp, option_labels: list[str]):
    if resp is None:
        return None
    if not isinstance(resp, dict):
        raise SessionError("Định dạng câu trả lời không hợp lệ.")
    k = inp.get("kind")
    if k == "choice":
        labels = resp.get("labels")
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise SessionError("Định dạng câu trả lời không hợp lệ.")
        labels = sorted(set(labels))
        if not set(labels) <= set(option_labels):
            raise SessionError("Phương án không tồn tại.")
        if not inp.get("multi") and len(labels) > 1:
            raise SessionError("Chỉ được chọn một phương án.")
        return {"labels": labels}
    if k == "numeric":
        v = resp.get("value")
        if v is not None and not isinstance(v, (str, int, float)):
            raise SessionError("Giá trị không hợp lệ.")
        return {"value": str(v)[:60] if v is not None else None}
    if k == "text":
        t = resp.get("text")
        if t is not None and not isinstance(t, str):
            raise SessionError("Giá trị không hợp lệ.")
        return {"text": (t or "")[:2000]}
    if k == "tf_sequence":
        vals = resp.get("values")
        if not isinstance(vals, list) or len(vals) > inp.get("n", 0) or \
                not all(v is None or isinstance(v, bool) for v in vals):
            raise SessionError("Định dạng câu trả lời không hợp lệ.")
        return {"values": vals}
    if k == "boolean":
        v = resp.get("value")
        if v is not None and not isinstance(v, bool):
            raise SessionError("Giá trị không hợp lệ.")
        return {"value": v}
    if k == "none":
        t = resp.get("text")
        return {"text": (t or "")[:5000]} if isinstance(t, str) else None
    raise SessionError("Câu hỏi không nhận câu trả lời.")


def _item(s: ExamSession, position: int) -> ExamItem:
    for it in s.items:
        if it.position == position:
            return it
    raise SessionError("Không tìm thấy câu hỏi.", 404, "not_found")


def _editable(s: ExamSession, it: ExamItem, now):
    cfg = cfg_of(s)
    dl = effective_deadline(s, cfg)
    if dl and now > dl + GRACE:
        raise SessionError("Đã hết thời gian làm bài.", 409, "time_up")
    if cfg.timing == "per_section" and it.section_index != s.current_section:
        raise SessionError("Câu hỏi không thuộc phần đang làm.", 409, "section_locked")
    if it.checked:
        raise SessionError("Câu này đã được kiểm tra đáp án, không thể sửa.", 409, "checked")


def save_answers(db: Session, s: ExamSession, changes: list[dict], now=None) -> dict:
    """Batch autosave. changes: [{position, response?, flagged?, time_ms?}]; returns per-position status."""
    now = now or utcnow()
    refresh_timing(db, s, now)
    _require_open(s)
    saved = {}
    for ch in changes[:200]:
        it = _item(s, int(ch["position"]))
        if "flagged" in ch and ch["flagged"] is not None:
            it.flagged = bool(ch["flagged"])
        if ch.get("time_ms"):
            it.time_spent_ms = min(it.time_spent_ms + max(0, int(ch["time_ms"])), 10 * 3600 * 1000)
        if "response" in ch:
            _editable(s, it, now)
            c = it.version.content
            it.answer = validate_response(c["input"], ch["response"], [o["label"] for o in c.get("options") or []])
            it.answered_at = now
        saved[it.position] = "ok"
    db.commit()
    return saved


def check_item(db: Session, s: ExamSession, position: int, now=None) -> ExamItem:
    """Immediate-feedback practice: reveal the key for one item (locks its answer)."""
    now = now or utcnow()
    _require_open(s)
    if s.feedback != "immediate":
        raise SessionError("Chế độ này chỉ hiện đáp án khi nộp bài.", 409, "no_immediate")
    it = _item(s, position)
    if not it.checked:
        r = score_item(it.version.answer, it.scoring_mode, it.answer, it.points, s.scoring_config)
        it.checked, it.outcome = True, r.outcome
    db.commit()
    return it


def self_assess(db: Session, s: ExamSession, position: int, verdict: str | None):
    it = _item(s, position)
    if it.scoring_mode != "self_check":
        raise SessionError("Câu này được chấm tự động.")
    if verdict not in ("correct", "incorrect", None):
        raise SessionError("Giá trị không hợp lệ.")
    if s.status == "in_progress":
        it.self_assessment = verdict
        db.commit()
    else:
        raise SessionError("Bài làm đã kết thúc.", 409, "closed")
    return it


# ------------------------------------------------------------------------------------------------
# submission
# ------------------------------------------------------------------------------------------------
def submit(db: Session, s: ExamSession, reason: str = "user", now=None) -> ExamSession:
    now = now or utcnow()
    if s.status == "submitted":
        return s
    _require_open(s)
    cfg = cfg_of(s)
    rows = [{"position": it.position, "section_index": it.section_index, "answer": it.version.answer,
             "scoring_mode": it.scoring_mode, "response": it.answer, "points": it.points} for it in s.items]
    res = score_session(rows, s.scoring_config, [{"key": x.key, "title": x.title} for x in cfg.sections])
    for it in s.items:  # items first: the freeze trigger fires once the session is 'submitted'
        r = res["items"][it.position]
        it.outcome, it.points_awarded = r.outcome, r.points
    self_counts = {"correct": sum(1 for it in s.items if it.self_assessment == "correct"),
                   "incorrect": sum(1 for it in s.items if it.self_assessment == "incorrect")}
    db.flush()
    s.status, s.submitted_at, s.submit_reason = "submitted", now, reason
    s.score, s.max_score = res["score"], res["max_score"]
    s.result = {"counts": res["counts"], "sections": res["sections"], "scaled": res["scaled"],
                "scale_to": res["scale_to"], "total": res["total"], "self_assessed": self_counts,
                "duration_seconds": int((now - s.started_at).total_seconds())}
    db.commit()
    return s


def abandon(db: Session, s: ExamSession) -> ExamSession:
    _require_open(s)
    s.status = "abandoned"
    db.commit()
    return s


# ------------------------------------------------------------------------------------------------
# client payloads
# ------------------------------------------------------------------------------------------------
DISPLAY = "ABCDEFGH"


def _options_for(content: dict, order: list[str] | None):
    opts = {o["label"]: o for o in content.get("options") or []}
    order = order or list(opts)
    return [{"key": lab, "display": DISPLAY[i], "content": opts[lab]["content"]} for i, lab in enumerate(order)
            if lab in opts]


def item_payload(s: ExamSession, it: ExamItem, reveal: bool) -> dict:
    c = it.version.content
    q = {"type": c["type"], "language": c.get("language"), "input": c["input"], "group": c.get("group"),
         "stem": c["stem"], "options": _options_for(c, it.option_order)}
    out = {"position": it.position, "section_index": it.section_index, "question": q, "response": it.answer,
           "flagged": it.flagged, "checked": it.checked, "scoring_mode": it.scoring_mode,
           "self_assessment": it.self_assessment, "question_ref": it.question_id, "points": it.points}
    if reveal or it.checked:
        a = it.version.answer
        out["answer"] = a
        if a and a["kind"] == "choice" and it.option_order:
            out["answer_display"] = [DISPLAY[it.option_order.index(l)] for l in a["labels"] if l in it.option_order]
        out["solution"] = c.get("solution")
        out["explanation"] = c.get("explanation")
        out["outcome"] = it.outcome
        out["points_awarded"] = it.points_awarded
    return out


def session_payload(s: ExamSession, now=None) -> dict:
    now = now or utcnow()
    cfg = cfg_of(s)
    done = s.status == "submitted"
    reveal = done and cfg.allow_review
    visible = []
    for it in s.items:
        if not done and cfg.timing == "per_section" and it.section_index > s.current_section:
            continue  # later sections are not delivered before they start
        visible.append(item_payload(s, it, reveal))
    sections = []
    for i, sec in enumerate(cfg.sections):
        n = sum(1 for it in s.items if it.section_index == i)
        st = "done" if done or (cfg.timing == "per_section" and i < s.current_section) else \
            ("active" if (cfg.timing != "per_section" or i == s.current_section) else "locked")
        sections.append({"index": i, "key": sec.key, "title": sec.title, "description": sec.description,
                         "duration_minutes": sec.duration_minutes, "count": n, "state": st})
    return {
        "id": str(s.id), "title": s.title, "mode": s.mode, "status": s.status, "feedback": s.feedback,
        "timing": cfg.timing, "server_now": now.isoformat(), "started_at": s.started_at.isoformat(),
        "deadline_at": (effective_deadline(s, cfg).isoformat() if effective_deadline(s, cfg) else None),
        "current_section": s.current_section, "sections": sections, "total": len(s.items),
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None, "submit_reason": s.submit_reason,
        "score": s.score, "max_score": s.max_score, "result": s.result, "allow_review": cfg.allow_review,
        "blueprint_id": s.blueprint_id, "seed": s.seed, "items": visible,
    }


def session_summary(s: ExamSession) -> dict:
    answered = sum(1 for it in s.items if it.answer and not _blank(it.answer))
    return {"id": str(s.id), "title": s.title, "mode": s.mode, "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "started_at": s.started_at.isoformat(), "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "deadline_at": s.deadline_at.isoformat() if s.deadline_at else None,
            "total": len(s.items), "answered": answered, "score": s.score, "max_score": s.max_score,
            "scaled": (s.result or {}).get("scaled"), "counts": (s.result or {}).get("counts"),
            "blueprint_id": s.blueprint_id}


def _blank(a):
    from .scoring import is_blank
    return is_blank(a)
