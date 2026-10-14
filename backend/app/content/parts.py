"""Multi-part questions ("Nhiều ý / Câu ghép"): one shared stem and several child items.

Content (question_version.content, type "multi_part"):
  "stem":  [blocks]                    the shared stem (context, data, figure)
  "parts": [{"key": "p1", "label": "a",
             "prompt": [blocks],            the item's own question
             "answer_type": "choice" | "numeric" | "text" | "formula" | "boolean",
             "multi": false,                choice items only: several correct options
             "options": [{"label": "A", "content": [blocks]}],   choice items only
             "solution": [blocks] | null,   the item's own worked solution
             "points": 1.0}]                the item's weight in the question's score
  "options": []                          the parent has no options
  "solution": [blocks] | null            an optional solution of the whole question

Answer (question_version.answer): the parent has no single answer, only one per item:
  {"kind": "parts", "parts": {"p1": {"kind": "choice", "labels": ["B"]},
                              "p2": {"kind": "numeric", "value": 2.5, "text": "2,5", "unit": null},
                              "p3": {"kind": "text", "text": "...", "accepted": [...]},
                              "p4": {"kind": "formula", "tex": "x^2+1", "accepted": [...]},
                              "p5": {"kind": "boolean", "value": true}}}

Input spec: {"kind": "parts", "parts": [{"key": "p1", "kind": "choice", "multi": false}, ...]}
Response:   {"parts": {"p1": {"labels": ["B"]}, "p2": {"value": "2,5"}, "p4": {"text": "x^2+1"}, ...}}

Each item is scored with the rules of its kind; the question's score is the points-weighted share of its
items (outcome correct / partial / incorrect / unanswered).
"""
from __future__ import annotations

import re

from . import answers as A

QTYPE = "multi_part"
ANSWER_TYPES = ("choice", "numeric", "text", "formula", "boolean")
ANSWER_TYPE_LABELS = {"choice": "Chọn phương án", "numeric": "Số", "text": "Chữ", "formula": "Công thức",
                      "boolean": "Đúng/Sai"}
MAX_PARTS = 12
KEY_RE = re.compile(r"^p\d{1,3}$")


def is_multi(content: dict | None) -> bool:
    return bool(content) and content.get("type") == QTYPE


def parts_of(content: dict | None) -> list[dict]:
    return list((content or {}).get("parts") or []) if is_multi(content) else []


def part_input(p: dict) -> dict:
    t = p.get("answer_type")
    if t == "choice":
        return {"kind": "choice", "multi": bool(p.get("multi"))}
    if t in ("numeric", "text", "formula", "boolean"):
        return {"kind": t}
    return {"kind": "none"}


def input_spec(content: dict) -> dict:
    return {"kind": "parts", "parts": [{"key": p["key"], **part_input(p)} for p in parts_of(content)]}


def part_blocks(content: dict) -> list:
    """Every block list of the items (prompts, options, solutions), for walkers over the render model."""
    out = []
    for p in parts_of(content):
        out.append(p.get("prompt"))
        out.append([o.get("content") for o in p.get("options") or []])
        out.append(p.get("solution"))
    return out


# ------------------------------------------------------------------------------------------------
# answers
# ------------------------------------------------------------------------------------------------
def norm_tex(t: str) -> str:
    """A formula answer compared up to spacing and equivalent spellings (\\dfrac/\\frac, \\left/\\right,
    braces around one character)."""
    t = (t or "").strip().strip("$")
    t = re.sub(r"\\(?:left|right)\b", "", t)
    t = re.sub(r"\\[dt]frac", r"\\frac", t)
    t = t.replace("\\cdot", "*").replace("\\times", "*").replace("−", "-").replace(",", ".")
    t = re.sub(r"\\[,;:! ]|~", "", t)
    t = re.sub(r"\s+", "", t)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\{(\\?[A-Za-z0-9])\}", r"\1", t)
    return t


class PartError(ValueError):
    pass


def normalize_part_answer(p: dict, raw: dict | None) -> dict | None:
    """Reviewer-entered answer of one item → normalised; None when not given. Raises PartError."""
    if not raw:
        return None
    t = p.get("answer_type")
    lab = p.get("label") or p.get("key")
    if t == "choice":
        labels = [o["label"] for o in p.get("options") or []]
        ls = sorted({str(x).strip().upper() for x in raw.get("labels") or [] if str(x).strip()})
        if not ls:
            return None
        bad = [x for x in ls if x not in labels]
        if bad:
            raise PartError(f"Ý {lab}: đáp án {', '.join(bad)} không có trong các phương án của ý.")
        if not p.get("multi") and len(ls) > 1:
            raise PartError(f"Ý {lab}: chỉ có một phương án đúng (bật 'nhiều đáp án' nếu cần).")
        return {"kind": "choice", "labels": ls}
    if t == "numeric":
        text = str(raw.get("text") if raw.get("text") not in (None, "") else raw.get("value") or "").strip()
        if not text:
            return None
        v = A.parse_number(text)
        if v is None:
            raise PartError(f"Ý {lab}: đáp số không phải là số hợp lệ.")
        return {"kind": "numeric", "value": v, "text": text, "unit": (raw.get("unit") or None)}
    if t == "text":
        text = str(raw.get("text") or "").strip()
        if not text:
            return None
        acc = [str(x).strip() for x in raw.get("accepted") or [] if str(x).strip()]
        return {"kind": "text", "text": text, "accepted": acc or [text]}
    if t == "formula":
        tex = str(raw.get("tex") or raw.get("text") or "").strip().strip("$")
        if not tex:
            return None
        acc = [str(x).strip().strip("$") for x in raw.get("accepted") or [] if str(x).strip()]
        return {"kind": "formula", "tex": tex, "accepted": acc or [tex]}
    if t == "boolean":
        if raw.get("value") not in (True, False):
            return None
        return {"kind": "boolean", "value": bool(raw["value"])}
    raise PartError(f"Ý {lab}: dạng đáp án không hỗ trợ.")


def normalize_answer(content: dict, raw: dict | None) -> dict | None:
    """{"kind":"parts","parts":{key: raw}} → normalised (items without an answer are left out)."""
    if not raw:
        return None
    if raw.get("kind") != "parts" or not isinstance(raw.get("parts"), dict):
        raise PartError("Câu nhiều ý có đáp án riêng cho từng ý.")
    out = {}
    for p in parts_of(content):
        a = normalize_part_answer(p, raw["parts"].get(p["key"]))
        if a is not None:
            out[p["key"]] = a
    return {"kind": "parts", "parts": out} if out else None


def missing_answers(content: dict, answer: dict | None) -> list[str]:
    have = ((answer or {}).get("parts") or {}) if (answer or {}).get("kind") == "parts" else {}
    return [p.get("label") or p["key"] for p in parts_of(content) if p["key"] not in have]


# ------------------------------------------------------------------------------------------------
# structure checks (Review Studio edit, validators)
# ------------------------------------------------------------------------------------------------
def problems(content: dict, answer: dict | None) -> list[str]:
    """Structural problems of a multi-part question (Vietnamese, for the reviewer)."""
    out = []
    ps = parts_of(content)
    if len(ps) < 1:
        out.append("Câu nhiều ý cần ít nhất một ý.")
    keys = [p.get("key") for p in ps]
    if len(set(keys)) != len(keys):
        out.append("Khoá của các ý bị trùng.")
    for p in ps:
        lab = p.get("label") or p.get("key")
        if not p.get("prompt"):
            out.append(f"Ý {lab}: thiếu nội dung câu hỏi.")
        if p.get("answer_type") not in ANSWER_TYPES:
            out.append(f"Ý {lab}: chưa chọn dạng trả lời.")
        if p.get("answer_type") == "choice":
            opts = p.get("options") or []
            if len(opts) < 2:
                out.append(f"Ý {lab}: cần ít nhất hai phương án.")
            if any(not o.get("content") for o in opts):
                out.append(f"Ý {lab}: có phương án trống.")
    for lab in missing_answers(content, answer):
        out.append(f"Ý {lab}: chưa có đáp án.")
    return out


# ------------------------------------------------------------------------------------------------
# student responses and scoring
# ------------------------------------------------------------------------------------------------
def blank(resp: dict | None) -> bool:
    if not resp:
        return True
    if "labels" in resp:
        return not resp["labels"]
    if "value" in resp:
        return resp["value"] is None or (isinstance(resp["value"], str) and not resp["value"].strip())
    if "text" in resp:
        return not (resp["text"] or "").strip()
    return True


def all_blank(resp: dict | None) -> bool:
    return not resp or not isinstance(resp.get("parts"), dict) or all(blank(r) for r in resp["parts"].values())


def validate_response(inp: dict, resp: dict, content: dict, validate_one) -> dict:
    """{"parts": {key: response}} checked item by item with the single-item validator of the exam service."""
    got = resp.get("parts")
    if not isinstance(got, dict):
        raise ValueError("parts expected")
    spec = {s["key"]: s for s in inp.get("parts") or []}
    opts = {p["key"]: [o["label"] for o in p.get("options") or []] for p in parts_of(content)}
    out = {}
    for key, r in got.items():
        if key not in spec:
            continue
        s = spec[key]
        one = {"kind": "text"} if s["kind"] == "formula" else s
        out[key] = validate_one(one, r, opts.get(key) or [])
    return {"parts": out}


def fraction_one(answer: dict, resp: dict, cfg: dict, fraction_correct) -> float:
    if answer["kind"] == "formula":
        got = norm_tex(resp.get("text") or "")
        return 1.0 if got and any(norm_tex(a) == got for a in answer.get("accepted") or [answer["tex"]]) else 0.0
    return fraction_correct(answer, resp, cfg)


def results(content: dict, answer: dict | None, resp: dict | None, cfg: dict, fraction_correct) -> list[dict]:
    """Per item: {key, label, outcome, fraction, points (weight), awarded}."""
    have = ((answer or {}).get("parts") or {})
    got = ((resp or {}).get("parts") or {})
    out = []
    for p in parts_of(content):
        w = float(p.get("points") or 1.0)
        a, r = have.get(p["key"]), got.get(p["key"])
        if a is None:
            out.append({"key": p["key"], "label": p.get("label"), "outcome": "ungraded", "fraction": 0.0,
                        "points": w, "awarded": 0.0})
            continue
        if blank(r):
            out.append({"key": p["key"], "label": p.get("label"), "outcome": "unanswered", "fraction": 0.0,
                        "points": w, "awarded": 0.0})
            continue
        f = fraction_one(a, r, cfg, fraction_correct)
        oc = "correct" if f >= 1.0 else ("partial" if f > 0 else "incorrect")
        out.append({"key": p["key"], "label": p.get("label"), "outcome": oc, "fraction": f, "points": w,
                    "awarded": round(f * w, 6)})
    return out


def fraction(content: dict, answer: dict, resp: dict, cfg: dict, fraction_correct) -> float:
    """Points-weighted share of the items answered correctly (items without a key do not count)."""
    rs = [r for r in results(content, answer, resp, cfg, fraction_correct) if r["outcome"] != "ungraded"]
    total = sum(r["points"] for r in rs)
    return sum(r["awarded"] for r in rs) / total if total > 0 else 0.0


def scoring_mode(content: dict, answer: dict | None) -> str:
    """auto when every item has a key and a deterministic rule (text items count as self-check)."""
    ps = parts_of(content)
    have = ((answer or {}).get("parts") or {})
    if not ps or any(p["key"] not in have for p in ps):
        return "self_check" if any(p.get("solution") for p in ps) or content.get("solution") else "none"
    return "auto"


def public_parts(content: dict) -> list[dict]:
    """Items as shown to a student before the answers are revealed (no solutions)."""
    out = []
    for p in parts_of(content):
        q = {"key": p["key"], "label": p.get("label"), "prompt": p.get("prompt") or [],
             "input": part_input(p), "points": float(p.get("points") or 1.0)}
        if p.get("answer_type") == "choice":
            q["options"] = [{"key": o["label"], "display": o["label"], "content": o.get("content") or []}
                            for o in p.get("options") or []]
        out.append(q)
    return out
