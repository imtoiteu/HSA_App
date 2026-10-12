"""Incremental remediation: promote not-ready questions to READY only on verifiable evidence.

`hsa-app remediate <batch> [--limit N] [--apply]` (dry run without --apply). Each batch is a candidate query plus
deterministic checks against the original source document (read-only, sha256-verified by review/sources.py) and
the source's own answer key. Fixes go through the review service (draft version → QA confirmations → approval), so
every change has a version, old/new diff, provenance and audit trail, and is skipped next time while its content hash
is unchanged. Anything not provable from the source stays in its queue with the reason recorded.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session, lazyload

from ..content.render import plain_text
from ..models import Question, QuestionReview, ReviewEvent, SourceCache, User
from . import content as C
from . import service as S
from .sources import Upstream, default_store

ACTOR_EMAIL = "remediation@hsa.local"
METHOD = "source_check"
CHOSEN_RE = re.compile(r"(?:chọn(?:\s*đáp\s*án)?|đáp án đúng(?:\s*là)?|vậy đáp án(?:\s*là)?)\s*[:.]?\s*\**\s*([A-D])\b", re.I)
LABEL_RE = re.compile(r"(?:chọn|đáp án|answer)\s*(?:đúng\s*)?(?:là\s*)?[:.]?\s*\**\s*([A-D])\b", re.I)


def actor(db: Session) -> User:
    """Audit identity of automated remediation (cannot log in: inactive, unusable password)."""
    u = db.scalar(select(User).where(User.email == ACTOR_EMAIL))
    if u is None:
        u = User(email=ACTOR_EMAIL, display_name="Remediation (kiểm tra theo nguồn)", password_hash="!disabled",
                 role="approver", is_active=False)
        db.add(u)
        db.flush()
    return u


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = s.replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))  # accent-insensitive: PDF text keeps accents, but
    return re.sub(r"[^0-9a-z]+", "", s)                              # converters sometimes split combining marks


def words(s: str) -> list[str]:
    s = unicodedata.normalize("NFC", s or "").lower()
    return [w for w in re.findall(r"\w+", s) if not w.isdigit() or len(w) > 1]


def _text_without_math(nodes) -> str:
    out = []
    for n in C.walk(nodes):
        if n.get("t") == "s":
            out.append(n.get("v", ""))
        elif n.get("t") in ("m", "img", "br", "tab"):
            out.append(" ")
    return " ".join(out)


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    page: int | None = None
    details: dict = field(default_factory=dict)


def check_source_text(content: dict, pages: list[str]) -> Verdict:
    """Stem and every option must occur in the source text, options shortly after the stem."""
    full = [norm(p) for p in pages]
    joined = "".join(full)
    starts, acc = [], 0
    for p in full:
        starts.append(acc)
        acc += len(p)
    stem = norm(_text_without_math(content.get("stem")))
    if len(stem) < 12:
        return Verdict(False, "stem text too short to locate in the source")
    # locate the stem by its longest chunks (formulas/images are not in the text layer)
    chunks = [c for c in re.split(r"\s{2,}| ", _text_without_math(content.get("stem"))) if c]
    probe = norm(" ".join(chunks[:12]))[:60] or stem[:60]
    pos = joined.find(probe)
    if pos < 0:
        probe = stem[:40]
        pos = joined.find(probe)
    if pos < 0:
        return Verdict(False, "stem not found in the source document text")
    if joined.find(probe, pos + 1) >= 0:
        dup = True
    else:
        dup = False
    # whole stem text (as one string, math removed) must be present in order
    pieces = [norm(x) for x in re.split(r"[\n]+", _text_without_math(content.get("stem")))]
    pieces = [x for x in pieces if len(x) >= 4]
    cur = pos
    for piece in pieces:
        k = joined.find(piece[:80], cur)
        if k < 0 or k - cur > 3000:
            return Verdict(False, f"stem fragment not found after its start: {piece[:40]}")
        cur = k
    window = joined[pos:pos + 6000]
    missing = []
    for o in content.get("options") or []:
        t = norm(_text_without_math(o.get("content")))
        if t and t not in window:
            missing.append(o.get("label"))
    if missing:
        return Verdict(False, f"options {','.join(missing)} not found after the stem in the source")
    page = next((i + 1 for i, s in enumerate(starts) if s <= pos < s + len(full[i])), None)
    return Verdict(True, "stem and options match the source text" + (" (stem text occurs twice)" if dup else ""),
                   page, {"dup_stem_in_doc": dup})


def check_answer(q: Question, answer: dict | None, content: dict, up: Upstream) -> Verdict:
    if not answer or answer.get("kind") != "choice":
        return Verdict(False, "no choice answer")
    if q.answer_source_type not in ("SOURCE_PROVIDED_ANSWER", "SOURCE_PROVIDED_SOLUTION"):
        return Verdict(False, f"answer not source-provided ({q.answer_source_type})")
    labels = answer["labels"]
    occs = up.occurrences_answers(q.external_id)
    keys = {tuple(o["labels"]) for o in occs if o["labels"]}
    if not occs or not keys:
        return Verdict(False, "no answer evidence in the source occurrences")
    if keys != {tuple(labels)}:
        return Verdict(False, f"source occurrences disagree on the key: {sorted(keys)}")
    ev = [e for o in occs for e in o["evidence"]]
    sol = plain_text(content.get("solution") or []) + " " + plain_text(content.get("explanation") or [])
    said = {m.group(1).upper() for m in CHOSEN_RE.finditer(sol)}
    if len(said) == 1 and said != set(labels):  # one explicit "Chọn X" that contradicts the key
        return Verdict(False, f"solution says {sorted(said)} but the key is {labels}")
    said = said if len(said) == 1 else set()  # several labels = a discussion of the options, not a verdict
    kinds = sorted({e.get("type", "?") for e in ev})
    return Verdict(True, f"source answer key {labels} ({', '.join(kinds)}; {len(occs)} occurrence(s) agree)"
                   + ("; solution agrees" if said else ""), details={"evidence": kinds})


def nfc_content(content: dict) -> tuple[dict, bool]:
    """Unicode NFC normalisation of every text node (deterministic, meaning-preserving)."""
    changed = False

    def fix(nodes):
        nonlocal changed
        for n in C.walk(nodes):
            if n.get("t") == "s":
                v = unicodedata.normalize("NFC", n["v"])
                if v != n["v"]:
                    n["v"], changed = v, True
    import json
    c = json.loads(json.dumps(content))
    fix([c.get("stem"), [o.get("content") for o in c.get("options") or []], c.get("group"), c.get("solution"),
         c.get("explanation")])
    return c, changed


# ------------------------------------------------------------------------------------------------
# batches (candidate queries). Every batch excludes questions under review, drafts and review decisions.
# ------------------------------------------------------------------------------------------------
BASE = """
  q.removed_upstream = false AND q.review_gate IS NULL AND r.draft_version_id IS NULL
  AND r.status IN ('none') AND r.findings_critical = 0 AND r.dirty = false
"""
BATCHES = {
    # upstream NEEDS_REVIEW only because of Unicode/label normalisation, nothing else
    "normalization_only": BASE + """
      AND q.editorial_states = '{NEEDS_REVIEW}' AND q.policy_reasons = ARRAY['state:NEEDS_REVIEW']::varchar[]
      AND q.review_flags <@ ARRAY['not_nfc_normalized','option_label_normalized']::varchar[]
      AND q.state_notes = '{}' AND q.question_type = 'single_choice' AND NOT q.has_formula
      AND q.subject_source = 'upstream_effective' AND q.classification_review IS NULL
    """,
}


def candidates(db: Session, batch: str, limit: int | None) -> list[int]:
    sql = f"SELECT q.id FROM question q JOIN question_review r ON r.question_id = q.id WHERE {BATCHES[batch]} ORDER BY q.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(db.scalars(text(sql)))


def run(db: Session, batch: str, limit: int | None = None, apply: bool = False, media_root=None,
        log=print) -> dict:
    store = default_store()
    up = store.up
    who = actor(db)
    stats = {"examined": 0, "promoted": 0, "unresolved": 0, "structural_fixed": 0, "answer_fixed": 0,
             "formula_fixed": 0, "visual_fixed": 0, "subject_fixed": 0, "source_conflicts": 0, "reasons": {}}
    page_cache: dict[str, list[str]] = {}
    for qid in candidates(db, batch, limit):
        q = db.scalar(select(Question).options(lazyload(Question.current_version)).where(Question.id == qid))
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        stats["examined"] += 1

        def unresolved(reason, conflict=False):
            stats["unresolved"] += 1
            key = reason.split(":")[0][:60]
            stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            if conflict:
                stats["source_conflicts"] += 1
            note = f"[{batch}] {reason}"
            last = db.scalar(select(ReviewEvent.note).where(ReviewEvent.question_id == q.id,
                                                            ReviewEvent.action == "remediation_skip")
                             .order_by(ReviewEvent.id.desc()).limit(1))
            if apply and last != note:  # the reason is recorded once, not on every run
                db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id,
                                   action="remediation_skip", changes=[], note=note))
                db.commit()
            log(f"  SKIP {q.external_id}: {reason}")

        doc = (q.provenance or {}).get("document_id")
        sc = db.get(SourceCache, doc) if doc else None
        if not sc or sc.status != "ready" or not sc.blob_sha256:
            unresolved(f"source not available ({sc.status if sc else 'none'})")
            continue
        if sc.blob_sha256 not in page_cache:
            page_cache[sc.blob_sha256] = store.page_texts(sc.blob_sha256)
        content, nfc_changed = nfc_content(v.content)
        sv = check_source_text(content, page_cache[sc.blob_sha256])
        if not sv.ok:
            unresolved(sv.reason, conflict=True)
            continue
        av = check_answer(q, v.answer, content, up)
        if not av.ok:
            unresolved(av.reason, conflict="disagree" in av.reason or "says" in av.reason)
            continue
        if not apply:
            stats["promoted"] += 1
            log(f"  OK   {q.external_id} p.{sv.page}: {sv.reason}; {av.reason}" + ("; NFC fix" if nfc_changed else ""))
            continue
        ref = {"document_id": doc, "page": sv.page, "blob_sha256": sc.blob_sha256, "method": METHOD, "batch": batch}
        if nfc_changed:
            S.save_edit(db, q, who, {"stem": content["stem"], "options": content["options"],
                                     "solution": content.get("solution"), "explanation": content.get("explanation")},
                        note=f"[{batch}] chuẩn hoá Unicode NFC (không đổi nội dung)", source_ref=ref, media_root=media_root)
            stats["structural_fixed"] += 1
            v = S.working_version(db, q, r)
        has_vis = bool(C.images(v.content)) or C.has_table(v.content)
        notes = {
            "content": f"[{batch}] {sv.reason} (trang {sv.page})",
            "answer": f"[{batch}] {av.reason}",
            "formula": f"[{batch}] không có công thức",
            "visual": f"[{batch}] hình/bảng nhúng trong tài liệu gốc, tệp tồn tại" if has_vis else f"[{batch}] không có hình",
            "subject": f"[{batch}] phân loại cuối của ngân hàng: {q.upstream_effective_subject}",
            "source": f"[{batch}] {doc} trang {sv.page}, sha256 {sc.blob_sha256[:12]} đã xác minh",
        }
        for dim in C.DIMENSIONS:
            state = "ok"
            if dim in ("formula",) and not C.collect_tex(v.content):
                state = "na"
            if dim == "visual" and not has_vis:
                state = "na"
            S.confirm(db, q, who, dim, state, notes[dim], media_root=media_root, method=METHOD)
        try:
            res = S.approve(db, q, who, f"[{batch}] tự động theo nguồn: nội dung + đáp án khớp tài liệu gốc",
                            media_root=media_root)
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"approval refused: {ex}")
            continue
        db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id, action="remediation",
                           changes=[], note=f"[{batch}] promoted", source_ref=ref))
        db.commit()
        stats["promoted"] += res["gate"] == "ready"
        log(f"  READY {q.external_id} served={res['served']} reasons={res['reasons']}")
    if apply:
        db.commit()
    return stats
