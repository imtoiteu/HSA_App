"""Review workflow (docs/REVIEW_STUDIO.md).

* Edits never touch the served content directly: they create an immutable `question_version` (origin=review)
  that becomes the question's *draft*. Students keep receiving the current version until the draft is approved.
* QA dimensions are confirmed against content-part hashes (review/content.py); an edit invalidates only the
  dimensions whose parts changed.
* Approval (approver/admin) requires every dimension ok/not-required and no open critical finding; it publishes
  the draft (current_version_id) and sets question.review_gate = 'ready', which the serving policy treats as
  READY_TO_SERVE. Every other policy check (content flags, type, scoring) still applies.
* A dimension marked "fail" sets review_gate = 'blocked' (never served, even if upstream says READY).
* Exam history is untouched: sessions reference the immutable versions they used.
"""
import datetime as dt
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..commerce import access as plans_access
from ..content.render import plain_text
from ..models import (Asset, FormulaCheck, Question, QuestionBank, QuestionReview, QuestionVersion, ReviewEvent,
                      ReviewFinding, Subject, User)
from ..settings_store import get_setting
from ..sync.importer import bank_alias, formula_flags, tex_sha
from ..sync.policy import PolicyInput, effective_served, evaluate
from . import content as C
from . import validators as V

CONFLICT_FLAGS = {"answer_conflict_across_occurrences", "multiple_options_highlighted", "answer_not_in_options"}
INCOMPLETE_FLAGS = {"empty_stem", "too_few_options", "empty_option", "group_context_missing", "answer_empty",
                    "no_input"}
QUEUES = ["NEEDS_FORMULA_REVIEW", "NEEDS_ANSWER_LINKING", "NEEDS_VISUAL_REVIEW", "SUBJECT_REVIEW", "UNCLASSIFIED",
          "GENERAL_REVIEW", "ANSWER_CONFLICT", "INCOMPLETE", "VALIDATOR", "UPSTREAM_CHANGED", "DRAFT", "RETURNED",
          "READY", "NOT_READY"]

# who may do what (app_user.role)
PERMS = {
    "view": {"admin", "editor", "reviewer", "approver"},
    "edit": {"admin", "editor", "approver"},
    "confirm": {"admin", "reviewer", "approver"},
    "approve": {"admin", "approver"},
    "assign": {"admin", "approver"},
    "ai": {"admin", "editor", "reviewer", "approver"},
}


class ReviewError(Exception):
    def __init__(self, message, status=409, code="review_conflict", data=None):
        super().__init__(message)
        self.status, self.code, self.data = status, code, data


def can(user: User, perm: str) -> bool:
    return user.role in PERMS[perm]


def now():
    return dt.datetime.now(dt.timezone.utc)


# ------------------------------------------------------------------------------------------------
# state helpers
# ------------------------------------------------------------------------------------------------
def get_review(db: Session, q: Question, lock=False) -> QuestionReview:
    st = select(QuestionReview).where(QuestionReview.question_id == q.id)
    r = db.scalar(st.with_for_update() if lock else st)
    if r is None:
        r = QuestionReview(question_id=q.id, dims={}, hashes={}, queues=[], dim_state={})
        db.add(r)
        db.flush()
    return r


def working_version(db: Session, q: Question, r: QuestionReview) -> QuestionVersion | None:
    if r.draft_version_id:
        return db.get(QuestionVersion, r.draft_version_id)
    return db.get(QuestionVersion, q.current_version_id) if q.current_version_id else None


def parts_of(q: Question, v: QuestionVersion | None) -> dict:
    if v is None:
        return {}
    return C.part_hashes(v.content, v.answer, q.subject_code, q.provenance)


def dim_view(q: Question, r: QuestionReview, v: QuestionVersion | None) -> dict:
    parts = parts_of(q, v)
    req = C.required_dims(v.content if v else {}, q.editorial_states)
    status = C.dim_status(r.dims or {}, parts, req) if v else {d: "pending" for d in C.DIMENSIONS}
    return {"status": status, "required": req, "parts": parts, "complete": C.dims_complete(status)}


def effective_ready(q: Question) -> bool:
    return q.review_gate == "ready" or (q.editorial_state == "READY_TO_SERVE" and q.review_gate is None)


def compute_gate(q: Question, r: QuestionReview, view: dict) -> str | None:
    status = view["status"]
    if any(s == "fail" for s in status.values()):
        return "blocked"
    if r.status == "approved" and not r.draft_version_id and view["complete"] and r.findings_critical == 0:
        return "ready"
    if r.draft_version_id and q.review_gate == "ready":
        return "ready"  # the published (approved) version keeps serving while a new draft is reviewed
    return None


def queues_for(q: Question, r: QuestionReview, view: dict) -> list[str]:
    st, states = view["status"], set(q.editorial_states or [])
    out = []
    pend = lambda d: st.get(d) not in ("ok", "na")  # noqa: E731
    if "NEEDS_FORMULA_REVIEW" in states and pend("formula"):
        out.append("NEEDS_FORMULA_REVIEW")
    if "NEEDS_ANSWER_LINKING" in states and pend("answer"):
        out.append("NEEDS_ANSWER_LINKING")
    if "NEEDS_VISUAL_REVIEW" in states and pend("visual"):
        out.append("NEEDS_VISUAL_REVIEW")
    if q.classification_review and pend("subject"):
        out.append("SUBJECT_REVIEW")
    if q.subject_source == "unclassified" and pend("subject"):
        out.append("UNCLASSIFIED")
    if "NEEDS_REVIEW" in states and pend("content"):
        out.append("GENERAL_REVIEW")
    if set(q.review_flags or []) & CONFLICT_FLAGS and pend("answer"):
        out.append("ANSWER_CONFLICT")
    if set(q.content_flags or []) & INCOMPLETE_FLAGS:
        out.append("INCOMPLETE")
    if r.findings_critical:
        out.append("VALIDATOR")
    if r.upstream_changed:
        out.append("UPSTREAM_CHANGED")
    if r.draft_version_id:
        out.append("DRAFT")
    if r.status == "returned":
        out.append("RETURNED")
    out.append("READY" if effective_ready(q) else "NOT_READY")
    return out


def priority_for(q: Question, r: QuestionReview, view: dict) -> int:
    """Higher = review first: questions one step from READY, with an answer and a subject, and problems on
    questions students already receive."""
    blockers = [s for s in (q.editorial_states or []) if s != "READY_TO_SERVE"]
    p = 0
    if len(blockers) == 1:
        p += 40
    if q.answer_kind:
        p += 20
    if q.subject_source not in ("unclassified", None):
        p += 10
    if q.is_served and (r.findings_critical or r.findings_open):
        p += 30
    if r.draft_version_id:
        p += 15
    p -= min(r.findings_critical, 3) * 5
    return p


def apply_policy(db: Session, q: Question):
    policy = get_setting(db, "serving_policy")
    ok, reasons = evaluate(PolicyInput(q.editorial_state, q.question_type, q.scoring_mode, q.content_flags,
                                       q.removed_upstream, q.review_gate), policy)
    bank = db.get(QuestionBank, q.bank_id)
    if bank is not None and not bank.is_active:
        ok, reasons = False, reasons + ["bank_inactive"]
    served = effective_served(ok, q.admin_override) and (bank is None or bank.is_active)
    changed = (q.policy_eligible, q.is_served) != (ok, served)
    q.policy_eligible, q.policy_reasons, q.is_served = ok, reasons, served
    if changed:
        plans_access.clear_cache()
        db.execute(update(QuestionReview).where(QuestionReview.question_id == q.id).values(served=served))


def derive_fields(db: Session, q: Question, v: QuestionVersion, failing: set | None = None):
    """Question fields derived from a (published) content version."""
    c, a = v.content, v.answer
    q.has_image = bool(C.images(c))
    q.has_table = C.has_table(c)
    q.has_formula = bool(C.collect_tex(c))
    q.question_type = c.get("type") or q.question_type
    q.answer_kind = a["kind"] if a else None
    from ..content import answers as A
    inp = c.get("input") or {"kind": "none"}
    mode = A.scoring_mode(inp, a, bool(c.get("solution") or c.get("explanation")))
    policy = get_setting(db, "serving_policy")
    if q.answer_kind == "text" and policy.get("text_answers_auto_scored"):
        mode = "auto"
    q.scoring_mode = mode
    if failing is None:
        failing = set(db.scalars(select(FormulaCheck.tex_sha).where(FormulaCheck.ok.is_(False))))
    keep = [f for f in q.content_flags or [] if f not in C.CONTENT_FLAGS
            and f not in ("formula_render_error", "solution_formula_render_error")]
    q.content_flags = sorted(set(keep) | C.derived_flags(c, a) | set(formula_flags(c, failing)))


def mark_upstream_changed(db: Session, q: Question, reviewed: bool) -> bool:
    r = db.scalar(select(QuestionReview).where(QuestionReview.question_id == q.id))
    if r is None or not (reviewed or r.draft_version_id):
        return False
    r.upstream_changed = True
    r.dirty = True
    db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, action="upstream_changed",
                       changes=[], note="Ngân hàng nguồn đã cập nhật câu này; nội dung đã duyệt được giữ nguyên.",
                       to_version_id=q.upstream_version_id))
    return True


# ------------------------------------------------------------------------------------------------
# validation + queues (incremental)
# ------------------------------------------------------------------------------------------------
def _fingerprint(q: Question, content_hash: str) -> str:
    return C.h([content_hash, q.editorial_state, sorted(q.editorial_states or []), sorted(q.policy_reasons or []),
                q.group_key, q.subject_source, sorted(q.review_flags or []), q.review_gate])


def refresh(db: Session, q: Question, r: QuestionReview | None = None, *, media_root: Path | None = None,
            failing: set | None = None, force: bool = False, dup_lookup=None, findings: list | None = None) -> dict:
    """Re-derive one question's review state. Validators run only when the content/metadata fingerprint or the
    validator version changed (or force). `findings` = the question's existing finding rows (bulk prefetch)."""
    r = r or get_review(db, q)
    v = working_version(db, q, r)
    view = dim_view(q, r, v)
    if findings is None:
        db.flush()  # rows added by an earlier refresh in this transaction (sessions are autoflush=False)
        findings = list(db.scalars(select(ReviewFinding).where(ReviewFinding.question_id == q.id)))
    bank = db.get(QuestionBank, q.bank_id)
    sync_columns(q, r, v, active=bool(bank.is_active) if bank is not None else True)
    if v is not None:
        r.content_hash = C.semantic_hash(view["parts"])
        fp = _fingerprint(q, r.content_hash)
        if force or r.validated_hash != fp or r.validator_version != V.VALIDATOR_VERSION:
            ex, near = V.duplicate_keys(v.content)
            r.dup_exact = C.h(ex)[:64] if ex else None
            r.dup_near = C.h(near)[:64] if near else None
            found = V.validate(q, v.content, v.answer, media_root, failing, tex_sha)
            if r.upstream_changed:
                found.append(V.Finding("upstream_changed", "warning", "general"))
            found += _duplicate_findings(db, q, r, dup_lookup)
            findings = _store_findings(db, q, r, found, view["parts"], findings)
            r.validated_hash, r.validator_version, r.validated_at = fp, V.VALIDATOR_VERSION, now()
    open_ = [f for f in findings if f.status == "open"]
    r.findings_open = len(open_)
    r.findings_critical = sum(1 for f in open_ if f.severity == "critical")
    gate = compute_gate(q, r, view)
    if gate != q.review_gate:
        q.review_gate = gate
        apply_policy(db, q)
    r.gate = q.review_gate or ""
    r.dim_state = view["status"]
    r.queues = queues_for(q, r, view)
    r.priority = priority_for(q, r, view)
    r.dirty = False
    return view


def sync_columns(q: Question, r: QuestionReview, v: QuestionVersion | None, active: bool | None = None):
    """Copy the question fields the queue filters/sorts on (the queue never scans the wide question table)."""
    p = q.provenance or {}
    num = p.get("source_question_number")
    r.external_id = q.external_id
    r.subject_code = q.subject_code or ""
    r.qtype = q.question_type or ""
    r.served = bool(q.is_served)
    r.state = q.editorial_state or ""
    r.gate = q.review_gate or ""
    if active is not None:
        r.active = active and not q.removed_upstream
    elif q.removed_upstream:
        r.active = False
    r.doc_id = (p.get("document_id") or "")[:40]
    r.doc_path = (p.get("document_path") or "")[-200:]
    r.source_number = num if isinstance(num, int) and 0 <= num < 10**6 else 0
    if v is not None:
        c = v.content
        txt = plain_text(c.get("stem") or []) + "\n" + "\n".join(plain_text(o.get("content") or [])
                                                                  for o in c.get("options") or [])
        r.search_text = txt.lower()[:4000]


def _duplicate_findings(db, q, r, dup_lookup) -> list:
    out = []
    for key, code in ((r.dup_exact, "exact_duplicate"), (r.dup_near, "near_duplicate")):
        if not key:
            continue
        col = QuestionReview.dup_exact if code == "exact_duplicate" else QuestionReview.dup_near
        if dup_lookup is not None:
            others = [x for x in dup_lookup(code, key) if x != q.id][:5]
        else:
            others = list(db.scalars(select(QuestionReview.question_id).where(col == key,
                                                                              QuestionReview.question_id != q.id)
                                     .limit(5)))
        if others:
            ext = dup_lookup("ext", others) if dup_lookup is not None else \
                list(db.scalars(select(Question.external_id).where(Question.id.in_(others))))
            out.append(V.Finding(code, "warning", "content", ", ".join(ext)))
            if code == "exact_duplicate":
                break  # an exact duplicate is also a near one; report once
    return out


def _store_findings(db, q, r, found, parts, rows) -> list:
    """Upsert findings; returns every finding row of the question (no flush needed by the caller)."""
    existing = {f.code: f for f in rows}
    t = now()
    seen = set()
    out = list(rows)
    for f in found:
        if f.code in seen:
            continue
        seen.add(f.code)
        ph = C.dim_hash(f.dimension, parts) if f.dimension in C.DIM_PARTS else r.content_hash
        row = existing.get(f.code)
        if row is None:
            row = ReviewFinding(question_id=q.id, code=f.code, severity=f.severity, dimension=f.dimension,
                                detail=f.detail[:2000], part_hash=ph, validator_version=V.VALIDATOR_VERSION,
                                status="open", updated_at=t)
            db.add(row)
            out.append(row)
            continue
        row.severity, row.dimension, row.detail, row.validator_version = f.severity, f.dimension, f.detail[:2000], \
            V.VALIDATOR_VERSION
        if row.status == "dismissed" and row.part_hash == ph:
            continue  # dismissal stands while the content it was about is unchanged
        if row.status in ("fixed", "dismissed") or (row.status == "accepted" and row.part_hash != ph):
            row.status, row.reason, row.resolved_by, row.resolved_at = "open", None, None, None
        row.part_hash, row.updated_at = ph, t
    for code, row in existing.items():
        if code not in seen and row.status in ("open", "accepted"):
            row.status, row.resolved_at, row.updated_at = "fixed", t, t
            row.reason = "Không còn phát hiện sau khi cập nhật nội dung"
    return out


# ------------------------------------------------------------------------------------------------
# history
# ------------------------------------------------------------------------------------------------
def event(db, q, user, action, changes=None, note=None, source_ref=None, from_v=None, to_v=None):
    db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=user.id if user else None,
                       action=action, changes=changes or [], note=note, source_ref=source_ref,
                       from_version_id=from_v, to_version_id=to_v))


def _txt(blocks) -> str:
    return plain_text(blocks or [])[:4000]


def diff_versions(old: QuestionVersion | None, new_content: dict, new_answer) -> list[dict]:
    oc, oa = (old.content, old.answer) if old else ({}, None)
    ch = []
    if oc.get("type") != new_content.get("type"):
        ch.append({"field": "type", "old": oc.get("type"), "new": new_content.get("type")})
    for k in ("stem", "solution", "explanation"):
        if (oc.get(k) or None) != (new_content.get(k) or None):
            ch.append({"field": k, "old": _txt(oc.get(k)), "new": _txt(new_content.get(k))})
    oo = {o["label"]: o["content"] for o in oc.get("options") or []}
    no = {o["label"]: o["content"] for o in new_content.get("options") or []}
    for lab in sorted(set(oo) | set(no)):
        if oo.get(lab) != no.get(lab):
            ch.append({"field": f"option:{lab}", "old": _txt(oo.get(lab)) if lab in oo else None,
                       "new": _txt(no.get(lab)) if lab in no else None})
    if (oc.get("group") or None) != (new_content.get("group") or None):
        og, ng = oc.get("group") or {}, new_content.get("group") or {}
        ch.append({"field": "group", "old": (og.get("key"), _txt(og.get("passage"))) if og else None,
                   "new": (ng.get("key"), _txt(ng.get("passage"))) if ng else None})
    if oa != new_answer:
        ch.append({"field": "answer", "old": oa, "new": new_answer})
    if C.images(oc) != C.images(new_content):
        ch.append({"field": "images", "old": C.images(oc), "new": C.images(new_content)})
    if sorted(C.collect_tex(oc)) != sorted(C.collect_tex(new_content)):
        ch.append({"field": "formula", "old": sorted(C.collect_tex(oc))[:50], "new": sorted(C.collect_tex(new_content))[:50]})
    return ch


# ------------------------------------------------------------------------------------------------
# actions
# ------------------------------------------------------------------------------------------------
def _known_src(db):
    cache = {}

    def known(src: str) -> bool:
        if src not in cache:
            sha = src.split("/")[1].split(".")[0]
            cache[src] = db.get(Asset, sha) is not None
        return cache[src]
    return known


def _new_version(db, q, content, answer, user, note) -> QuestionVersion:
    vh = C.version_hash(content, answer)
    existing = db.scalar(select(QuestionVersion).where(QuestionVersion.question_id == q.id,
                                                       QuestionVersion.content_hash == vh))
    if existing is not None:
        return existing
    vno = (db.scalar(select(func.max(QuestionVersion.version_no)).where(QuestionVersion.question_id == q.id)) or 0) + 1
    v = QuestionVersion(question_id=q.id, version_no=vno, content_hash=vh, content=content, answer=answer,
                        source_revision=f"review:{user.id}", origin="review", created_by=user.id, note=note)
    db.add(v)
    db.flush()
    return v


def _set_draft(db, q, r, v: QuestionVersion, user):
    if v.id == q.current_version_id:
        r.draft_version_id = None
    else:
        r.draft_version_id = v.id
        if r.status in ("approved", "reviewed", "none", "returned"):
            r.status = "draft"
    r.updated_by, r.updated_at = user.id, now()


def save_edit(db: Session, q: Question, user: User, edit: dict, note: str | None = None,
              source_ref: dict | None = None, media_root: Path | None = None,
              tex_checks: dict | None = None, apply_group: bool = False) -> dict:
    r = get_review(db, q, lock=True)
    base = working_version(db, q, r)
    if base is None:
        raise ReviewError("Câu hỏi chưa có nội dung.", 409)
    try:
        content = C.build_content(base.content, edit, _known_src(db))
        labels = [o["label"] for o in content.get("options") or []]
        if "answer" in edit:
            answer = C.normalize_answer(content["type"], edit["answer"], labels)
        else:
            answer = base.answer
            if answer and answer.get("kind") == "choice" and any(l not in labels for l in answer["labels"]):
                raise C.ContentError("Đáp án hiện tại không còn khớp phương án sau khi sửa; hãy chọn lại đáp án.")
        C.finalize(content, answer)
    except C.ContentError as ex:
        raise ReviewError(str(ex), 422, "invalid") from ex
    _store_tex_checks(db, content, tex_checks)
    changes = diff_versions(base, content, answer)
    if not changes:
        return {"changed": False}
    v = _new_version(db, q, content, answer, user, note)
    _set_draft(db, q, r, v, user)
    event(db, q, user, "edit", changes, note, source_ref, base.id, v.id)
    refresh(db, q, r, media_root=media_root, force=True)
    group_n = 0
    if apply_group and "group" in edit and q.group_key and content.get("group"):
        group_n = _propagate_group(db, q, user, content["group"], note, media_root)
    return {"changed": True, "version_id": v.id, "changes": [c["field"] for c in changes], "group_updated": group_n}


def _propagate_group(db, q, user, group, note, media_root) -> int:
    """Apply the edited shared passage to the other questions of the group (each gets its own draft)."""
    n = 0
    for other in db.scalars(select(Question).where(Question.group_key == q.group_key, Question.id != q.id,
                                                   Question.bank_id == q.bank_id)):
        r = get_review(db, other, lock=True)
        base = working_version(db, other, r)
        if base is None or base.content.get("group") == group:
            continue
        content = dict(base.content)
        content["group"] = group
        C.finalize(content, base.answer)
        v = _new_version(db, other, content, base.answer, user, note)
        _set_draft(db, other, r, v, user)
        event(db, other, user, "edit", diff_versions(base, content, base.answer),
              (note or "") + f" (đoạn chung sửa từ {q.external_id})", None, base.id, v.id)
        refresh(db, other, r, media_root=media_root, force=True)
        n += 1
    return n


def _store_tex_checks(db, content, tex_checks):
    """KaTeX results computed in the editor with the student renderer (same library/version) for new formulas."""
    if not tex_checks:
        return
    from sqlalchemy.dialects.postgresql import insert
    texs = C.collect_tex(content)
    rows = []
    for t in texs:
        res = tex_checks.get(t)
        if res is None:
            continue
        rows.append({"tex_sha": tex_sha(t), "tex": t, "ok": bool(res.get("ok")), "error": (res.get("error") or None),
                     "renderer": "katex-review-editor"})
    if rows:
        st = insert(FormulaCheck).values(rows)
        db.execute(st.on_conflict_do_nothing(index_elements=["tex_sha"]))


def set_subject(db: Session, q: Question, user: User, code: str | None, note: str | None, media_root=None) -> dict:
    from ..sync.subjects import resolve_subject, upstream_tuple
    if code and db.get(Subject, code) is None:
        raise ReviewError("Môn học không tồn tại.", 422, "invalid")
    r = get_review(db, q, lock=True)
    before = (q.subject_code, q.subject_source)
    q.subject_override = code or None
    q.subject_override_note = note
    q.subject_code, q.subject_source = resolve_subject(q.source_subject, q.inferred_subject, q.inference_confidence,
                                                       q.subject_override, bank_alias(db, q.bank_id),
                                                       get_setting(db, "serving_policy"), upstream_tuple(q))
    if before[0] != q.subject_code:
        plans_access.clear_cache()
    event(db, q, user, "subject", [{"field": "subject", "old": before[0], "new": q.subject_code}], note)
    r.updated_by, r.updated_at = user.id, now()
    refresh(db, q, r, media_root=media_root, force=True)
    return {"subject": q.subject_code, "subject_source": q.subject_source}


def set_topic(db: Session, q: Question, user: User, topic: str | None, note: str | None) -> dict:
    old = q.topic
    q.topic = (topic or "").strip()[:200] or None
    if old != q.topic:
        event(db, q, user, "topic", [{"field": "topic", "old": old, "new": q.topic}], note)
    return {"topic": q.topic}


def confirm(db: Session, q: Question, user: User, dim: str, state: str | None, note: str | None,
            media_root=None) -> dict:
    """Confirm one QA dimension (ok | na | fail) for the working version, or clear it (state None)."""
    if dim not in C.DIMENSIONS:
        raise ReviewError("Tiêu chí không hợp lệ.", 422, "invalid")
    if state is not None and state not in C.DIM_STATES:
        raise ReviewError("Trạng thái không hợp lệ.", 422, "invalid")
    r = get_review(db, q, lock=True)
    v = working_version(db, q, r)
    if v is None:
        raise ReviewError("Câu hỏi chưa có nội dung.", 409)
    parts = parts_of(q, v)
    if state == "fail" and not (note or "").strip():
        raise ReviewError("Hãy ghi rõ lỗi khi đánh dấu không đạt.", 422, "note_required")
    dims = dict(r.dims or {})
    old = (dims.get(dim) or {}).get("s")
    if state is None:
        dims.pop(dim, None)
    else:
        dims[dim] = {"s": state, "h": C.dim_hash(dim, parts), "by": user.id, "at": now().isoformat(),
                     "note": (note or None), "v": v.id}
    r.dims = dims
    event(db, q, user, "confirm", [{"field": f"qa:{dim}", "old": old, "new": state}], note)
    view = dim_view(q, r, v)
    if view["complete"] and r.status in ("none", "draft", "returned"):
        r.status, r.reviewed_by, r.reviewed_at = "reviewed", user.id, now()
    elif not view["complete"] and r.status == "reviewed":
        r.status = "draft" if r.draft_version_id else "none"
    r.updated_by, r.updated_at = user.id, now()
    r.verified_by, r.verified_at, r.verification_method = user.id, now(), "human_review"
    refresh(db, q, r, media_root=media_root)
    return {"dims": dim_view(q, r, v)["status"], "status": r.status}


def approve(db: Session, q: Question, user: User, note: str | None, media_root=None) -> dict:
    r = get_review(db, q, lock=True)
    v = working_version(db, q, r)
    if v is None:
        raise ReviewError("Câu hỏi chưa có nội dung.", 409)
    refresh(db, q, r, media_root=media_root)
    view = dim_view(q, r, v)
    missing = [d for d, s in view["status"].items() if s not in ("ok", "na")]
    if missing:
        raise ReviewError("Chưa thể duyệt: còn tiêu chí chưa xác nhận hoặc không đạt: "
                          + ", ".join(C.DIM_LABELS[d] for d in missing), 409, "qa_incomplete", {"missing": missing})
    if r.findings_critical:
        raise ReviewError(f"Chưa thể duyệt: còn {r.findings_critical} lỗi nghiêm trọng của bộ kiểm tra tự động "
                          "(sửa hoặc bỏ qua kèm lý do).", 409, "critical_findings")
    if v.answer is None and (v.content.get("input") or {}).get("kind") not in (None, "none"):
        raise ReviewError("Chưa thể duyệt: câu hỏi chưa có đáp án.", 409, "answer_missing")
    published_from = q.current_version_id
    if r.draft_version_id:
        q.current_version_id = r.draft_version_id
        r.draft_version_id = None
        if v.answer != (db.get(QuestionVersion, published_from).answer if published_from else None):
            q.answer_source_type = "EDITORIAL_TRANSCRIBED"
        q.content_changed_at = now()
        derive_fields(db, q, v)
        event(db, q, user, "publish", [{"field": "current_version", "old": published_from, "new": v.id}], note,
              None, published_from, v.id)
    r.status, r.approved_by, r.approved_at = "approved", user.id, now()
    r.upstream_changed = False
    r.updated_by, r.updated_at = user.id, now()
    old_gate = q.review_gate
    refresh(db, q, r, media_root=media_root, force=True)
    apply_policy(db, q)
    event(db, q, user, "approve", [{"field": "review_gate", "old": old_gate, "new": q.review_gate},
                                   {"field": "served", "old": None, "new": q.is_served}], note)
    return {"status": r.status, "gate": q.review_gate, "served": q.is_served, "eligible": q.policy_eligible,
            "reasons": q.policy_reasons}


def set_status(db: Session, q: Question, user: User, action: str, note: str | None, media_root=None) -> dict:
    """return (back to the editor), reopen (withdraw an approval), discard_draft."""
    r = get_review(db, q, lock=True)
    old = r.status
    if action == "return":
        if not (note or "").strip():
            raise ReviewError("Hãy ghi lý do trả lại.", 422, "note_required")
        r.status = "returned"
    elif action == "reopen":
        r.status = "draft" if r.draft_version_id else "none"
        if q.review_gate == "ready":
            q.review_gate = None
            apply_policy(db, q)
    elif action == "discard_draft":
        if not r.draft_version_id:
            raise ReviewError("Không có bản nháp.", 409)
        event(db, q, user, "discard_draft", [{"field": "draft", "old": r.draft_version_id, "new": None}], note,
              None, r.draft_version_id, q.current_version_id)
        r.draft_version_id = None
        r.status = "none" if old in ("draft", "reviewed") else old
    else:
        raise ReviewError("Thao tác không hợp lệ.", 422, "invalid")
    r.updated_by, r.updated_at = user.id, now()
    event(db, q, user, action, [{"field": "status", "old": old, "new": r.status}], note)
    refresh(db, q, r, media_root=media_root)
    return {"status": r.status, "gate": q.review_gate}


def restore(db: Session, q: Question, user: User, version_id: int, note: str | None, media_root=None) -> dict:
    """Make an earlier version the draft again (published only through approval)."""
    v = db.get(QuestionVersion, version_id)
    if v is None or v.question_id != q.id:
        raise ReviewError("Không tìm thấy phiên bản.", 404, "not_found")
    r = get_review(db, q, lock=True)
    base = working_version(db, q, r)
    _set_draft(db, q, r, v, user)
    event(db, q, user, "restore", diff_versions(base, v.content, v.answer), note, None,
          base.id if base else None, v.id)
    refresh(db, q, r, media_root=media_root, force=True)
    return {"draft_version_id": r.draft_version_id, "status": r.status}


def resolve_finding(db: Session, q: Question, user: User, finding_id: int, action: str, reason: str | None,
                    media_root=None) -> dict:
    f = db.get(ReviewFinding, finding_id)
    if f is None or f.question_id != q.id:
        raise ReviewError("Không tìm thấy phát hiện.", 404, "not_found")
    if action == "dismiss" and not (reason or "").strip():
        raise ReviewError("Bỏ qua cần ghi lý do.", 422, "note_required")
    old = f.status
    f.status = {"dismiss": "dismissed", "accept": "accepted", "reopen": "open"}.get(action)
    if f.status is None:
        raise ReviewError("Thao tác không hợp lệ.", 422, "invalid")
    f.reason, f.resolved_by, f.resolved_at, f.updated_at = reason, user.id, now(), now()
    if f.status == "accepted":
        f.reason = reason or "Đã xác nhận lỗi, cần sửa"
    db.flush()
    event(db, q, user, "finding", [{"field": f"finding:{f.code}", "old": old, "new": f.status}], reason)
    r = get_review(db, q)
    refresh(db, q, r, media_root=media_root)
    return {"id": f.id, "status": f.status}


def assign(db: Session, user: User, qids: list[int], reviewer_id: int | None, assignment_id: int | None = None) -> int:
    if reviewer_id is not None:
        rv = db.get(User, reviewer_id)
        if rv is None or not can(rv, "view"):
            raise ReviewError("Người được giao phải là biên tập viên/người duyệt.", 422, "invalid")
    n = db.execute(update(QuestionReview).where(QuestionReview.question_id.in_(qids)).values(
        assignee_id=reviewer_id, assignment_id=assignment_id if reviewer_id else None,
        assigned_at=now() if reviewer_id else None)).rowcount
    for qid, ext in db.execute(select(Question.id, Question.external_id).where(Question.id.in_(qids))):
        db.add(ReviewEvent(question_id=qid, external_id=ext, actor_id=user.id, action="assign",
                           changes=[{"field": "assignee", "old": None, "new": reviewer_id}]))
    return n
