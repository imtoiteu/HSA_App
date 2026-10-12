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
    m = FIGURE_HINT.search(txt)
    if not m:
        return Verdict(True)
    if C.images(content) or C.has_table(content):
        return Verdict(True)
    opts = " ".join(plain_text(o.get("content") or []) for o in content.get("options") or [])
    if m.group(1).lower().startswith("sơ đồ") and re.search(r"[→⟶⇒]|->", txt + " " + opts):
        return Verdict(True)  # the scheme is written out (in the stem or as the options)
    return Verdict(False, f"incomplete: refers to “{m.group(1)}” but the question has no figure/table")


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
                  "số oxi hóa", "đồng vị", "hóa trị", "hoá trị", "nồng độ"],
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
    if high and not contradict:
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
)]


def strip_furniture(content: dict) -> tuple[dict, bool]:
    import json
    c = json.loads(json.dumps(content))
    changed = False

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
TRANSFORMS = {"watermark_only": "watermark"}


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

        st = check_structure(v.content)
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
        wm_removed = False
        if TRANSFORMS.get(batch) == "watermark":
            content, wm_removed = strip_watermarks(content)
            if C.images(content) or C.has_table(content):
                unresolved("other figures remain after removing the watermark (needs a visual check)")
                continue
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
        av = check_answer(q, v.answer, content, up) if S.dim_view(q, r, v)["status"].get("answer") != "ok" else \
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
        if nfc_changed or wm_removed or trimmed:
            what = "; ".join(x for x in ("chuẩn hoá Unicode NFC (không đổi nội dung)" if nfc_changed else "",
                                         "bỏ ảnh quảng cáo/watermark Tailieuchuan.vn (không thuộc câu hỏi)"
                                         if wm_removed else "",
                                         f"cắt dòng thừa dính vào phương án {', '.join(trimmed)} theo đúng tài liệu gốc"
                                         if trimmed else "") if x)
            edit = {"stem": content["stem"], "options": content["options"], "solution": content.get("solution"),
                    "explanation": content.get("explanation")}
            if content.get("group") is not None:
                edit["group"] = content["group"]
            S.save_edit(db, q, who, edit, note=f"[{batch}] {what}", source_ref=ref, media_root=media_root)
            stats["structural_fixed"] += bool(nfc_changed or trimmed)
            stats["visual_fixed"] += wm_removed
            v = S.working_version(db, q, r)
        has_vis = bool(C.images(v.content)) or C.has_table(v.content)
        notes = {
            "content": f"[{batch}] {sv.reason} (trang {sv.page})",
            "answer": f"[{batch}] {av.reason}",
            "formula": f"[{batch}] không có công thức",
            "visual": f"[{batch}] hình/bảng nhúng trong tài liệu gốc, tệp tồn tại" if has_vis else (
                f"[{batch}] chỉ có ảnh watermark (đã bỏ); câu không cần hình" if wm_removed else f"[{batch}] không có hình"),
            "subject": f"[{batch}] {q.subject_code}: {sj.reason}",
            "source": f"[{batch}] {doc} trang {sv.page}, sha256 {sc.blob_sha256[:12]} đã xác minh",
        }
        for dim in C.DIMENSIONS:
            if dim in ("subject", "answer") and S.dim_view(q, r, v)["status"].get(dim) == "ok":
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
        if d.get("confirm_answer"):
            S.confirm(db, q, who, "answer", "ok", note, media_root=media_root, method="claude_review")
            n["answer_confirmed"] = n.get("answer_confirmed", 0) + 1
        if d.get("subject") or d.get("confirm_subject"):
            S.confirm(db, q, who, "subject", "ok", note, media_root=media_root, method="claude_review")
            n["subject_confirmed"] += 1
        db.commit()
        log(f"  {q.external_id}: subject={q.subject_code}")
    return n
