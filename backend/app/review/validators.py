"""Deterministic (local, no AI) validators. They run on a question's working version and return findings that
feed the review queues; reviewers accept, fix or dismiss them in the Review Studio.

Bump VALIDATOR_VERSION whenever a check changes: questions validated by an older version are re-checked by
`hsa-app review-refresh`; unchanged content validated by the current version is skipped.
"""
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..content.render import plain_text
from ..sync.subjects import collect_tex
from .content import images, walk

VALIDATOR_VERSION = 1


@dataclass
class Finding:
    code: str
    severity: str  # critical | warning | info
    dimension: str  # content | answer | formula | visual | subject | source | general
    detail: str = ""


LABELS = {
    "cq_id_format": "Mã câu hỏi (cq_id) sai định dạng",
    "empty_stem": "Đề bài trống",
    "too_few_options": "Câu trắc nghiệm có ít hơn 2 phương án",
    "option_labels": "Nhãn phương án trùng hoặc không liên tiếp",
    "empty_option": "Có phương án trống",
    "duplicate_option": "Hai phương án giống hệt nhau",
    "answer_missing": "Chưa có đáp án",
    "answer_not_in_options": "Đáp án không thuộc các phương án",
    "answer_malformed": "Đáp án sai cấu trúc",
    "missing_asset": "Hình không tồn tại trong kho media",
    "broken_image_ref": "Tham chiếu hình hỏng",
    "unresolved_placeholder": "Còn chỗ không hiển thị được (⚠) hoặc mảnh công thức dạng ảnh chưa xác nhận",
    "raw_latex_in_text": "Văn bản có ký hiệu LaTeX chưa được đặt trong công thức",
    "malformed_latex": "Công thức LaTeX sai cú pháp",
    "formula_render_error": "Công thức không hiển thị được trên web (KaTeX)",
    "ocr_garbage": "Văn bản có dấu hiệu lỗi OCR/mã hoá",
    "group_passage_missing": "Câu thuộc nhóm nhưng đoạn dữ liệu chung trống",
    "group_key_mismatch": "Liên kết nhóm (group) không khớp",
    "subject_missing": "Chưa phân loại môn",
    "ready_inconsistent": "READY nhưng còn cờ QA/trạng thái chưa xử lý",
    "solution_contains_next_question": "Lời giải có vẻ chứa nội dung câu tiếp theo",
    "exact_duplicate": "Trùng hoàn toàn với câu khác",
    "near_duplicate": "Gần trùng với câu khác",
    "upstream_changed": "Nguồn (HSA-question-bank) đã thay đổi sau khi biên tập",
}
CRITICAL_CODES = {k for k in LABELS if k not in {
    "duplicate_option", "raw_latex_in_text", "ocr_garbage", "group_key_mismatch", "subject_missing",
    "ready_inconsistent", "solution_contains_next_question", "exact_duplicate", "near_duplicate",
    "upstream_changed"}}

CHOICE_INPUT = "choice"
MOJIBAKE = re.compile(r"(Ã[\x80-\xbf¡-ÿ]|Â[\x80-\xbf ]|Ä[\x80-\xbf‘’]|áº|á»|Æ°|â€[™œ\x9d“”])")
RAW_LATEX = re.compile(r"(\\(frac|sqrt|left|right|int|sum|lim|alpha|beta|pi|Delta|cdot|times|le|ge|neq|vec|overline)\b"
                       r"|\$[^$\n]{1,80}\$|\^\{|_\{|\\\(|\\\[)")
NEXT_Q = re.compile(r"(?:^|\n|\s)(?:Câu|Question)\s+\d{1,3}\s*[:.)]\s+\S.{10,}?(?:\n|\s)A[.)]\s", re.S)
HEADING = re.compile(r"\b(KIỂM TRA|ĐỀ THI|ĐỀ KIỂM TRA|PHẦN\s+[IVX]+\b|HẾT\s*-*$|-{3,}\s*HẾT)", re.M)
ENV_RE = re.compile(r"\\(begin|end)\{([a-zA-Z*]+)\}")


def _text(nodes) -> str:
    return plain_text(nodes or [])


def latex_problem(tex: str) -> str | None:
    """Structural LaTeX check (KaTeX itself runs in the browser/CI check; this catches the obvious)."""
    depth, i = 0, 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return "dư dấu }"
        i += 1
    if depth:
        return "thiếu dấu }"
    lefts = len(re.findall(r"\\left(?![a-zA-Z])", tex))
    rights = len(re.findall(r"\\right(?![a-zA-Z])", tex))
    if lefts != rights:
        return "\\left/\\right không cân"
    stack = []
    for kind, env in ENV_RE.findall(tex):
        if kind == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            return f"\\end{{{env}}} không khớp"
    if stack:
        return f"thiếu \\end{{{stack[-1]}}}"
    if "$" in tex.replace("\\$", ""):
        return "có ký hiệu $ bên trong công thức"
    return None


def ocr_suspect(text: str) -> str | None:
    if not text:
        return None
    if "\ufffd" in text:
        return "ký tự thay thế �"
    if MOJIBAKE.search(text):
        return "chuỗi mojibake (sai bảng mã)"
    if unicodedata.normalize("NFC", text) != text:
        return "chưa chuẩn hoá Unicode NFC"
    letters = sum(ch.isalpha() for ch in text)
    if len(text) >= 40:
        odd = sum(1 for ch in text if not (ch.isalnum() or ch.isspace() or ch in ".,;:!?()[]{}-–—'\"“”‘’/%+=<>*…°"))
        if odd / len(text) > 0.25:
            return "tỉ lệ ký tự lạ cao"
        if letters and re.search(r"(?:\b\w\b\s){8,}", text):
            return "nhiều ký tự rời rạc"
    return None


def validate(q, content: dict, answer: dict | None, media_root: Path | None, failing_tex: set | None = None,
             tex_sha=None) -> list[Finding]:
    """q: Question row (metadata); content/answer: the working version."""
    out: list[Finding] = []
    add = lambda code, dim, detail="": out.append(  # noqa: E731
        Finding(code, "critical" if code in CRITICAL_CODES else "warning", dim, detail))

    if q.bank_id and q.external_id.startswith("cq_") and not re.fullmatch(r"cq_[0-9a-z_]+", q.external_id):
        add("cq_id_format", "source", q.external_id)

    stem = content.get("stem") or []
    options = content.get("options") or []
    inp = content.get("input") or {}
    stem_txt = _text(stem)
    if not stem_txt.strip() and not any(b.get("t") in ("img", "table") for b in stem):
        add("empty_stem", "content")

    if inp.get("kind") == CHOICE_INPUT:
        if len(options) < 2:
            add("too_few_options", "content", f"{len(options)} phương án")
        labels = [o.get("label") for o in options]
        expected = [chr(65 + i) for i in range(len(labels))]
        if len(set(labels)) != len(labels) or labels != expected:
            add("option_labels", "content", " ".join(map(str, labels)))
        texts = {}
        for o in options:
            t = _text(o.get("content")).strip()
            if not t and not o.get("content"):
                add("empty_option", "content", o.get("label", ""))
            key = re.sub(r"\s+", " ", t).lower()
            if key and not any(n.get("t") == "img" for n in walk(o.get("content"))) and key in texts:
                add("duplicate_option", "content", f"{texts[key]} = {o.get('label')}")
            texts.setdefault(key, o.get("label"))

    # --- answer
    qtype = content.get("type")
    if answer is None:
        if qtype not in ("constructed_response", "open_or_unknown"):
            add("answer_missing", "answer")
    else:
        kind = answer.get("kind")
        if kind == "choice":
            labels = {o.get("label") for o in options}
            bad = [l for l in answer.get("labels") or [] if l not in labels]
            if bad or not answer.get("labels"):
                add("answer_not_in_options", "answer", ", ".join(bad) or "trống")
            elif qtype == "single_choice" and len(answer["labels"]) > 1:
                add("answer_malformed", "answer", "câu một lựa chọn có nhiều đáp án")
        elif kind == "tf_sequence":
            n = inp.get("n")
            if not answer.get("values") or (n and n != len(answer["values"])):
                add("answer_malformed", "answer", f"{len(answer.get('values') or [])} giá trị cho {n} ý")
        elif kind == "numeric":
            v = answer.get("value")
            if not isinstance(v, (int, float)) or v != v:
                add("answer_malformed", "answer", "đáp số không hợp lệ")
        elif kind == "text":
            if not (answer.get("text") or "").strip():
                add("answer_malformed", "answer", "đáp án chữ trống")
        elif kind != "boolean":
            add("answer_malformed", "answer", f"dạng {kind}")

    # --- images
    for n in walk([stem, [o.get("content") for o in options], content.get("group"), content.get("solution"),
                   content.get("explanation")]):
        if n.get("t") != "img":
            continue
        src = n.get("src") or ""
        if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{64}\.[a-z]{3,4}", src):
            add("broken_image_ref", "visual", src[:120])
        elif media_root is not None and not (Path(media_root) / src).exists():
            add("missing_asset", "visual", src)

    # --- placeholders / raster fragments the student would see
    shown = [stem, [o.get("content") for o in options], content.get("group")]
    warn_n = sum(1 for n in walk(shown) if n.get("t") == "warn" or n.get("review"))
    if warn_n:
        dim = "formula" if any(n.get("review") and n.get("t") in ("img", "m") for n in walk(shown)) else "visual"
        add("unresolved_placeholder", dim, f"{warn_n} chỗ")

    # --- formulas
    texs = collect_tex(content)
    bad_tex = [(t, p) for t in texs if (p := latex_problem(t))]
    if bad_tex:
        add("malformed_latex", "formula", "; ".join(f"{p}: {t[:60]}" for t, p in bad_tex[:3]))
    if failing_tex and tex_sha:
        failing = [t for t in texs if tex_sha(t) in failing_tex]
        if failing:
            add("formula_render_error", "formula", "; ".join(t[:60] for t in failing[:3]))
    raw_hits = []
    for n in walk(shown + [content.get("solution")]):
        if n.get("t") == "s" and RAW_LATEX.search(n.get("v") or ""):
            raw_hits.append(n["v"][:60])
    if raw_hits:
        add("raw_latex_in_text", "formula", raw_hits[0])

    # --- OCR / encoding
    all_text = "\n".join([stem_txt] + [_text(o.get("content")) for o in options])
    why = ocr_suspect(all_text)
    if why:
        add("ocr_garbage", "content", why)

    # --- group linkage
    g = content.get("group")
    if q.group_key and not g:
        add("group_key_mismatch", "content", f"thuộc nhóm {q.group_key} nhưng không có đoạn chung")
    if g is not None:
        if not g.get("passage") and not g.get("instruction_only"):
            add("group_passage_missing", "content", g.get("key") or "")
        if q.group_key and g.get("key") and g["key"] != q.group_key:
            add("group_key_mismatch", "content", f"{g['key']} ≠ {q.group_key}")

    # --- subject
    if not q.subject_code or q.subject_source == "unclassified":
        add("subject_missing", "subject")

    # --- READY consistency
    if q.editorial_state == "READY_TO_SERVE":
        other = [s for s in (q.editorial_states or []) if s != "READY_TO_SERVE"]
        blocking = [r for r in (q.policy_reasons or []) if not r.startswith(("state:", "type:", "scoring:"))]
        if other or blocking:
            add("ready_inconsistent", "general", ", ".join(other + blocking)[:200])

    # --- solution bleeding into the next question
    sol_txt = _text(content.get("solution")) + "\n" + _text(content.get("explanation"))
    if sol_txt.strip() and (NEXT_Q.search(sol_txt) or HEADING.search(sol_txt)):
        m = NEXT_Q.search(sol_txt) or HEADING.search(sol_txt)
        add("solution_contains_next_question", "content", sol_txt[max(0, m.start() - 10):m.start() + 60].strip())
    return out


# ------------------------------------------------------------------------------------------------
# duplicates (bank-wide, computed by review-refresh)
# ------------------------------------------------------------------------------------------------
def norm_key(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower()
    return re.sub(r"[\W_]+", "", t)


def duplicate_keys(content: dict) -> tuple[str | None, str | None]:
    """(exact key = stem + options, near key = stem only). None for stems too short or figure-based to compare."""
    stem = _text(content.get("stem"))
    k = norm_key(stem)
    if len(k) < 40 or images({"stem": content.get("stem")}):
        return None, None
    opts = "|".join(norm_key(_text(o.get("content"))) for o in content.get("options") or [])
    return k + "#" + opts, k
