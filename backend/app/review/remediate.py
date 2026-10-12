"""Incremental remediation: promote not-ready questions to READY only on verifiable evidence.

`hsa-app remediate <batch> [--limit N] [--apply]` (dry run without --apply). Each batch is a candidate query plus
deterministic checks against the original source document (read-only, sha256-verified by review/sources.py) and
the source's own answer key. Fixes go through the review service (draft version → QA confirmations → approval), so
every change has a version, old/new diff, provenance and audit trail, and is skipped next time while its content hash
is unchanged. Anything not provable from the source stays in its queue with the reason recorded.
"""
import json
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


INSTRUCTION = re.compile(r"(choose (?:a|the)|mark the letter|read the (?:following|passage)|indicate the|"
                         r"chọn (?:đáp án|phương án|từ|câu)|đọc (?:đoạn|văn bản|ngữ liệu|thông tin)|"
                         r"dựa vào (?:thông tin|đoạn|văn bản))", re.I)
OPTION_LIST = re.compile(r"(?:^|\s)A[.)]\s.+?\sB[.)]\s.+?\sC[.)]\s", re.S)


def check_structure(content: dict) -> Verdict:
    """Single-choice HSA items have exactly four options, none of which carries its own A./B./C. list (arrangement
    questions whose real options were glued into the last option are not repairable by trimming)."""
    opts = content.get("options") or []
    if len(opts) != 4:
        return Verdict(False, f"option structure suspect: {len(opts)} options")
    for o in opts:
        t = plain_text(o.get("content") or [])
        if OPTION_LIST.search(t):
            return Verdict(False, f"option structure suspect: option {o.get('label')} contains an A./B./C. list")
        if re.search(r"\n\s*\**[A-H][.)]\s", t):
            return Verdict(False, f"option structure suspect: option {o.get('label')} has a line starting with an option label")
    return Verdict(True)


FIGURE_HINT = re.compile(r"(sơ đồ|hình vẽ|hình bên|hình dưới|như hình (?:vẽ|bên|dưới|sau)|theo hình|"
                         r"đồ thị (?:sau|bên|dưới|như)|bảng sau|bảng số liệu|bảng dưới|biểu đồ|quan sát hình|cho hình)",
                         re.I)


def check_complete(content: dict) -> Verdict:
    """The stem refers to a figure/scheme/table that the parsed content does not contain."""
    txt = unicodedata.normalize("NFC", plain_text(content.get("stem") or []) + " " +
                                plain_text((content.get("group") or {}).get("passage") or []))
    runs = [n.get("v", "") for b in content.get("stem") or [] for n in b.get("c") or [] if n.get("t") == "s"]
    if any(re.search(r"\S {5,}\S", v) for v in runs):
        return Verdict(False, "incomplete: a gap inside a stem line (an inline drawing such as a reaction arrow was lost)")
    m = FIGURE_HINT.search(txt)
    if not m:
        return Verdict(True)
    if C.images(content) or C.has_table(content):
        return Verdict(True)
    opts = " ".join(plain_text(o.get("content") or []) for o in content.get("options") or [])
    if m.group(1).lower().startswith("sơ đồ") and re.search(r"[→⟶⇒]|->", txt + " " + opts):
        return Verdict(True)  # the scheme is written out (in the stem or as the options)
    return Verdict(False, f"incomplete: refers to “{m.group(1)}” but the question has no figure/table")


# a group header that is only an instruction (the section's question numbers + what to do), no shared data
GROUP_INSTRUCTION = re.compile(
    r"^\W*(?:questions?\W*)?(?:\(\s*)?(?:which of the following best restates each of the given sentences|"
    r"each of the following sentences has one error \(a, b, c,? (?:or )?d\)\. find it and blacken your choice on "
    r"your answer sheet|choose a suitable word or phrase \((?:marked )?a, b, c,? (?:or )?d\) to fill in each blank|"
    r"(?:đọc và )?trả lời các câu hỏi)\W*$", re.I)
# the stem leans on context that is not in the question itself
CONTEXT_REF = re.compile(
    r"(passage|paragraph|the text\b|the author|the writer|according to|in line \d|the (?:article|extract|reading)|"
    r"đoạn|bài đọc|bài thơ|văn bản|tác giả|nhân vật|ngữ liệu|thông tin (?:trên|đã cho|được cung cấp)|"
    r"dữ liệu|bảng|biểu đồ|hình|theo (?:bài|đoạn|văn bản|thông tin)|trên đây|nêu trên|ở trên|câu chuyện)", re.I)


def check_group_instruction(content: dict) -> Verdict:
    """A group without shared data: the header must be a plain instruction and the stem self-contained."""
    g = content.get("group") or {}
    if g.get("passage"):
        return Verdict(False, "group has a passage")
    hdr = unicodedata.normalize("NFC", plain_text(g.get("header") or [])).strip()
    hdr = re.sub(r"\d+", " ", hdr)
    if not GROUP_INSTRUCTION.match(re.sub(r"\s+", " ", hdr).strip()):
        return Verdict(False, f"group header is not a plain instruction: “{hdr[:60]}”")
    stem = unicodedata.normalize("NFC", plain_text(content.get("stem") or [])).strip()
    if len(re.sub(r"[\W\d_]+", "", stem)) < 15:
        return Verdict(False, "stem too short to stand alone (likely a numbered blank of a shared passage)")
    m = CONTEXT_REF.search(stem)
    if m:
        return Verdict(False, f"stem refers to shared context (“{m.group(1)}”)")
    if "has one error" in hdr.lower():
        marked = [n for b in content.get("stem") or [] for n in b.get("c") or [] if "u" in (n.get("m") or [])]
        if len(marked) < 3:
            return Verdict(False, "error identification without the underlined parts in the stem")
    return Verdict(True, "tiêu đề nhóm chỉ là lời dẫn; câu hỏi tự đủ nghĩa")


def group_english_instruction(content: dict) -> bool:
    hdr = plain_text((content.get("group") or {}).get("header") or []).lower()
    return bool(re.search(r"restates each of the given sentences|has one error|fill in each blank", hdr))


def _lead_text(blocks) -> str:
    """Normalized text of a stem up to its first formula/image (what the source text layer surely contains)."""
    out = []
    for b in blocks or []:
        for n in b.get("c") or []:
            if n.get("t") != "s":
                return norm("".join(out))
            out.append(n.get("v", ""))
        out.append(" ")
    return norm("".join(out))


def check_group_source(content: dict, member_stems: list, pages: list[str]) -> Verdict:
    """In the source, the group's instruction is followed directly by its first question: no shared passage that the
    parse lost (a passage always sits between the instruction and the first question)."""
    joined = "".join(norm(p) for p in pages)
    hdr = norm(re.sub(r"^\W*questions?\b", "", plain_text((content.get("group") or {}).get("header") or []),
                      flags=re.I))
    hdr = re.sub(r"\d+", "", hdr)
    stem = _lead_text(content.get("stem"))[:40]
    pos = joined.find(stem) if len(stem) >= 12 else -1
    if pos < 0 or len(hdr) < 12:
        return Verdict(False, "stem/group header not located in the source")
    h = joined.rfind(hdr, 0, pos)
    if h < 0 or pos - h > 60000:
        return Verdict(False, "group instruction not found before the question in the source")
    first = pos
    for st in member_stems:
        t = _lead_text(st)[:40]
        if len(t) >= 12:
            k = joined.find(t, h + len(hdr), first)
            if k >= 0:
                first = k
    gap = re.sub(r"(questions?|cau|tu|den|to|from)", "", re.sub(r"\d+", "", joined[h + len(hdr):first]))
    if len(gap) > 30:
        return Verdict(False, f"unaccounted source text between the group instruction and its first question "
                              f"({len(gap)} chars: {gap[:40]}…) — possible lost passage")
    return Verdict(True, "trong tài liệu gốc, lời dẫn nhóm đứng ngay trước câu đầu của nhóm (không có đoạn dữ liệu chung)")


def check_wrong_passage(content: dict) -> Verdict:
    """A question attached to a passage of another subject (upstream: its number is outside the passage's range):
    detach it when the question stands on its own."""
    g = content.get("group") or {}
    ptxt = plain_text(g.get("passage") or [])
    stem = unicodedata.normalize("NFC", plain_text(content.get("stem") or [])).strip()
    if not ptxt.strip():
        return Verdict(False, "no passage to detach from")
    m = CONTEXT_REF.search(stem)
    if m:
        return Verdict(False, f"stem refers to shared context (“{m.group(1)}”)")
    if len(re.sub(r"[\W\d_]+", "", stem)) < 25:
        return Verdict(False, "stem too short to stand alone")
    q_txt = stem + " " + " ".join(plain_text(o.get("content") or []) for o in content.get("options") or [])
    ps, qs = subject_scores(ptxt), subject_scores(q_txt)
    pb, qb = max(ps, key=ps.get), max(qs, key=qs.get)
    if ps[pb] < 1 or qs[qb] < 1 or pb == qb or qs.get(pb, 0) > 0 or ps.get(qb, 0) > 0:
        return Verdict(False, f"passage ({pb} {ps[pb]}) and question ({qb} {qs[qb]}) not clearly different subjects")
    return Verdict(True, f"đoạn chung thuộc môn {pb}, câu hỏi thuộc môn {qb} và tự đủ nghĩa: gắn nhầm đoạn")


# page-number graphics ("12", "17", …) of the source pages, glued into passages as inline raster fragments; each one
# checked visually (contact sheet, 2026-10-12) — page furniture, not content
PAGE_NUMBER_FRAGMENTS = {
    "007bd0c3b6462f6fdc26c72d1a5ab571f641350c5e4ba38b1899d0d7897a45f6",
    "0cd241c1d2aff2911b1730ff5308ea372b754dfccc96cc8df5f4e637f2e15fa2",
    "23e92c6f6fc54c001dc9c3c024ba39feb075b66420a7bc8b280e44e7512b06a6",
    "2582c42e84b99dce1ccb4afe7f3ed0c691e92725d4c4700e28a760db05e68cd0",
    "26def98527e930d50908a62e097092fa91948df766346b3e2ea7d2c98083ba9b",
    "2a060f5a916764af7e53d9a0de126fc1d06c6cc0e7868c41f614f3a53f0c1bdc",
    "2a50fc7d8875a042113aed72d3f0f57cccfe13b1c18051ed43651e1ffaf6d0a7",
    "2f3b949885ca4455d5412cc372ea064bffd6b73705cb6b93b7dfb4170ca77306",
    "3a99e41fff49d7d143543fa7002fd5c89bbf822047347d87617212ad693332ff",
    "5b3a58731dcd6dfe241ddc055af1e6108af2a9e6d3681c0d2acd20db830bf4c6",
    "600e7261dcf77a2609d52c3e519f23bc19e73ce30fad059b83df50fbfc7335cc",
    "743431eeb6fffd6bf4e5ca611cd9e99e0f3cb15641b4dc6b650ef0daadac5d8c",
    "832e3d879fa409a499d27eac6ab9838ba11a9e596362ec1316f37aab32c4d9a6",
    "8aa067b9facb541b1a8eb1de2133a7f1cdf376df152a85644f59cde1d9ee69e0",
    "91e468b59df3fbddb871ce47df9ae877fc4a67214b8b3d226ca6153d66136b0f",
    "9230bdd2ca9e4717456708cb4c3036b3e73cf69c7ffc7a8817d6336c3ab478a6",
    "9740b81a5b3cc0b13c61d4d9a6beec9b43288b826c29c321e76ecacee359076d",
    "9e9872da2f1ef88d40e167bcc0d0fbbc48cbafb2c8f3e047584452a872ac2560",
    "a525e90d5f4ed38c4072aac9ad329729c57a6b04acdba748dbb0d5e66c1bf8c7",
    "c6f6a3a9a8c673540fae49ced07bdaa0e43cd9a163ae34b9f0d4fcc9d77fcba2",
    "ee047a29864c11a65896240f417d7cc3db1c7de2750de5b756317466211da51d",
    "ee8ff8ca319145207e93409b79ea9c7060728581c7496b283521bba09b91197c",
    "f81ffa7e560d91d255c15028436cc1fcc2eea63bca8692e7a713ace07d340a07",
}


def strip_verified_warns(content: dict, pages: list[str]) -> tuple[dict, int, str | None]:
    """Remove "đối tượng không hiển thị được" placeholders that stand for nothing: in the source text the words
    before and after the placeholder are adjacent (at most a bullet/dash between them)."""
    joined = "".join(norm(p) for p in pages)
    c = json.loads(json.dumps(content))
    parts = [("stem", c.get("stem"))] + [(f"option {o.get('label')}", o.get("content")) for o in c.get("options") or []]
    g = c.get("group") or {}
    parts += [("passage", g.get("passage")), ("header", g.get("header"))]
    removed = 0
    for name, blocks in parts:
        if not blocks:
            continue
        for b in blocks:  # page numbers first: the text around a placeholder is then contiguous
            if b.get("c"):
                keep = [n for n in b["c"] if not (n.get("t") == "img" and n.get("review") and
                        (n.get("src") or "").split("/")[-1].split(".")[0] in PAGE_NUMBER_FRAGMENTS)]
                removed += len(b["c"]) - len(keep)
                b["c"] = keep
        flat = [(b, n) for b in blocks for n in (b.get("c") or [])]
        for i, (b, n) in enumerate(flat):
            if n.get("t") != "warn":
                continue
            bef = norm("".join(x.get("v", "") for _, x in flat[:i] if x.get("t") == "s"))[-30:]
            aft = norm("".join(x.get("v", "") for _, x in flat[i + 1:] if x.get("t") == "s"))[:30]
            if len(bef) < 12 or len(aft) < 12:
                return content, 0, f"unrenderable object in the {name} without enough text around it to verify"
            k = joined.find(bef)
            while k >= 0 and not any(joined.startswith(aft, k + len(bef) + gap) for gap in range(4)):
                k = joined.find(bef, k + 1)
            if k < 0:
                return content, 0, f"unrenderable object in the {name}: the source has content there (not verified empty)"
        for b in blocks:
            if b.get("c"):
                before = len(b["c"])
                b["c"] = [n for n in b["c"] if n.get("t") != "warn"]
                removed += before - len(b["c"])

    def nested_warns(x) -> int:
        if isinstance(x, dict):
            return (x.get("t") == "warn" or bool(x.get("review"))) + sum(nested_warns(v) for v in x.values())
        if isinstance(x, list):
            return sum(nested_warns(v) for v in x)
        return 0
    left = nested_warns([c.get("stem"), [o.get("content") for o in c.get("options") or []], c.get("group")])
    if left:
        return content, 0, f"{left} placeholder(s)/raster fragment(s) left that cannot be verified as empty"
    return c, removed, None


def split_inline_options(content: dict, answer: dict | None) -> tuple[dict | None, dict | None, str]:
    """A multiple-choice question parsed as short response: its stem ends with paragraphs that hold only the
    options ("A. … ⇥ B. … ⇥ C. … ⇥ D. …", labels as their own text runs). Rebuild it as single choice."""
    if not answer or answer.get("kind") != "text" or not re.fullmatch(r"[A-D]", (answer.get("text") or "").strip()):
        return None, None, "key is not a single option letter"
    stem = content.get("stem") or []
    opts, cur, first = {}, None, None
    for bi, b in enumerate(stem):
        nodes = b.get("c") or []
        labels = [n for n in nodes if n.get("t") == "s" and re.fullmatch(r"\s*[A-D]\s*", n.get("v", ""))]
        if not labels:
            if cur is not None:
                return None, None, "text after the options"
            continue
        if first is None:
            first = bi
        for n in nodes:
            if n.get("t") == "s" and re.fullmatch(r"\s*[A-D]\s*", n.get("v", "")):
                cur = n["v"].strip()
                if cur in opts or cur != "ABCD"[len(opts)]:
                    return None, None, "option labels out of order"
                opts[cur] = []
            elif n.get("t") in ("tab", "br"):
                continue
            elif cur is None:
                return None, None, "text before the first option label in the option line"
            else:
                opts[cur].append(json.loads(json.dumps(n)))
    if first is None or list(opts) != list("ABCD"):
        return None, None, "four options A–D not found at the end of the stem"
    out = []
    for lab in "ABCD":
        nodes = opts[lab]
        if nodes and nodes[0].get("t") == "m":
            nodes[0]["tex"] = re.sub(r"^\s*\.\s*", "", nodes[0]["tex"])
        elif nodes and nodes[0].get("t") == "s":
            nodes[0]["v"] = re.sub(r"^\s*\.\s*", "", nodes[0]["v"])
        nodes = [n for n in nodes if not (n.get("t") == "m" and not n.get("tex", "").strip())]
        if not plain_text([{"t": "p", "c": nodes}]).strip(" .") and not any(n.get("t") == "m" for n in nodes):
            return None, None, f"option {lab} empty"
        out.append({"label": lab, "content": [{"t": "p", "c": nodes}]})
    c = json.loads(json.dumps(content))
    c["stem"], c["options"], c["type"] = stem[:first], out, "single_choice"
    if not plain_text(c["stem"]).strip():
        return None, None, "stem empty without the options"
    return c, {"kind": "choice", "labels": [answer["text"].strip()]}, ""


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
PASSAGE_MARKER = re.compile(r"^\W*(?:read the (?:following )?(?:passage|text)|đọc (?:kĩ |kỹ )?(?:đoạn trích|đoạn văn|văn bản|"
                            r"bài thơ|đoạn thơ|ngữ liệu|bài đọc))", re.I)
NOT_PASSAGE = re.compile(r"^\s*(?:[A-D][.)]\s|câu\s*\d+|question\s*\d+|đáp án|lời giải|phương pháp giải|hướng dẫn)", re.I)


def docx_paragraphs(path) -> list[dict]:
    """Body paragraphs of a .docx in order: {"kind": p|table|drawing|math, "text", "runs": [(text, marks)]}."""
    import xml.etree.ElementTree as ET
    import zipfile
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    out = []
    for el in root.find(_W + "body"):
        if el.tag == _W + "tbl":
            out.append({"kind": "table", "text": "", "runs": []})
            continue
        if el.tag != _W + "p":
            continue
        runs = []
        for r in el.iter(_W + "r"):
            t = "".join(x.text or "" for x in r.iter(_W + "t"))
            if not t:
                continue
            pr = r.find(_W + "rPr")
            marks = []
            if pr is not None:
                for tag, m in (("b", "b"), ("i", "i"), ("u", "u")):
                    e = pr.find(_W + tag)
                    if e is not None and e.get(_W + "val") not in ("0", "false", "none"):
                        marks.append(m)
            runs.append((t, marks))
        kind = "p"
        if list(el.iter(_W + "drawing")) or list(el.iter("{urn:schemas-microsoft-com:vml}shape")):
            kind = "drawing"
        elif list(el.iter(_M + "oMath")):
            kind = "math"
        out.append({"kind": kind, "text": "".join(t for t, _ in runs), "runs": runs})
    return out


def docx_passage(paras: list[dict], header: str, stem_blocks: list) -> tuple[list | None, str]:
    """The shared passage of a reading group, from the source .docx: the paragraphs between "Read the passage…"
    and the group's instruction (or after a "Read the following passage…" instruction up to the first question)."""
    sk = norm(_text_without_math(stem_blocks))[:30]
    hk = norm(header)
    idx = [i for i, p in enumerate(paras) if len(sk) >= 15 and sk in norm(p["text"])]
    if len(idx) != 1:
        return None, f"question found {len(idx)} times in the source .docx"
    i = idx[0]
    hs = [j for j in range(max(0, i - 600), i) if hk and hk[:40] in norm(paras[j]["text"])]
    if not hs:
        return None, "group instruction not found before the question in the source .docx"
    h = hs[-1]
    if any(PASSAGE_MARKER.match(paras[j]["text"]) for j in range(h + 1, i)):
        return None, "another passage starts between the group instruction and the question"
    if PASSAGE_MARKER.match(paras[h]["text"]):          # instruction first, passage after it
        start, end = h + 1, h + 1
        while end < i and not NOT_PASSAGE.match(paras[end]["text"]):
            end += 1
    else:                                                # passage first, then the instruction
        m = [j for j in range(max(0, h - 25), h) if PASSAGE_MARKER.match(paras[j]["text"])]
        if not m:
            return None, "no \"Read the passage\" marker before the group instruction"
        start, end = m[-1] + 1, h
    body = [p for p in paras[start:end] if p["text"].strip()]
    if any(p["kind"] != "p" for p in paras[start:end]):
        return None, "the passage contains a table/figure/formula (needs a visual check)"
    if any(NOT_PASSAGE.match(p["text"]) for p in body):
        return None, "the passage range contains question/option/solution lines"
    if sum(len(p["text"]) for p in body) < 300:
        return None, "passage too short"
    stem_txt = unicodedata.normalize("NFC", plain_text(stem_blocks)).lower()
    if re.search(r"gạch chân|in đậm|in nghiêng|underlined|in bold|in italics?|highlighted", stem_txt) and \
            not any(m for p in body for _, m in p["runs"]):
        return None, "the question points to marked (underlined/bold) words but the recovered passage has no marks"
    blocks = []
    for p in body:
        nodes = []
        for t, marks in p["runs"]:
            t = t.replace("\xa0", " ")
            if nodes and nodes[-1].get("m", []) == marks:
                nodes[-1]["v"] += t
            else:
                nodes.append({"t": "s", "v": t, **({"m": marks} if marks else {})})
        blocks.append({"t": "p", "c": nodes})
    return blocks, ""


def check_source_text(content: dict, pages: list[str]) -> Verdict:
    """Stem and every option must occur in the source text, options shortly after the stem."""
    full = [norm(p) for p in pages]
    joined = "".join(full)
    starts, acc = [], 0
    for p in full:
        starts.append(acc)
        acc += len(p)
    stem_blocks = list(content.get("stem") or [])
    # a generic section instruction glued in front of the stem ("Choose A, B, C or D…") occurs once in the source,
    # before the first question of the section: locate the question by what follows it
    while len(stem_blocks) > 1 and INSTRUCTION.match(unicodedata.normalize("NFC", plain_text(stem_blocks[:1])).strip()):
        stem_blocks = stem_blocks[1:]
    stem = norm(_text_without_math(stem_blocks))
    if len(stem) < 12:
        return Verdict(False, "stem text too short to locate in the source")
    # locate the stem by its longest chunks (formulas/images are not in the text layer)
    chunks = [c for c in re.split(r"\s{2,}| ", _text_without_math(stem_blocks)) if c]
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
    pieces = [norm(x) for x in re.split(r"[\n]+", _text_without_math(stem_blocks))]
    pieces = [x for x in pieces if len(x) >= 4]
    cur = pos
    for piece in pieces:
        k = joined.find(piece[:80], cur)
        if k < 0 or k - cur > 3000:
            return Verdict(False, f"stem fragment not found after its start: {piece[:40]}")
        cur = k
    window = joined[pos:pos + 6000]
    missing, at = [], 0
    for o in content.get("options") or []:
        t = norm(_text_without_math(o.get("content")))
        if not t:
            continue
        k = window.find(t, at)  # options must appear in order A, B, C, D after the stem
        if k < 0:
            missing.append(o.get("label"))
        else:
            at = k + 1
    if missing:
        return Verdict(False, f"options {','.join(missing)} not found (in order) after the stem in the source")
    g = content.get("group") or {}
    if g.get("passage"):
        ptxt = _text_without_math(g["passage"])
        low = unicodedata.normalize("NFC", ptxt).lower().strip()
        if (re.match(r"(lời giải|hướng dẫn giải|đáp án|giải chi tiết|ta có|gọi|vậy|do đó|[⇒→=])", low)
                or re.search(r"(chọn|đáp án)\s*(đáp án\s*)?[:.]?\s*[a-d]\b", low)):
            return Verdict(False, "shared passage is solution text, not the question's passage")
        pn = norm(ptxt)
        probe_p = pn[:60]
        if len(probe_p) >= 20:
            # the passage must come before the stem, not far away (a passage of another question)
            kp = joined.rfind(probe_p, max(0, pos - 30000), pos + 1)
            if kp < 0:
                return Verdict(False, "shared passage not found before the stem in the source")
    page = next((i + 1 for i, s in enumerate(starts) if s <= pos < s + len(full[i])), None)
    return Verdict(True, "stem and options match the source text" + (" (stem text occurs twice)" if dup else ""),
                   page, {"dup_stem_in_doc": dup})


def trim_options(content: dict, pages: list[str]) -> tuple[dict, list[str]]:
    """Repair options that carry stray trailing paragraphs (segmentation contamination): an option whose full text is
    not in the source, but whose leading paragraphs are (right after the stem), is cut to those paragraphs. Options
    whose full text is in the source are never touched. Returns (content, trimmed labels)."""
    import json
    c = json.loads(json.dumps(content))
    joined = "".join(norm(p) for p in pages)
    stem = norm(_text_without_math(c.get("stem")))
    pos = joined.find(stem[:60]) if stem else -1
    if pos < 0:
        return content, []
    window = joined[pos:pos + 6000]
    trimmed, at = [], 0
    for o in c.get("options") or []:
        blocks = o.get("content") or []
        full = norm(_text_without_math(blocks))
        k = window.find(full, at) if full else -1
        if k >= 0 or len(blocks) < 2:
            at = k + 1 if k >= 0 else at
            continue
        for n in range(len(blocks) - 1, 0, -1):
            head = norm(_text_without_math(blocks[:n]))
            j = window.find(head, at) if len(head) >= 2 else -1
            if j >= 0:
                o["content"] = blocks[:n]
                trimmed.append(o["label"])
                at = j + 1
                break
    return (c, trimmed) if trimmed else (content, [])


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
    kinds_ = {e.get("type") for e in ev}
    # inline evidence sits next to the question (an "Đáp án: X" line, the solution's "Chọn X", a highlighted
    # option): it cannot be shifted onto another question. Positional evidence (answer-key table, a separate
    # solution section keyed by number) can be misaligned, so alone it needs a second signal or an independent check.
    inline = kinds_ & {"answer_statement", "solution_conclusion", "option_highlight", "option_color"}
    if len(kinds_) < 2 and not inline:
        # one evidence type (e.g. only the answer-key table) can be misaligned with the questions: needs a second
        # source signal or an independent solution before promotion
        return Verdict(False, f"single answer evidence ({', '.join(sorted(k for k in kinds_ if k))}): needs independent check")
    sol = plain_text(content.get("solution") or []) + " " + plain_text(content.get("explanation") or [])
    said = {m.group(1).upper() for m in CHOSEN_RE.finditer(sol)}
    if len(said) == 1 and said != set(labels):  # one explicit "Chọn X" that contradicts the key
        return Verdict(False, f"solution says {sorted(said)} but the key is {labels}")
    said = said if len(said) == 1 else set()  # several labels = a discussion of the options, not a verdict
    kinds = sorted({e.get("type", "?") for e in ev})
    return Verdict(True, f"source answer key {labels} ({', '.join(kinds)}; {len(occs)} occurrence(s) agree)"
                   + ("; solution agrees" if said else ""), details={"evidence": kinds})


# Content-based subject signal (cross-check only: it never assigns a subject by itself)
SUBJECT_TERMS = {
    "physics": ["dao động", "con lắc", "lò xo", "bước sóng", "tần số", "điện áp", "cường độ dòng điện", "điện trở",
                "tụ điện", "cuộn cảm", "hạt nhân", "phân hạch", "phóng xạ", "photon", "quang điện", "gia tốc",
                "vận tốc", "động năng", "thế năng", "công suất", "mev", "từ trường", "khúc xạ", "thấu kính",
                "giao thoa", "sóng cơ", "sóng điện từ", "chu kì", "biên độ", "suất điện động", "trọng lực", "lực ma sát"],
    "chemistry": ["phản ứng", "dung dịch", "mol", "nguyên tử", "phân tử", "oxi hóa", "oxi hoá", "axit", "bazơ",
                  "kim loại", "este", "ancol", "polime", "hợp chất", "nguyên tố", "cation", "anion", "kết tủa",
                  "điện phân", "hiđrocacbon", "amin", "glucozơ", "chất béo", "liên kết ion", "liên kết cộng hóa trị",
                  "số oxi hóa", "đồng vị", "hóa trị", "hoá trị", "nồng độ", "amino axit", "aminoaxit", "peptit",
                  "anilin", "glyxin", "alanin", "amin ", "protein", "đồng phân", "cấu hình electron", "glucozơ",
                  "saccarozơ", "fructozơ", "mantozơ", "tinh bột", "xenlulozơ", "este", "xà phòng", "thạch cao",
                  "cao su", "polime", "ancol", "anđehit", "andehit", "axit "],
    "biology": ["gen", "adn", "arn", "nhiễm sắc thể", "tế bào", "quần thể", "quần xã", "đột biến", "di truyền",
                "alen", "kiểu gen", "kiểu hình", "sinh vật", "enzim", "hệ sinh thái", "quang hợp", "hô hấp",
                "tiến hóa", "tiến hoá", "loài", "nuclêôtit", "prôtêin", "thụ tinh"],
    "math": ["hàm số", "phương trình", "bất phương trình", "đồ thị hàm", "đạo hàm", "tích phân", "nguyên hàm",
             "logarit", "số phức", "hình chóp", "lăng trụ", "thể tích", "xác suất", "cấp số", "giới hạn", "vectơ",
             "tọa độ", "toạ độ", "đường tròn", "mặt phẳng", "giá trị lớn nhất", "giá trị nhỏ nhất", "tổ hợp",
             "chỉnh hợp", "dãy số", "tam giác", "hình vuông", "cực trị", "tiệm cận", "nghiệm"],
    "history": ["chiến tranh", "cách mạng", "kháng chiến", "thực dân", "đế quốc", "hiệp định", "phong trào",
                "khởi nghĩa", "việt minh", "liên xô", "chiến dịch", "triều đại", "đảng cộng sản", "chiến thắng"],
    "geography": ["khí hậu", "địa hình", "dân số", "đồng bằng", "lượng mưa", "nông nghiệp", "công nghiệp",
                  "tây nguyên", "duyên hải", "trung du", "miền núi", "lãnh thổ", "gió mùa", "sản lượng", "vùng kinh tế"],
    "civics": ["pháp luật", "công dân", "vi phạm", "quyền và nghĩa vụ", "nghĩa vụ", "khiếu nại", "tố cáo", "bầu cử",
               "ứng cử", "hợp đồng", "khởi kiện", "tòa án", "toà án", "trách nhiệm pháp lí", "trách nhiệm pháp lý",
               "bất khả xâm phạm", "tín ngưỡng", "tôn giáo", "quy phạm pháp luật", "hiến pháp", "luật",
               "quyền tự do", "bình đẳng trước pháp luật", "xử phạt", "vi phạm hành chính", "vi phạm hình sự"],
    "literature": ["đoạn trích", "tác giả", "bài thơ", "văn bản", "nhân vật", "tác phẩm", "biện pháp tu từ",
                   "thể thơ", "phong cách ngôn ngữ", "từ láy", "chính tả", "câu văn", "tiểu thuyết", "truyện ngắn",
                   "từ hán việt", "thành ngữ", "phương thức biểu đạt", "nghĩa của từ", "lỗi dùng từ"],
}
EN_STOP = {"the", "of", "and", "to", "is", "in", "that", "for", "which", "with", "was", "are", "be", "this"}


def subject_scores(text_: str) -> dict:
    t = " " + unicodedata.normalize("NFC", text_ or "").lower() + " "
    out = {k: sum(len(re.findall(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t)) for w in ws) for k, ws in SUBJECT_TERMS.items()}
    toks = re.findall(r"[a-z]+", t)
    out["english"] = sum(1 for w in toks if w in EN_STOP) // 2 if len(toks) > 15 else 0
    return out


SECTION_SUBJECTS = [  # heading of the source section the question sits in → subject (most specific first)
    ("english", ["tiếng anh", "english"]),
    ("physics", ["vật lí", "vật lý"]), ("chemistry", ["hoá học", "hóa học"]), ("biology", ["sinh học"]),
    ("history", ["lịch sử"]), ("geography", ["địa lí", "địa lý"]),
    ("civics", ["giáo dục công dân", "gdcd", "kinh tế và pháp luật", "kinh tế pháp luật", "pháp luật"]),
    ("literature", ["ngữ văn", "văn học", "tiếng việt", "tư duy định tính"]),
    ("logic", ["logic", "tư duy lô-gic", "suy luận logic"]),
    ("math", ["toán", "tư duy định lượng", "xử lí số liệu", "xử lý số liệu"]),
]


def section_subject(section: str | None) -> str | None:
    t = unicodedata.normalize("NFC", section or "").lower()
    # "KHOA HỌC hoặc TIẾNG ANH": a choice between parts, or a heading naming several subjects, says nothing
    named = {subj for subj, keys in SECTION_SUBJECTS if any(k in t for k in keys)}
    if " hoặc " in t or len(named) > 1:
        return None
    if not t or "khoa học" in t and not any(k in t for _, ks in SECTION_SUBJECTS[:7] for k in ks):
        return None  # "Khoa học"/"Tư duy khoa học" sections mix subjects
    for subj, keys in SECTION_SUBJECTS:
        if any(k in t for k in keys):
            return subj
    return None


def check_subject(q: Question, content: dict) -> Verdict:
    """SUBJECT_OK when the content agrees with the label, or a high-confidence upstream label is not contradicted."""
    subj = q.subject_code
    if not subj or q.subject_source == "unclassified" or subj == "general":
        return Verdict(False, "subject unclassified")
    txt = " ".join(plain_text(content.get(k) or []) for k in ("stem", "solution", "explanation"))
    txt += " " + " ".join(plain_text(o.get("content") or []) for o in content.get("options") or [])
    if content.get("group"):
        txt += " " + plain_text(content["group"].get("passage") or [])
    sc = subject_scores(txt)
    best = max(sc, key=sc.get)
    mine = sc.get(subj, 0)
    if mine >= 2 and mine >= sc[best] - 1:
        return Verdict(True, f"nội dung khớp môn {subj} (từ khoá {mine})")
    high = q.classification_confidence == "high" or q.classification_source in ("semantic_correction",
                                                                                  "semantic_assignment")
    contradict = sc[best] >= 3 and best != subj and sc[best] >= mine + 3
    sec = section_subject((q.provenance or {}).get("section"))
    if sec == subj and not contradict:
        return Verdict(True, f"mục của tài liệu gốc thuộc môn {subj}: “{(q.provenance or {}).get('section')}”")
    if sec and sec != subj and sc.get(sec, 0) >= sc.get(subj, 0):
        return Verdict(False, f"subject conflict: assigned {subj}, source section says {sec}",
                       details={"suggest": sec, "scores": sc})
    if best == subj and mine >= 1 and not contradict:
        return Verdict(True, f"nội dung nghiêng về môn {subj} (từ khoá {mine}, không môn nào khác hơn)")
    bare_math = subj == "math" and mine == 0 and not C.collect_tex(content) and not re.search(r"\d", txt)
    if high and not contradict and (mine >= 1 or sc[best] == 0) and not bare_math:  # no evidence for the label but some for another
        # subject: the upstream label is not enough (e.g. geography questions of a mixed exam labelled math)
        return Verdict(True, f"phân loại cuối của ngân hàng ({q.classification_source}/{q.classification_confidence}),"
                             f" nội dung không mâu thuẫn")
    if sc[best] == 0:
        return Verdict(False, f"subject unverified: no content/section evidence for {subj}")
    return Verdict(False, f"subject conflict: assigned {subj}, content suggests {best} ({sc[best]} vs {mine})",
                   details={"suggest": best, "scores": sc})


def strip_hsa_watermark(content: dict) -> tuple[dict, bool]:
    """Remove the "H S A" page watermark the PDF text layer mixes into stems/options ("…equipment.⏎H⇥S⇥A")."""
    import json
    c = json.loads(json.dumps(content))
    changed = False

    def clean(inl: list) -> list:
        nonlocal changed
        out, i = [], 0
        while i < len(inl):
            seq = inl[i:i + 5]
            if (len(seq) == 5 and [n.get("t") for n in seq] == ["s", "tab", "s", "tab", "s"]
                    and [seq[0]["v"].strip(), seq[2]["v"].strip(), seq[4]["v"].strip()] == ["H", "S", "A"]):
                while out and out[-1].get("t") in ("br", "tab") or (out and out[-1].get("t") == "s"
                                                                    and not out[-1]["v"].strip()):
                    out.pop()
                i += 5
                changed = True
                continue
            n = inl[i]
            if n.get("t") == "s" and re.search(r"(^|\s)H[ \t]S[ \t]A(\s|$)", n["v"]):
                n = dict(n, v=re.sub(r"\s*(?<!\w)H[ \t]S[ \t]A(?!\w)\s*", " ", n["v"]).rstrip())
                changed = True
            out.append(n)
            i += 1
        return out
    for b in C.walk([c.get("stem"), [o.get("content") for o in c.get("options") or []], c.get("group"),
                     c.get("solution"), c.get("explanation")]):
        if b.get("t") == "p":
            b["c"] = clean(b["c"])
    return c, changed


# page furniture (headers/footers/adverts) the source documents' text layers mix into questions; measured on
# 2026-10-12 as recurring across many documents. Only these exact phrases are removed.
FURNITURE = [re.compile(p_, re.I) for p_ in (
    r"Tài liệu được sưu tầm miễn phí,? nghiêm cấm sử dụng tài liệu kiếm lợi nhuận\.?",
    r"Thầy Vũ Tuấn Anh\s+Tài liệu khóa Live C[^:]{0,20}:\s*Nắm trọn từng chuyên đề",
    r"-{3,}\s*Hết chủ đề [^,]{2,20}, hãy làm chủ đề tiếp theo nếu đã chọn\s*-{3,}",
    r"KHOA HỌC\s+Chủ đề [^\n]{2,20} có \d+ câu hỏi từ \d+ đến \d+",
    r"Cán bộ coi thi không giải thích gì thêm\.?",
    r"Fanpage:[^\n|]{0,120}\|\s*\d{1,3}",
    r"(?<!\w)c o m / t u a n a n h \.?",
    r"(?<!\w)p h y s i c s(?!\w)",
    # section headers of a full exam glued to the end of the preceding question
    r"ĐỀ THI THAM KHẢO\s*KỲ THI ĐÁNH GIÁ NĂNG LỰC\s*$",
    r"[—–-]*\s*-*\s*ĐỀ ÔN TẬP (?:CHƯƠNG|CHUYÊN ĐỀ) [^\n]{3,80}\(Thời gian: \d+ phút\)\s*$",
    r"\d\.\d[.:]\s*(?:TIẾNG ANH|SUY LUẬN KHOA HỌC|TOÁN HỌC|VĂN HỌC[^\n]{0,20})\s*$",
    r"TƯ DUY KHOA HỌC\s*/\s*GIẢI QUYẾT VẤN ĐỀ\s*$",
)]
# an exam-section header / divider that the parser glued as extra paragraph(s) to the last option of a section
SECTION_HEAD = re.compile(r"^\s*(?:\d\.\d\.?\s*(?:TIẾNG ANH|SUY LUẬN KHOA HỌC|TOÁN HỌC|VĂN HỌC|KHOA HỌC|TƯ DUY)|"
                          r"-{3,}\s*Hết chủ đề|ĐỀ ÔN TẬP CHƯƠNG|ĐỀ THI THAM KHẢO|TƯ DUY KHOA HỌC|"
                          r"PHẦN \d|Read the (?:following )?passage|"
                          r"\d\.\d[.:]?\s*(?i:Địa l[íý]|Lịch sử|Vật l[íý]|Ho[áà] học|Sinh học|Ngữ văn|Tiếng Anh|Toán học)\s*$)")


def option_section_head(content: dict) -> str | None:
    for o in content.get("options") or []:
        for b in o.get("content") or []:
            if SECTION_HEAD.match(plain_text([b])):
                return o.get("label")
    return None


# a following reading passage glued to the last option (only ever stripped from options)
OPTION_PASSAGE_TAIL = re.compile(r"(?:Read the (?:following )?passage|Read the passage carefully|Mark the letter A, B, C,? or D on "
                                 r"your answer sheet to indicate the correct (?:option|word))[\s\S]*$", re.I)


def strip_furniture(content: dict) -> tuple[dict, bool]:
    import json
    c = json.loads(json.dumps(content))
    changed = False
    for o in c.get("options") or []:
        blocks = o.get("content") or []
        cut = next((i for i, b in enumerate(blocks) if i > 0 and SECTION_HEAD.match(plain_text([b]))), None)
        if cut is not None:  # the header paragraph and whatever follows it belong to the next section
            o["content"] = blocks[:cut]
            changed = True
        for b in o.get("content") or []:
            for n in b.get("c") or []:
                if n.get("t") == "s" and OPTION_PASSAGE_TAIL.search(n.get("v", "")):
                    n["v"] = OPTION_PASSAGE_TAIL.sub("", n["v"]).rstrip()
                    changed = True

    def clean_blocks(blocks):
        nonlocal changed
        out = []
        for b in blocks or []:
            if b.get("t") == "p":
                inl = []
                for n in b["c"]:
                    if n.get("t") == "s":
                        v = n["v"]
                        for rx in FURNITURE:
                            v = rx.sub(" ", v)
                        if v != n["v"]:
                            changed = True
                            v = re.sub(r"[ \t]{2,}", " ", v)
                            if not v.strip():
                                continue
                            n = dict(n, v=v)
                    inl.append(n)
                while inl and (inl[-1].get("t") in ("br", "tab") or inl[-1].get("t") == "s" and not inl[-1]["v"].strip()):
                    inl.pop()
                if not inl or not any(x.get("t") != "s" or x["v"].strip() for x in inl):
                    changed = changed or bool(b["c"])
                    continue
                b = dict(b, c=inl)
            out.append(b)
        return out
    for k in ("stem", "solution", "explanation"):
        if c.get(k):
            c[k] = clean_blocks(c[k])
    for o in c.get("options") or []:
        o["content"] = clean_blocks(o["content"])
    if c.get("group"):
        for k in ("header", "passage"):
            c["group"][k] = clean_blocks(c["group"].get(k))
    return c, changed


def solution_contradicts(answer: dict, content: dict) -> str | None:
    """The solution text clearly paraphrases another option than the key (no letter needed)."""
    raw = plain_text(content.get("solution") or []) + " " + plain_text(content.get("explanation") or [])
    sol = norm(raw)
    if len(sol) < 30 or not answer or answer.get("kind") != "choice" or len(answer["labels"]) != 1:
        return None
    stem = unicodedata.normalize("NFC", plain_text(content.get("stem") or [])).lower()
    # negative questions ("không đúng", "KHÔNG phải", "EXCEPT") restate the true options in their solution, and a
    # solution that walks through the options by letter is not a paraphrase of one option: neither is comparable
    if re.search(r"\b(không|chưa|sai|trừ|except|not|false|incorrect)\b", stem) or \
            re.search(r"(?:^|[\s(])(?:[A-D][.):]|[A-D]\s+(?:sai|đúng|là|is|are)\b|đáp án [A-D]|phương án [A-D])",
                      raw):
        return None
    key = answer["labels"][0]
    cov = {}
    for o in content.get("options") or []:
        ws = [w for w in words(_text_without_math(o.get("content"))) if len(w) > 1]
        if len(ws) < 4:
            return None  # short options (numbers, single words) are not comparable this way
        cov[o["label"]] = sum(1 for w in ws if norm(w) in sol) / len(ws)
    best = max(cov, key=cov.get)
    if best != key and cov[best] >= 0.85 and cov.get(key, 0) <= cov[best] - 0.3:
        return f"solution text matches option {best} ({cov[best]:.0%}) rather than the key {key} ({cov.get(key, 0):.0%})"
    return None


def strip_own_labels(content: dict) -> tuple[dict, list[str]]:
    """Option text that repeats its own label ("B. Đoàn người…" in option B) → label removed."""
    import json
    c = json.loads(json.dumps(content))
    fixed = []
    for o in c.get("options") or []:
        blocks = o.get("content") or []
        if not blocks or blocks[0].get("t") != "p" or not blocks[0]["c"] or blocks[0]["c"][0].get("t") != "s":
            continue
        first = blocks[0]["c"][0]
        m = re.match(r"\s*\**\s*" + re.escape(o["label"]) + r"\s*[.)]\s*\**\s*", first["v"])
        if m and first["v"][m.end():].strip():
            first["v"] = first["v"][m.end():]
            fixed.append(o["label"])
    return (c, fixed) if fixed else (content, [])


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


# "Tailieuchuan.vn" banners embedded by a source website (visually confirmed, 2026-10-12): not part of any question
WATERMARKS = {
    "a4b022077981d5dbcc0feeb990de5671098fbea25bdade599db0292c95b7a18b",
    "c4796d86d42277958300f20c4208d88183fe6130e4d7b198d76001dafe26a716",
    "4655edcbfe27beb8ece86c67ab0b0b7b31df4239b7adb2ee1e5a3264d9a4d149",
    "93e78c19553cd1796d016c74a411686271fae5ec044ff199ae3f45fa36bb4c23",
    "f13fc4235b889530d1b3165535d270f3a4f128855b3a1d284243c7aed705b204",
    "4b2618f1262e8fcf86ddbd4303b25e5c635c0259aca2fe1f7f410c136471553e",
}


def strip_watermarks(content: dict) -> tuple[dict, bool]:
    import json
    c = json.loads(json.dumps(content))
    changed = False

    def is_wm(n):
        return n.get("t") == "img" and (n.get("src") or "").split("/")[-1].split(".")[0] in WATERMARKS

    def clean_blocks(blocks):
        nonlocal changed
        out = []
        for b in blocks or []:
            if is_wm(b):
                changed = True
                continue
            if b.get("t") == "p":
                inl = [n for n in b["c"] if not is_wm(n)]
                if len(inl) != len(b["c"]):
                    changed = True
                    # drop leftover brackets/whitespace around the banner, e.g. "(" "Tailieuchuan.vn" ")"
                    if not plain_text([{"t": "p", "c": inl}]).strip(" ()[]\t\n") and not any(
                            n.get("t") in ("m", "img") for n in inl):
                        continue
                    b = dict(b, c=inl)
            elif b.get("t") == "table":
                b = dict(b, rows=[[dict(cell, c=clean_blocks(cell["c"])) for cell in row] for row in b["rows"]])
            out.append(b)
        return out
    for k in ("stem", "solution", "explanation"):
        if c.get(k):
            c[k] = clean_blocks(c[k])
    for o in c.get("options") or []:
        o["content"] = clean_blocks(o["content"])
    if c.get("group"):
        for k in ("header", "passage"):
            c["group"][k] = clean_blocks(c["group"].get(k))
    return c, changed


# ------------------------------------------------------------------------------------------------
# batches (candidate queries). Every batch excludes questions under review, drafts and review decisions.
# ------------------------------------------------------------------------------------------------
BASE = f"""
  q.removed_upstream = false AND q.review_gate IS NULL
  AND (r.draft_version_id IS NULL AND r.status = 'none'
       OR r.status = 'draft' AND (SELECT u.email FROM question_version dv JOIN app_user u ON u.id = dv.created_by
                                  WHERE dv.id = r.draft_version_id) = '{ACTOR_EMAIL}')
  AND r.findings_critical = 0 AND r.dirty = false
"""  # drafts are only continued when this remediation created them (never a person's work in progress)
_WM_RE = "|".join("as_" + w[:16] for w in sorted(WATERMARKS))
BATCHES = {
    # upstream NEEDS_REVIEW only because of Unicode/label normalisation, nothing else
    "normalization_only": BASE + """
      AND q.editorial_states = '{NEEDS_REVIEW}' AND q.policy_reasons = ARRAY['state:NEEDS_REVIEW']::varchar[]
      AND q.review_flags <@ ARRAY['not_nfc_normalized','option_label_normalized']::varchar[]
      AND q.state_notes = '{}' AND q.question_type = 'single_choice' AND NOT q.has_formula
      AND q.subject_source IN ('upstream_effective', 'admin', 'original')
    """,
    # upstream NEEDS_VISUAL_REVIEW only because of a website watermark banner (removed by the batch)
    "watermark_only": BASE + f"""
      AND q.editorial_states = '{{NEEDS_VISUAL_REVIEW}}'
      AND q.policy_reasons = ARRAY['state:NEEDS_VISUAL_REVIEW']::varchar[]
      AND NOT EXISTS (SELECT 1 FROM unnest(q.state_notes) n WHERE n !~ '({_WM_RE})')
      AND q.question_type = 'single_choice' AND NOT q.has_formula
      AND q.subject_source IN ('upstream_effective', 'admin', 'original')
    """,
}
BATCHES["contamination_only"] = BASE + """
      AND q.editorial_states = '{NEEDS_REVIEW}' AND q.policy_reasons = ARRAY['state:NEEDS_REVIEW']::varchar[]
      AND q.review_flags <@ ARRAY['not_nfc_normalized','option_label_normalized']::varchar[]
      AND q.state_notes <> '{}'
      AND NOT EXISTS (SELECT 1 FROM unnest(q.state_notes) n WHERE n NOT LIKE 'possible segmentation contamination%')
      AND q.question_type = 'single_choice' AND NOT q.has_formula
      AND q.subject_source IN ('upstream_effective', 'admin', 'original')
"""
# questions flagged only because they come from a PDF text layer, in subjects without mathematics (the text layer
# can flatten formulas — x², m/s² — which a text comparison cannot detect, so math/physics/chemistry wait)
BATCHES["pdf_text_nonmath"] = BASE + """
      AND q.editorial_states = '{NEEDS_REVIEW}' AND q.policy_reasons = ARRAY['state:NEEDS_REVIEW']::varchar[]
      AND q.review_flags && ARRAY['pdf_text_layer_fidelity']::varchar[]
      AND q.review_flags <@ ARRAY['pdf_text_layer_fidelity','not_nfc_normalized','option_label_normalized']::varchar[]
      AND q.question_type = 'single_choice' AND NOT q.has_formula
      AND q.subject_code IN ('geography','english','literature','biology','history','civics')
      AND NOT EXISTS (SELECT 1 FROM review_finding f WHERE f.question_id = q.id AND f.status = 'open'
                      AND f.code IN ('ocr_garbage', 'raw_latex_in_text'))
"""
# upstream NEEDS_VISUAL_REVIEW only because a figure is small: everything else is verified automatically, the
# figure itself needs a visual confirmation (confirm_visual decision) before promotion
BATCHES["visual_lowres"] = BASE + f"""
      AND q.editorial_states = '{{NEEDS_VISUAL_REVIEW}}'
      AND q.policy_reasons = ARRAY['state:NEEDS_VISUAL_REVIEW']::varchar[]
      AND NOT EXISTS (SELECT 1 FROM unnest(q.state_notes) n WHERE n !~ '^low-resolution figure ')
      AND EXISTS (SELECT 1 FROM unnest(q.state_notes) n WHERE n !~ '({_WM_RE})')
      AND q.question_type = 'single_choice' AND NOT q.has_formula
"""
# upstream READY, blocked only because the group has no passage: verified instruction-only groups
BATCHES["group_instruction_only"] = BASE.replace(  # the missing-passage finding is what this batch resolves
    "r.findings_critical = 0", "NOT EXISTS (SELECT 1 FROM review_finding f WHERE f.question_id = q.id AND "
    "f.status = 'open' AND f.severity = 'critical' AND f.code <> 'group_passage_missing')") + """
      AND q.editorial_states = '{READY_TO_SERVE}' AND q.policy_reasons = '{group_context_missing}'
      AND q.question_type = 'single_choice' AND NOT q.has_formula AND NOT q.has_image AND NOT q.has_table
"""
# the same with formulas: the formulas are the bank's READY version (unchanged here), KaTeX-checked by the policy
BATCHES["group_instruction_only_math"] = BATCHES["group_instruction_only"].replace(
    "AND NOT q.has_formula ", "AND q.has_formula ")
# upstream READY, attached to a passage whose range does not include it (group_membership_suspect)
BATCHES["group_detach_wrong_passage"] = BASE + """
      AND q.editorial_states = '{READY_TO_SERVE}' AND q.policy_reasons = '{group_membership_suspect}'
      AND q.question_type = 'single_choice' AND NOT q.has_image AND NOT q.has_table
"""
DETACH_BATCHES = {"group_detach_wrong_passage"}
# upstream READY, blocked only by a placeholder for an object that could not be converted
BATCHES["render_warning_artifact"] = BASE.replace(  # the placeholder finding is what this batch resolves
    "r.findings_critical = 0", "NOT EXISTS (SELECT 1 FROM review_finding f WHERE f.question_id = q.id AND "
    "f.status = 'open' AND f.severity = 'critical' AND f.code <> 'unresolved_placeholder')") + """
      AND q.editorial_states = '{READY_TO_SERVE}' AND q.policy_reasons = '{render_warning}'
      AND q.question_type = 'single_choice' AND NOT q.has_image AND NOT q.has_table
"""
WARN_BATCHES = {"render_warning_artifact"}
# upstream READY multiple-choice questions parsed as short response (options inside the stem)
BATCHES["inline_options_split"] = BASE + """
      AND q.editorial_states = '{READY_TO_SERVE}' AND q.policy_reasons = '{inline_options_in_short_response}'
      AND q.question_type = 'short_response'
"""
SPLIT_BATCHES = {"inline_options_split"}
# upstream READY reading questions whose shared passage was lost by the parser: recovered from the source .docx
# (runs only where the original archives are readable: the sources service)
BATCHES["group_passage_recover"] = BASE.replace(
    "r.findings_critical = 0", "NOT EXISTS (SELECT 1 FROM review_finding f WHERE f.question_id = q.id AND "
    "f.status = 'open' AND f.severity = 'critical' AND f.code <> 'group_passage_missing')") + """
      AND q.editorial_states = '{READY_TO_SERVE}' AND q.policy_reasons = '{group_context_missing}'
      AND q.question_type = 'single_choice' AND NOT q.has_image AND NOT q.has_table AND NOT q.has_formula
      AND (q.provenance->>'document_id') IS NOT NULL
"""
RECOVER_BATCHES = {"group_passage_recover"}
# answer not linked by the bank: content verified against the source here, the answer by solving the question
# (confirm_answer / answer decisions, method claude_review) — never taken from position alone
BATCHES["answer_linking"] = BASE.replace(  # the missing-answer finding is what this batch resolves
    "r.findings_critical = 0", "NOT EXISTS (SELECT 1 FROM review_finding f WHERE f.question_id = q.id AND "
    "f.status = 'open' AND f.severity = 'critical' AND f.code <> 'answer_missing')") + """
      AND q.editorial_states = '{NEEDS_ANSWER_LINKING}'
      AND q.question_type = 'single_choice' AND NOT q.has_formula AND NOT q.has_image AND NOT q.has_table
      AND (q.provenance->>'extraction_method') <> 'pdf_text_layer'
"""
ANSWER_BATCHES = {"answer_linking"}
GROUP_BATCHES = {"group_instruction_only", "group_instruction_only_math"}
# the same with formulas (the formulas are the bank's version; only the figure was flagged)
BATCHES["visual_lowres_formula"] = BATCHES["visual_lowres"].replace(
    "AND q.question_type = 'single_choice' AND NOT q.has_formula", "AND q.question_type = 'single_choice' AND q.has_formula")
NEEDS_VISUAL_CONFIRMATION = {"visual_lowres", "visual_lowres_formula"}
TRANSFORMS = {"watermark_only": "watermark"}


def load_docx(store, document_id: str) -> list | None:
    """Paragraphs of a source .docx (None when it is not a .docx or the original archive is not readable)."""
    import tempfile
    from pathlib import Path
    doc = store.up.document(document_id)
    if not doc or (doc.get("file_kind") or "").lower() != "docx":
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="hsa_docx_", dir=store._tmp()) as tmp:
            src = store._materialize(doc, Path(tmp))
            return docx_paragraphs(src) if src else None
    except (OSError, KeyError, ValueError):
        return None


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
    docx_cache: dict[str, list | None] = {}
    for qid in candidates(db, batch, limit):
        q = db.scalar(select(Question).options(lazyload(Question.current_version)).where(Question.id == qid))
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        stats["examined"] += 1

        def unresolved(reason, conflict=False, record=True):
            stats["unresolved"] += 1
            key = reason.split(":")[0][:60]
            stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            if conflict:
                stats["source_conflicts"] += 1
            note = f"[{batch}] {reason}"
            last = db.scalar(select(ReviewEvent.note).where(ReviewEvent.question_id == q.id,
                                                            ReviewEvent.action == "remediation_skip")
                             .order_by(ReviewEvent.id.desc()).limit(1))
            if apply and record and last != note:  # the reason is recorded once, not on every run
                db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id,
                                   action="remediation_skip", changes=[], note=note))
                db.commit()
            log(f"  SKIP {q.external_id}: {reason}")

        st = check_structure(v.content) if batch not in SPLIT_BATCHES else Verdict(True)  # checked after the split
        if st.ok:
            st = check_complete(v.content)
        if not st.ok:
            unresolved(st.reason, conflict=True)
            continue
        doc = (q.provenance or {}).get("document_id")
        sc = db.get(SourceCache, doc) if doc else None
        if not sc or sc.status != "ready" or not sc.blob_sha256:
            transient = sc is not None and sc.status in ("queued", "preparing")
            unresolved(f"source not available ({sc.status if sc else 'none'})", record=not transient)
            continue
        if sc.blob_sha256 not in page_cache:
            # pdftotext of a large document can take a while: never hold a transaction open meanwhile
            # (idle_in_transaction_session_timeout would kill the connection)
            db.commit() if apply else db.rollback()
            page_cache[sc.blob_sha256] = store.page_texts(sc.blob_sha256)
            q = db.scalar(select(Question).options(lazyload(Question.current_version)).where(Question.id == qid))
            r = S.get_review(db, q)
            v = S.working_version(db, q, r)
        content, nfc_changed = nfc_content(v.content)
        content, relabelled = strip_own_labels(content)
        content, wm_text = strip_hsa_watermark(content)
        content, furniture = strip_furniture(content)
        nfc_changed = nfc_changed or bool(relabelled) or wm_text or furniture
        head = option_section_head(content)
        if head:
            unresolved(f"option {head} carries an exam-section header / next section text", conflict=True)
            continue
        wm_removed = False
        if TRANSFORMS.get(batch) == "watermark":
            content, wm_removed = strip_watermarks(content)
            if C.images(content) or C.has_table(content):
                unresolved("other figures remain after removing the watermark (needs a visual check)")
                continue
        passage_recovered = False
        if batch in RECOVER_BATCHES:
            if doc not in docx_cache:
                db.commit() if apply else db.rollback()
                docx_cache[doc] = load_docx(store, doc)
                q = db.scalar(select(Question).options(lazyload(Question.current_version)).where(Question.id == qid))
                r = S.get_review(db, q)
                v = S.working_version(db, q, r)
            paras = docx_cache[doc]
            if paras is None:
                unresolved("source .docx not readable here", record=False)
                continue
            blocks, why = docx_passage(paras, plain_text((content.get("group") or {}).get("header") or []),
                                       content.get("stem") or [])
            if blocks is None:
                unresolved(why)
                continue
            content = json.loads(json.dumps(content))
            content["group"]["passage"] = blocks
            passage_recovered = True
        split_answer = None
        if batch in SPLIT_BATCHES:
            content, split_answer, why = split_inline_options(content, v.answer)
            if content is None:
                unresolved(why)
                continue
            st = check_structure(content)
            if not st.ok:
                unresolved(st.reason, conflict=True)
                continue
        warns_removed = 0
        if batch in WARN_BATCHES:
            content, warns_removed, problem = strip_verified_warns(content, page_cache[sc.blob_sha256])
            if problem or not warns_removed:
                unresolved(problem or "no placeholder found")
                continue
        group_marked = False
        if batch in GROUP_BATCHES:
            gv = check_group_instruction(content)
            if gv.ok:
                members = db.scalars(select(Question).options(lazyload(Question.current_version))
                                     .where(Question.group_key == q.group_key)).all()
                stems = [m.current_version.content.get("stem") for m in members if m.current_version]
                gv = check_group_source(content, stems, page_cache[sc.blob_sha256])
            if not gv.ok:
                unresolved(gv.reason)
                continue
            content = json.loads(json.dumps(content))
            content["group"]["instruction_only"] = True
            group_marked = True
            if apply and group_english_instruction(content) and q.subject_code != "english":
                # an English-language section (its instruction and the question are in English)
                why = (f"[{batch}] câu tiếng Anh (lời dẫn “{plain_text(content['group']['header'])[:60]}”), "
                       f"không phải {q.subject_code}")
                S.set_subject(db, q, who, "english", why, media_root=media_root)
                S.confirm(db, q, who, "subject", "ok", why, media_root=media_root, method=METHOD)
                stats["subject_fixed"] += 1
                r = S.get_review(db, q)
        group_detached = None
        if batch in DETACH_BATCHES:
            # detached already by a reviewer decision (detach_group): the draft stands alone
            gv = Verdict(True, "tách khỏi đoạn gắn nhầm theo quyết định người duyệt") if content.get("group") is None \
                else check_wrong_passage(content)
            if not gv.ok:
                unresolved(gv.reason)
                continue
            if content.get("group") is not None:
                content = json.loads(json.dumps(content))
                group_detached, content["group"] = content["group"], None
        sv = check_source_text(content, page_cache[sc.blob_sha256])
        trimmed = []
        if not sv.ok and "options" in sv.reason:
            fixed, trimmed = trim_options(content, page_cache[sc.blob_sha256])
            if trimmed:
                sv2 = check_source_text(fixed, page_cache[sc.blob_sha256])
                if sv2.ok:
                    content, sv = fixed, sv2
                else:
                    trimmed = []
        if not sv.ok:
            unresolved(sv.reason, conflict=True)
            continue
        if batch in ANSWER_BATCHES and S.dim_view(q, r, v)["status"].get("answer") != "ok":
            stats.setdefault("answer_pending", []).append(q.external_id)
            unresolved("answer needs to be solved independently", record=False)
            continue
        av = check_answer(q, split_answer or v.answer, content, up) if S.dim_view(q, r, v)["status"].get("answer") != "ok" else \
            Verdict(True, "đáp án đã được xác minh độc lập (claude_review)")
        if not av.ok:
            unresolved(av.reason, conflict="disagree" in av.reason or "says" in av.reason)
            continue
        if apply and (q.subject_source == "unclassified" or q.subject_code in (None, "general")) and \
                (q.provenance or {}).get("extraction_method") != "pdf_text_layer":
            # unclassified: take the subject named by the question's own source section when the content agrees
            sec = section_subject((q.provenance or {}).get("section"))
            if sec:
                txt = " ".join(plain_text(content.get(k) or []) for k in ("stem", "solution")) + " " + " ".join(
                    plain_text(o.get("content") or []) for o in content.get("options") or [])
                scores = subject_scores(txt)
                if scores.get(sec, 0) >= max(scores.values()) or max(scores.values()) < 3:
                    S.set_subject(db, q, who, sec, f"[{batch}] môn theo mục của tài liệu gốc: "
                                                   f"“{(q.provenance or {}).get('section')}”", media_root=media_root)
                    S.confirm(db, q, who, "subject", "ok", f"[{batch}] mục tài liệu gốc: "
                              f"“{(q.provenance or {}).get('section')}”", media_root=media_root, method=METHOD)
                    stats["subject_fixed"] += 1
                    v = S.working_version(db, q, r)
        sv_dims = S.dim_view(q, r, v)["status"]
        if batch in NEEDS_VISUAL_CONFIRMATION and sv_dims.get("visual") != "ok":
            stats.setdefault("visual_pending", []).append(q.external_id)
            unresolved("needs visual confirmation of the figure", record=False)
            continue
        if sv_dims.get("answer") != "ok":
            contra = solution_contradicts(v.answer, content)
            if contra:
                unresolved(contra, conflict=True)
                continue
        sj = check_subject(q, content) if sv_dims.get("subject") != "ok" else Verdict(
            True, "môn đã được người duyệt xác nhận")
        if not sj.ok:
            unresolved(sj.reason)
            continue
        if not apply:
            stats["promoted"] += 1
            log(f"  OK   {q.external_id} [{q.subject_code}: {sj.reason}] p.{sv.page}: {sv.reason}; {av.reason}" + ("; NFC fix" if nfc_changed else "")
                + (f"; trimmed {trimmed}" if trimmed else "")
                + ("; watermark removed" if wm_removed else ""))
            continue
        ref = {"document_id": doc, "page": sv.page, "blob_sha256": sc.blob_sha256, "method": METHOD, "batch": batch}
        if nfc_changed or wm_removed or trimmed or group_marked or group_detached or warns_removed or split_answer \
                or passage_recovered:
            what = "; ".join(x for x in ("khôi phục đoạn văn chung bị mất từ tệp .docx gốc (khớp văn bản PDF)"
                                         if passage_recovered else "","tách 4 phương án A–D đang nằm trong đề thành câu trắc nghiệm một đáp án "
                                         f"(đáp án {split_answer['labels'][0]} giữ nguyên)" if split_answer else "",f"bỏ {warns_removed} ô “đối tượng không hiển thị được”/ảnh số trang (tài liệu gốc không có nội dung ở đó)"
                                         if warns_removed else "","nhóm chỉ có lời dẫn, không có đoạn dữ liệu chung (đối chiếu tài liệu gốc)"
                                         if group_marked else "",
                                         f"tách khỏi đoạn chung gắn nhầm ({gv.reason})" if group_detached else "","chuẩn hoá Unicode NFC (không đổi nội dung)" if nfc_changed else "",
                                         "bỏ ảnh quảng cáo/watermark Tailieuchuan.vn (không thuộc câu hỏi)"
                                         if wm_removed else "",
                                         f"cắt dòng thừa dính vào phương án {', '.join(trimmed)} theo đúng tài liệu gốc"
                                         if trimmed else "") if x)
            edit = {"stem": content["stem"], "options": content["options"], "solution": content.get("solution"),
                    "explanation": content.get("explanation")}
            if content.get("group") is not None or group_detached:
                edit["group"] = content["group"]
            if split_answer:
                edit["type"], edit["answer"] = "single_choice", split_answer
            S.save_edit(db, q, who, edit, note=f"[{batch}] {what}", source_ref=ref, media_root=media_root)
            stats["structural_fixed"] += bool(nfc_changed or trimmed or group_marked or group_detached or warns_removed
                                              or split_answer or passage_recovered)
            stats["visual_fixed"] += wm_removed
            v = S.working_version(db, q, r)
        has_vis = bool(C.images(v.content)) or C.has_table(v.content)
        notes = {
            "content": f"[{batch}] {sv.reason} (trang {sv.page})",
            "answer": f"[{batch}] {av.reason}",
            "formula": f"[{batch}] công thức giữ nguyên bản READY_TO_SERVE của ngân hàng; KaTeX hiển thị không lỗi"
            if C.collect_tex(v.content) else f"[{batch}] không có công thức",
            "visual": f"[{batch}] hình/bảng nhúng trong tài liệu gốc, tệp tồn tại" if has_vis else (
                f"[{batch}] chỉ có ảnh watermark (đã bỏ); câu không cần hình" if wm_removed else f"[{batch}] không có hình"),
            "subject": f"[{batch}] {q.subject_code}: {sj.reason}",
            "source": f"[{batch}] {doc} trang {sv.page}, sha256 {sc.blob_sha256[:12]} đã xác minh",
        }
        for dim in C.DIMENSIONS:
            if dim in ("subject", "answer", "visual") and S.dim_view(q, r, v)["status"].get(dim) == "ok":
                continue  # keep the reviewer's confirmation
            state = "ok"
            if dim in ("formula",) and not C.collect_tex(v.content):
                state = "na"
            if dim == "visual" and not has_vis:
                state = "na"
            m = "claude_review" if (dim == "answer" and "claude_review" in av.reason) or \
                (dim == "subject" and "người duyệt" in sj.reason) else METHOD
            S.confirm(db, q, who, dim, state, notes[dim], media_root=media_root, method=m)
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


def apply_decisions(db: Session, decisions: list[dict], media_root=None, log=print) -> dict:
    """Reviewer decisions made by reading the question (and its source): [{"cq_id", "subject"?, "note",
    "confirm_subject"?: bool}]. A subject change is an app override (never written upstream)."""
    who = actor(db)
    n = {"subject_fixed": 0, "subject_confirmed": 0, "missing": 0}
    for d in decisions:
        q = db.scalar(select(Question).where(Question.external_id == d["cq_id"]))
        if q is None:
            n["missing"] += 1
            continue
        note = f"[claude_review] {d['note']}"
        if d.get("revert"):
            S.revert_publication(db, q, who, note, media_root=media_root)
            n["reverted"] = n.get("reverted", 0) + 1
            db.commit()
            log(f"  {q.external_id}: reverted, served={q.is_served}")
            continue
        if d.get("clear_subject"):
            S.set_subject(db, q, who, None, note, media_root=media_root)
            n["subject_cleared"] = n.get("subject_cleared", 0) + 1
        if d.get("detach_group"):  # read: the passage belongs to other questions, this one stands on its own
            S.save_edit(db, q, who, {"group": None}, note=note, media_root=media_root)
            n["group_detached"] = n.get("group_detached", 0) + 1
        if d.get("clean_furniture"):  # served question: drop section headers/dividers glued to its options
            r = S.get_review(db, q)
            w = S.working_version(db, q, r)
            cleaned, changed = strip_furniture(w.content)
            if not changed or option_section_head(cleaned):
                raise ValueError(f"{q.external_id}: clean_furniture changed nothing or left a section header")
            before = S.dim_view(q, r, w)["status"]
            S.save_edit(db, q, who, {"stem": cleaned["stem"], "options": cleaned["options"]}, note=note,
                        media_root=media_root)
            r = S.get_review(db, q)
            after = S.dim_view(q, r, S.working_version(db, q, r))["status"]
            upstream_ready = q.review_gate is None and q.editorial_state == "READY_TO_SERVE"
            for dim in C.DIMENSIONS:
                st = before.get(dim) if before.get(dim) in ("ok", "na") else ("ok" if upstream_ready else None)
                if st and after.get(dim) != st:
                    why = note if before.get(dim) in ("ok", "na") else \
                        f"{note} — nội dung khác giữ nguyên bản READY_TO_SERVE của ngân hàng (đang được phục vụ)"
                    if dim in ("formula",) and not C.collect_tex(cleaned):
                        st = "na"
                    if dim == "visual" and not (C.images(cleaned) or C.has_table(cleaned)):
                        st = "na"
                    S.confirm(db, q, who, dim, st, why, media_root=media_root,
                              method="claude_review" if before.get(dim) in ("ok", "na") else "upstream_ready")
            S.approve(db, q, who, note, media_root=media_root)
            n["furniture_cleaned"] = n.get("furniture_cleaned", 0) + 1
        if d.get("text_fix"):  # {"part": "stem", "old": ..., "new": ...}: one exact run of text, verified against the source
            fx = d["text_fix"]
            r = S.get_review(db, q)
            blocks = json.loads(json.dumps(S.working_version(db, q, r).content.get(fx["part"]) or []))
            hits = [n for b in blocks for n in b.get("c") or [] if n.get("t") == "s" and fx["old"] in n.get("v", "")]
            if len(hits) != 1 or hits[0]["v"].count(fx["old"]) != 1:
                raise ValueError(f"{q.external_id}: text_fix needs exactly one match, found {len(hits)}")
            hits[0]["v"] = hits[0]["v"].replace(fx["old"], fx["new"])
            before = S.dim_view(q, r, S.working_version(db, q, r))["status"]
            S.save_edit(db, q, who, {fx["part"]: blocks}, note=note, media_root=media_root)
            r = S.get_review(db, q)
            after = S.dim_view(q, r, S.working_version(db, q, r))["status"]
            for dim, st in before.items():  # the edit re-hashes the content; only checks that passed before
                if st in ("ok", "na") and after.get(dim) != st:
                    S.confirm(db, q, who, dim, st, note, media_root=media_root, method="claude_review")
            S.approve(db, q, who, note, media_root=media_root)
            n["text_fixed"] = n.get("text_fixed", 0) + 1
        if d.get("answer"):
            r = S.get_review(db, q)
            if q.review_gate == "ready" and not r.draft_version_id:
                pass  # the published version is edited through a draft and re-approved below
            labels = d["answer"] if isinstance(d["answer"], list) else [d["answer"]]
            S.save_edit(db, q, who, {"answer": {"kind": "choice", "labels": labels}}, note=note,
                        media_root=media_root)
            S.confirm(db, q, who, "answer", "ok", note, media_root=media_root, method="claude_review")
            n["answer_fixed"] = n.get("answer_fixed", 0) + 1
            view = S.dim_view(q, r, S.working_version(db, q, r))
            if view["complete"]:
                S.approve(db, q, who, note, media_root=media_root)
        if d.get("subject") and d["subject"] != q.subject_code:
            S.set_subject(db, q, who, d["subject"], note, media_root=media_root)
            n["subject_fixed"] += 1
        if d.get("confirm_visual"):
            S.confirm(db, q, who, "visual", "ok", note, media_root=media_root, method="claude_review")
            n["visual_confirmed"] = n.get("visual_confirmed", 0) + 1
        if d.get("visual_fail"):  # figure is a worked solution / gives the answer away / unreadable
            S.confirm(db, q, who, "visual", "fail", note, media_root=media_root, method="claude_review")
            n["visual_failed"] = n.get("visual_failed", 0) + 1
        if d.get("confirm_answer"):
            S.confirm(db, q, who, "answer", "ok", note, media_root=media_root, method="claude_review")
            n["answer_confirmed"] = n.get("answer_confirmed", 0) + 1
        if d.get("subject") or d.get("confirm_subject"):
            S.confirm(db, q, who, "subject", "ok", note, media_root=media_root, method="claude_review")
            n["subject_confirmed"] += 1
        db.commit()
        log(f"  {q.external_id}: subject={q.subject_code}")
    return n
