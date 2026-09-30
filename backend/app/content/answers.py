"""Answer normalisation and input specification.

An answer exists only when the source provided it (answer_source_type SOURCE_PROVIDED_ANSWER /
SOURCE_PROVIDED_SOLUTION, or an editorial overlay that transcribed source evidence). Unresolved
or automatically inferred candidates produce `None` — the app never guesses.

Normalised answers:
  {"kind":"choice","labels":["C"]}                  single/multiple choice, error identification
  {"kind":"numeric","value":9.0,"text":"9","unit":null}
  {"kind":"text","text":"...","accepted":["..."]}     short response (self-check unless configured)
  {"kind":"tf_sequence","values":[true,false,...]}    true/false statements a) b) c) d)
  {"kind":"boolean","value":true}                    single true/false without options

Input spec (what the student enters):
  {"kind":"choice","multi":false} | {"kind":"numeric"} | {"kind":"text"} |
  {"kind":"tf_sequence","n":4} | {"kind":"boolean"} | {"kind":"none"}
"""
import re
import unicodedata

TRUSTED_SOURCES = {"SOURCE_PROVIDED_ANSWER", "SOURCE_PROVIDED_SOLUTION", "EDITORIAL_TRANSCRIBED", "IMPORTED"}
CHOICE_TYPES = {"single_choice", "multiple_choice", "error_identification"}
TRUE_WORDS = {"đúng", "d", "đ", "true", "t", "yes", "có"}
FALSE_WORDS = {"sai", "s", "false", "f", "no", "không"}
NUM_RE = re.compile(r"^[+\-−]?\d+(?:[.,]\d+)?$")


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").strip().lower()
    s = s.replace("−", "-").replace("–", "-")
    s = re.sub(r"\s+", " ", s)
    return s.rstrip(".")


def parse_number(s) -> float | None:
    """Vietnamese and English number formats: '1,5' '1.5' '-2' '3/4' ; returns None if not a number."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = norm_text(str(s)).replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"([+\-]?\d+)/(\d+)", t)
    if m and int(m.group(2)) != 0:
        return int(m.group(1)) / int(m.group(2))
    if re.fullmatch(r"[+\-]?\d{1,3}(\.\d{3})+", t):       # 1.000.000 (thousands separators)
        t = t.replace(".", "")
    elif re.fullmatch(r"[+\-]?\d{1,3}(,\d{3}){2,}", t):   # 1,000,000
        t = t.replace(",", "")
    t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def statement_count(stem_md: str) -> int:
    """Number of a) b) c) d) statements in a true/false-statements stem."""
    labels = re.findall(r"(?:^|\n)\s*\**([a-h])\)", stem_md or "")
    seen = []
    for l in labels:
        if l not in seen:
            seen.append(l)
    return len(seen)


def input_spec(qtype: str, options: list, answer: dict | None, stem_md: str = "") -> dict:
    if answer and answer["kind"] == "choice":
        return {"kind": "choice", "multi": qtype == "multiple_choice" or len(answer["labels"]) > 1}
    if qtype in CHOICE_TYPES or (qtype == "true_false" and options):
        return {"kind": "choice", "multi": qtype == "multiple_choice"} if options else {"kind": "none"}
    if answer and answer["kind"] == "tf_sequence":
        return {"kind": "tf_sequence", "n": len(answer["values"])}
    if answer and answer["kind"] == "numeric":
        return {"kind": "numeric"}
    if qtype == "true_false_statements":
        n = statement_count(stem_md)
        return {"kind": "tf_sequence", "n": n} if n else {"kind": "none"}
    if qtype == "true_false":
        return {"kind": "boolean"}
    if qtype == "numeric_response":
        return {"kind": "numeric"}
    if qtype == "short_response":
        return {"kind": "text"}
    return {"kind": "none"}  # constructed_response, open_or_unknown


def normalize_answer(qtype: str, raw: dict | None, source_type: str | None, option_labels: list[str],
                     option_texts: dict[str, str] | None = None) -> tuple[dict | None, str | None]:
    """Return (answer, problem). problem is a policy reason when a provided answer is unusable."""
    if not raw or source_type not in TRUSTED_SOURCES:
        return None, None
    kind = raw.get("kind")
    option_texts = option_texts or {}
    if kind == "labels":
        labels = sorted(set(raw.get("labels") or []))
        if not labels:
            return None, "answer_empty"
        if option_labels and not set(labels) <= set(option_labels):
            return None, "answer_not_in_options"
        if not option_labels:
            return None, "answer_without_options"
        return {"kind": "choice", "labels": labels}, None
    if kind == "numeric":
        v = raw.get("value")
        if v is None:
            v = parse_number(raw.get("text"))
        if v is None:
            return None, "answer_unparseable"
        if qtype == "true_false_statements":
            # "how many statements are true": numeric answer to a counting question
            return {"kind": "numeric", "value": float(v), "text": str(raw.get("text") or v), "unit": None}, None
        return {"kind": "numeric", "value": float(v), "text": str(raw.get("text") or v),
                "unit": raw.get("unit")}, None
    if kind == "true_false_sequence":
        vals = raw.get("raw_values") or raw.get("values") or []
        out = []
        for x in vals:
            if isinstance(x, bool):
                out.append(x)
                continue
            w = norm_text(str(x))
            if w in TRUE_WORDS:
                out.append(True)
            elif w in FALSE_WORDS:
                out.append(False)
            else:
                return None, "answer_unparseable"
        return ({"kind": "tf_sequence", "values": out}, None) if out else (None, "answer_empty")
    if kind == "text":
        text = (raw.get("text") or "").strip()
        if not text:
            return None, "answer_empty"
        if option_labels:
            # a choice question whose key was captured as text: accept only an exact label or option text
            t = text.strip().rstrip(".").upper()
            if t in option_labels:
                return {"kind": "choice", "labels": [t]}, None
            by_text = [l for l, c in option_texts.items() if norm_text(c) == norm_text(text)]
            if len(by_text) == 1:
                return {"kind": "choice", "labels": by_text}, None
            return None, "answer_not_in_options"
        if qtype == "true_false":
            w = norm_text(text)
            if w in TRUE_WORDS:
                return {"kind": "boolean", "value": True}, None
            if w in FALSE_WORDS:
                return {"kind": "boolean", "value": False}, None
            return None, "answer_unparseable"
        if qtype == "true_false_statements":
            parts = re.findall(r"[a-h]\)?\s*[-:]?\s*(đúng|sai|đ|s)\b", norm_text(text))
            if parts:
                return {"kind": "tf_sequence", "values": [p in ("đúng", "đ") for p in parts]}, None
            return None, "answer_unparseable"
        n = parse_number(text)
        if n is not None and NUM_RE.match(norm_text(text).replace(" ", "")):
            return {"kind": "numeric", "value": n, "text": text, "unit": raw.get("unit")}, None
        return {"kind": "text", "text": text, "accepted": [text]}, None
    return None, "answer_kind_unknown"


def scoring_mode(inp: dict, answer: dict | None, has_solution: bool, text_auto: bool = False) -> str:
    """auto: the app can score deterministically; self_check: the student compares with the key or
    the worked solution; none: nothing to compare with."""
    if answer:
        if answer["kind"] == "text" and not text_auto:
            return "self_check"
        if inp["kind"] == "none":
            return "self_check"
        return "auto"
    return "self_check" if has_solution else "none"


LABEL_REF = re.compile(r"(?:\b(?:cả|câu|đáp án|phương án|ý|both|all|none|only)\s+[A-H]\b)|\b[A-H]\s*(?:và|,|and|&|hoặc|or)\s*[A-H]\b|"
                       r"tất cả|các (?:đáp án|phương án|ý) trên|cả \w+ (?:đáp án|phương án|ý)|đều (?:đúng|sai)|"
                       r"không có (?:đáp án|phương án)|all of the above|none of the above|both .* and", re.I)


def shuffle_safe(qtype: str, option_texts: dict[str, str]) -> bool:
    """Options may be re-ordered only when no option refers to other options or to its position."""
    if qtype == "error_identification" or not option_texts or len(option_texts) < 2:
        return False
    return not any(LABEL_REF.search(t or "") for t in option_texts.values())
