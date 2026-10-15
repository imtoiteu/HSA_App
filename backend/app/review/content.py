"""Content helpers of the Review Studio: semantic part hashes, QA dimensions, sanitising editor input and the
question fields derived from a content version.

QA dimensions and the content parts each one covers. A confirmation stores the hash of its parts; it stays
valid exactly as long as those parts are unchanged, so an edit invalidates only the dimensions it touches
(changing the subject never re-opens the visual check, changing the answer re-opens ANSWER_OK only).
"""
import hashlib
import json
import re

from ..content import answers as A
from ..content import parts as P
from ..content.render import plain_text
from ..sync.hsa_upstream import _has_warn
from ..sync.subjects import collect_tex

DIMENSIONS = ["content", "answer", "formula", "visual", "subject", "source"]
DIM_LABELS = {"content": "Nội dung", "answer": "Đáp án", "formula": "Công thức", "visual": "Hình",
              "subject": "Môn", "source": "Nguồn"}
DIM_PARTS = {
    "content": ["type", "stem", "options", "solution", "group"],
    "answer": ["answer", "options", "type"],
    "formula": ["formula"],
    "visual": ["visual"],
    "subject": ["subject"],
    "source": ["source"],
}
# a dimension confirmation is one of these
DIM_STATES = ("ok", "na", "fail")

QTYPES = ["single_choice", "multiple_choice", "true_false", "true_false_statements", "numeric_response",
          "short_response", "error_identification", "constructed_response", "open_or_unknown", "multi_part"]
MARKS = {"b", "i", "u", "x", "sup", "sub"}
SRC_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}\.(png|jpg|gif|webp|svg)$")
MAX_TEXT = 20000
MAX_CONTENT_BYTES = 600_000


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def walk(nodes):
    """Yield every node (dict) of a render-model subtree."""
    if isinstance(nodes, list):
        for n in nodes:
            yield from walk(n)
    elif isinstance(nodes, dict):
        yield nodes
        for k in ("c", "rows", "items", "content", "header", "passage"):
            if k in nodes:
                yield from walk(nodes[k])


def content_nodes(content: dict) -> list:
    return [content.get("stem"), [o.get("content") for o in content.get("options") or []],
            content.get("group"), content.get("solution"), content.get("explanation")] + P.part_blocks(content)


def images(content: dict) -> list[str]:
    return sorted({n["src"] for n in walk(content_nodes(content)) if n.get("t") == "img" and n.get("src")})


def has_table(content: dict) -> bool:
    return any(n.get("t") == "table" for n in walk(content_nodes(content)))


def unvalidated_fragments(content: dict) -> int:
    """Raster formula fragments and placeholders the upstream renderer could not resolve."""
    return sum(1 for n in walk(content_nodes(content)) if n.get("t") == "warn" or n.get("review"))


def part_hashes(content: dict, answer: dict | None, subject: str | None, provenance: dict | None) -> dict:
    tex = sorted(collect_tex(content))
    visual = [images(content), [n.get("rows") and len(n["rows"]) for n in walk(content_nodes(content))
                                if n.get("t") == "table"]]
    prov = provenance or {}
    return {
        "type": h(content.get("type")),
        # a multi-part question's items belong to its stem (prompts, solutions) and options (answer types,
        # options); other questions keep exactly the hashes they always had
        "stem": h(content.get("stem")) if not P.is_multi(content) else
        h([content.get("stem"), [[p.get("label"), p.get("prompt"), p.get("solution"), p.get("points")]
                                 for p in P.parts_of(content)]]),
        "options": h(content.get("options")) if not P.is_multi(content) else
        h([content.get("options"), [[p.get("key"), p.get("answer_type"), p.get("multi"), p.get("options")]
                                    for p in P.parts_of(content)]]),
        "solution": h([content.get("solution"), content.get("explanation")]),
        "group": h(content.get("group")),
        "answer": h(answer),
        "formula": h([tex, unvalidated_fragments(content)]),
        "visual": h(visual),
        "subject": h(subject),
        "source": h([prov.get("document_id"), prov.get("representative_occurrence_id")]),
    }


def semantic_hash(parts: dict) -> str:
    return h(parts)


def dim_hash(dim: str, parts: dict) -> str:
    return h([parts.get(p) for p in DIM_PARTS[dim]])


def required_dims(content: dict, states: list | None = None) -> dict:
    """Which dimensions need a human decision. FORMULA is not required without formulas (and no unresolved
    formula fragments); VISUAL is not required without figures/tables and no visual review state."""
    states = states or []
    has_math = bool(collect_tex(content)) or unvalidated_fragments(content) > 0
    has_visual = bool(images(content)) or has_table(content) or "NEEDS_VISUAL_REVIEW" in states
    return {"content": True, "answer": True, "formula": has_math or "NEEDS_FORMULA_REVIEW" in states,
            "visual": has_visual, "subject": True, "source": True}


def dim_status(dims: dict, parts: dict, required: dict) -> dict:
    """Effective state per dimension: ok | na | fail | pending | stale (confirmed for older content)."""
    out = {}
    for d in DIMENSIONS:
        rec = (dims or {}).get(d)
        if rec and rec.get("h") == dim_hash(d, parts):
            out[d] = rec.get("s", "pending")
        elif rec:
            out[d] = "stale"
        else:
            out[d] = "pending" if required.get(d, True) else "na"
    return out


def dims_complete(status: dict) -> bool:
    return all(s in ("ok", "na") for s in status.values())


# ------------------------------------------------------------------------------------------------
# sanitising editor input (the editor posts the render model directly)
# ------------------------------------------------------------------------------------------------
class ContentError(ValueError):
    pass


def _s(v, limit=MAX_TEXT) -> str:
    if not isinstance(v, str):
        raise ContentError("text expected")
    if len(v) > limit:
        raise ContentError("text too long")
    return v


def _inline(n: dict, known_src) -> dict:
    t = n.get("t")
    if t == "s":
        out = {"t": "s", "v": _s(n.get("v", ""))}
        m = [x for x in (n.get("m") or []) if x in MARKS]
        if m:
            out["m"] = sorted(set(m))
        return out
    if t == "m":
        return {"t": "m", "tex": _s(n.get("tex", ""), 4000)}
    if t in ("br", "tab", "range"):
        return {"t": t}
    if t == "warn":
        return {"t": "warn", "v": _s(n.get("v", ""), 200)}
    if t == "img":
        src = _s(n.get("src", ""), 200)
        if not SRC_RE.match(src) or not known_src(src):
            raise ContentError(f"unknown image {src}")
        out = {"t": "img", "src": src, "inline": True}
        for k in ("w", "h"):
            if isinstance(n.get(k), (int, float)) and 0 < n[k] < 5000:
                out[k] = int(n[k])
        if n.get("review"):
            out["review"] = True
        return out
    raise ContentError(f"unknown inline node {t!r}")


def sanitize_blocks(blocks, known_src, depth=0) -> list:
    if blocks is None:
        return []
    if not isinstance(blocks, list) or depth > 6:
        raise ContentError("blocks expected")
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            raise ContentError("block expected")
        t = b.get("t")
        if t == "p":
            c = [_inline(n, known_src) for n in b.get("c") or [] if isinstance(n, dict)]
            if not c:
                continue
            p = {"t": "p", "c": c}
            if len(c) == 1 and c[0]["t"] == "m":
                p["display"] = bool(b.get("display", True))
            out.append(p)
        elif t == "img":
            src = _s(b.get("src", ""), 200)
            if not SRC_RE.match(src) or not known_src(src):
                raise ContentError(f"unknown image {src}")
            img = {"t": "img", "src": src}
            for k in ("w", "h"):
                if isinstance(b.get(k), (int, float)) and 0 < b[k] < 5000:
                    img[k] = int(b[k])
            for k in ("alt", "caption"):
                if b.get(k):
                    img[k] = _s(b[k], 500)
            out.append(img)
        elif t == "table":
            rows = []
            for r in b.get("rows") or []:
                row = []
                for cell in r or []:
                    cc = {"c": sanitize_blocks(cell.get("c"), known_src, depth + 1)}
                    for k in ("colspan", "rowspan"):
                        if isinstance(cell.get(k), int) and 1 < cell[k] <= 50:
                            cc[k] = cell[k]
                    row.append(cc)
                if row:
                    rows.append(row)
            if rows:
                out.append({"t": "table", "rows": rows})
        elif t == "list":
            items = [sanitize_blocks(i, known_src, depth + 1) for i in b.get("items") or []]
            items = [i for i in items if i]
            if items:
                out.append({"t": "list", "ordered": bool(b.get("ordered")), "items": items})
        elif t == "warn":
            out.append({"t": "warn", "v": _s(b.get("v", ""), 200)})
        else:
            raise ContentError(f"unknown block {t!r}")
    return out


def normalize_answer(qtype: str, raw: dict | None, labels: list[str], content: dict | None = None) -> dict | None:
    """Reviewer-entered answer → normalised answer (content/answers.py). Raises ContentError when malformed."""
    if qtype == P.QTYPE:
        try:
            return P.normalize_answer(content or {"type": qtype}, raw)
        except P.PartError as ex:
            raise ContentError(str(ex)) from ex
    if not raw:
        return None
    kind = raw.get("kind")
    if kind == "choice":
        ls = sorted({str(x).strip().upper() for x in raw.get("labels") or [] if str(x).strip()})
        if not ls:
            raise ContentError("Chọn ít nhất một phương án đúng.")
        bad = [x for x in ls if x not in labels]
        if bad:
            raise ContentError(f"Đáp án {', '.join(bad)} không có trong các phương án.")
        if qtype == "single_choice" and len(ls) > 1:
            raise ContentError("Câu một lựa chọn chỉ có một đáp án đúng.")
        return {"kind": "choice", "labels": ls}
    if kind == "numeric":
        text = str(raw.get("text") or raw.get("value") or "").strip()
        v = A.parse_number(text)
        if v is None:
            raise ContentError("Đáp số không phải là số hợp lệ.")
        return {"kind": "numeric", "value": v, "text": text, "unit": (raw.get("unit") or None)}
    if kind == "text":
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ContentError("Đáp án dạng chữ đang trống.")
        acc = [str(x).strip() for x in raw.get("accepted") or [] if str(x).strip()]
        return {"kind": "text", "text": text, "accepted": acc or [text]}
    if kind == "tf_sequence":
        vals = raw.get("values") or []
        if not vals or any(v not in (True, False) for v in vals):
            raise ContentError("Mỗi ý phải được chọn Đúng hoặc Sai.")
        return {"kind": "tf_sequence", "values": [bool(v) for v in vals]}
    if kind == "boolean":
        if raw.get("value") not in (True, False):
            raise ContentError("Chọn Đúng hoặc Sai.")
        return {"kind": "boolean", "value": bool(raw["value"])}
    if kind in ("math_expression", "formula"):
        return normalize_math(raw)
    raise ContentError("Dạng đáp án không hỗ trợ.")


def normalize_math(raw: dict) -> dict:
    """A "Biểu thức / Công thức toán" key: canonical LaTeX, declared variables, tolerance, accepted alternatives;
    every form must be understood by the equivalence checker."""
    from ..content import mathexpr as M
    tex = M.canonical(str(raw.get("tex") or raw.get("text") or ""))
    if not tex:
        raise ContentError("Đáp án biểu thức đang trống.")
    names = raw.get("variables") or []
    if isinstance(names, str):
        names = re.split(r"[,;\s]+", names)
    names = [str(x).strip() for x in names if str(x).strip()]
    if any(not re.fullmatch(r"[A-Za-z](?:_\w+)?|\\?[A-Za-z]+", x) for x in names) or len(names) > 8:
        raise ContentError("Biến khai báo không hợp lệ (vd: a, b, x_1).")
    names = [x.lstrip("\\") for x in names]
    try:
        tol = float(raw.get("tolerance") if raw.get("tolerance") not in (None, "") else 1e-6)
    except (TypeError, ValueError):
        raise ContentError("Sai số không hợp lệ.") from None
    if not 0 < tol <= 0.1:
        raise ContentError("Sai số phải trong khoảng (0; 0,1].")
    acc = []
    for t in [tex] + [M.canonical(str(x)) for x in raw.get("accepted") or [] if str(x).strip()]:
        why = M.check(t, names or None)
        if why:
            raise ContentError(f"Không hiểu biểu thức “{t}”: {why}.")
        if t not in acc:
            acc.append(t)
    out = {"kind": "math_expression", "tex": tex, "accepted": acc}
    if names:
        out["variables"] = names
    if tol != 1e-6:
        out["tolerance"] = tol
    return out


def build_content(base: dict, edit: dict, known_src) -> dict:
    """New content document from the working one and the editor's fields (only given keys change)."""
    c = json.loads(json.dumps(base))
    if "type" in edit and edit["type"]:
        if edit["type"] not in QTYPES:
            raise ContentError("Dạng câu hỏi không hợp lệ.")
        c["type"] = edit["type"]
    for k in ("stem", "solution", "explanation"):
        if k in edit:
            blocks = sanitize_blocks(edit[k], known_src)
            c[k] = blocks if (blocks or k == "stem") else None
    if "options" in edit:
        opts, seen = [], set()
        for o in edit["options"] or []:
            lab = str(o.get("label") or "").strip().upper()
            if not re.fullmatch(r"[A-H]", lab) or lab in seen:
                raise ContentError("Nhãn phương án phải là A–H và không trùng nhau.")
            seen.add(lab)
            opts.append({"label": lab, "content": sanitize_blocks(o.get("content"), known_src)})
        c["options"] = opts
    if "group" in edit:
        g = edit["group"]
        if g is None:
            c["group"] = None
        else:
            c["group"] = {"key": str(g.get("key") or (base.get("group") or {}).get("key") or "")[:80],
                          "header": sanitize_blocks(g.get("header"), known_src),
                          "passage": sanitize_blocks(g.get("passage"), known_src)}
            if g.get("instruction_only") and not c["group"]["passage"]:
                c["group"]["instruction_only"] = True  # verified: the header is only an instruction, no shared data
    if "parts" in edit:
        c["parts"] = sanitize_parts(edit["parts"], known_src)
    if c.get("type") == P.QTYPE:
        c["options"] = []   # a multi-part question has no options of its own: each item has its own
        c.setdefault("parts", [])
    elif "parts" in c:
        c.pop("parts")       # another type: the items no longer apply
    if len(json.dumps(c, ensure_ascii=False)) > MAX_CONTENT_BYTES:
        raise ContentError("Nội dung quá lớn.")
    return c


def sanitize_parts(parts, known_src) -> list[dict]:
    """The items of a multi-part question as posted by the editor."""
    if parts is None:
        return []
    if not isinstance(parts, list) or len(parts) > P.MAX_PARTS:
        raise ContentError(f"Tối đa {P.MAX_PARTS} ý.")
    out, keys = [], set()
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise ContentError("Ý không hợp lệ.")
        key = str(p.get("key") or f"p{i + 1}")
        if not P.KEY_RE.match(key) or key in keys:
            raise ContentError("Khoá của ý không hợp lệ hoặc bị trùng.")
        keys.add(key)
        t = p.get("answer_type")
        if t not in P.ANSWER_TYPES:
            raise ContentError(f"Ý {i + 1}: dạng trả lời không hợp lệ.")
        label = _s(str(p.get("label") or chr(ord("a") + i)), 8).strip() or chr(ord("a") + i)
        try:
            pts = float(p.get("points") if p.get("points") not in (None, "") else 1.0)
        except (TypeError, ValueError):
            raise ContentError(f"Ý {label}: điểm không hợp lệ.") from None
        if not 0 < pts <= 100:
            raise ContentError(f"Ý {label}: điểm phải lớn hơn 0.")
        item = {"key": key, "label": label, "prompt": sanitize_blocks(p.get("prompt"), known_src),
                "answer_type": t, "points": pts}
        sol = sanitize_blocks(p.get("solution"), known_src)
        item["solution"] = sol or None
        if t == "choice":
            item["multi"] = bool(p.get("multi"))
            opts, seen = [], set()
            for o in p.get("options") or []:
                lab = str(o.get("label") or "").strip().upper()
                if not re.fullmatch(r"[A-H]", lab) or lab in seen:
                    raise ContentError(f"Ý {label}: nhãn phương án phải là A–H và không trùng nhau.")
                seen.add(lab)
                opts.append({"label": lab, "content": sanitize_blocks(o.get("content"), known_src)})
            item["options"] = opts
        out.append(item)
    return out


def finalize(content: dict, answer: dict | None) -> dict:
    """Recompute the derived keys of a content document (input spec, shuffle safety, asset list)."""
    labels = [o["label"] for o in content.get("options") or []]
    opts_md = [(o["label"], plain_text(o["content"])) for o in content.get("options") or []]
    stem_txt = "\n".join(plain_text([b]) for b in content.get("stem") or [])
    if P.is_multi(content):
        content["input"] = P.input_spec(content)
        content["shuffle_safe"] = False
    else:
        content["input"] = A.input_spec(content["type"], opts_md, answer, stem_txt)
        content["shuffle_safe"] = A.shuffle_safe(content["type"], {l: t for l, t in opts_md}) if labels else False
    content["assets"] = sorted({s.split("/")[1].split(".")[0] for s in images(content)})
    return content


def version_hash(content: dict, answer: dict | None) -> str:
    """Same definition as BuiltQuestion.content_hash (question_version.content_hash)."""
    blob = json.dumps({"c": content, "a": answer}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


CONTENT_FLAGS = {"empty_stem", "too_few_options", "empty_option", "render_warning", "answer_not_in_options",
                 "answer_empty", "answer_without_options", "group_context_missing", "no_input",
                 "inline_options_in_short_response", "answer_unparseable", "answer_kind_unknown",
                 "parts_incomplete", "letter_answer_without_options", "option_count_suspect"}


def derived_flags(content: dict, answer: dict | None) -> set:
    """Content checks of the builder (sync/hsa_upstream.build_question) evaluated on a stored version."""
    flags = set()
    stem = content.get("stem") or []
    options = content.get("options") or []
    inp = content.get("input") or {}
    if not plain_text(stem).strip() and not any(b.get("t") in ("img", "table") for b in stem) and \
            not (P.is_multi(content) and P.parts_of(content)):
        flags.add("empty_stem")
    if P.is_multi(content):
        if P.problems(content, answer):
            flags.add("parts_incomplete")
        if _has_warn(P.part_blocks(content)):
            flags.add("render_warning")
    if inp.get("kind") == "choice":
        if len(options) < 2:
            flags.add("too_few_options")
        if any(not o.get("content") for o in options):
            flags.add("empty_option")
    g = content.get("group")
    if g is not None and not g.get("passage") and not g.get("instruction_only"):
        flags.add("group_context_missing")
    if _has_warn(stem) or _has_warn(options) or (g and _has_warn(g)):
        flags.add("render_warning")
    if A.letter_key_without_options(options, answer):
        flags.add("letter_answer_without_options")
    # (option_count_suspect is not derived here: a version approved in the Review Studio was checked by a person;
    # publishing it clears the upstream flag — it is listed in CONTENT_FLAGS for that)
    if answer and answer.get("kind") == "choice":
        labels = {o["label"] for o in options}
        if not labels:
            flags.add("answer_without_options")
        elif any(l not in labels for l in answer["labels"]):
            flags.add("answer_not_in_options")
    return flags
