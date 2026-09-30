"""hsa-md → render-ready block model (safe JSON; no raw HTML reaches clients).

Block  = {"t":"p","c":[Inline]} | {"t":"img","src","w","h"} | {"t":"table","rows":[[Cell]]} | {"t":"warn","v"}
Cell   = {"c":[Block], "colspan"?, "rowspan"?}
Inline = {"t":"s","v":text,"m":[marks]} | {"t":"m","tex"} | {"t":"br"} | {"t":"tab"} | {"t":"range"} | {"t":"warn","v"}
marks  ⊂ b (bold) i (italic) u (underline) x (strike) sup sub

Formula/image/table references are resolved through a `Resolver` so the same parser serves the
HSA upstream importer and generic JSONL banks. Every problem found while rendering is appended to
`Resolver.issues` as (kind, detail) with kind ∈ formula | visual | missing_visual | content,
mirroring the upstream editorial renderer so the derived editorial states agree with it.
"""
import re
from dataclasses import dataclass, field

TOKEN = re.compile(r"(\{\{redrawn\}\}|\{\{range\}\}|\*\*|(?<!\\)\*|</?u>|</?sup>|</?sub>|</?s>|"
                   r"\{\{f:fm_[0-9a-z_]+\}\}|\{\{img:as_[0-9a-f]+\}\}|\{\{tbl:\d+\}\}|\x00TEX\d+\x00|"
                   r"\{\{unsupported:[^}]*\}\}|\{\{image:[^}]+\}\}|\t|\n)")
MARK_OF = {"**": "b", "*": "i", "u": "u", "s": "x", "sup": "sup", "sub": "sub"}


@dataclass
class Resolver:
    """Callbacks used while rendering. Each returns render nodes; problems go to `issues`."""
    formula: callable = None        # fid -> list[Inline] (may append issues)
    image: callable = None          # aid -> (list[Inline], list[Block])  (inline nodes, blocks after paragraph)
    redrawn: callable = None        # () -> list[Block]
    image_path: callable = None     # relative path -> (list[Inline], list[Block])  (generic JSONL banks)
    issues: list = field(default_factory=list)
    assets: set = field(default_factory=set)  # sha256 of media files referenced

    def issue(self, kind, detail):
        self.issues.append((kind, detail))


def _extract_tex(txt: str) -> tuple[str, list[str]]:
    """Replace every {{tex:LATEX}} by a placeholder, balancing braces inside LATEX (so that e.g.
    {{tex:\\frac{1}{2}}} keeps its closing braces)."""
    out, texs, i = [], [], 0
    while True:
        j = txt.find("{{tex:", i)
        if j < 0:
            out.append(txt[i:])
            break
        out.append(txt[i:j])
        k, depth = j + 6, 0
        while k < len(txt):
            ch = txt[k]
            if ch == "\\":
                k += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0 and txt.startswith("}}", k):
                    break
                depth -= 1
            k += 1
        if k >= len(txt):  # unterminated: keep literally
            out.append(txt[j:])
            break
        out.append(f"\x00TEX{len(texs)}\x00")
        texs.append(txt[j + 6:k])
        i = k + 2
    return "".join(out), texs


def _unescape(t: str) -> str:
    return t.replace("\\*", "*").replace("\\\\", "\\").replace("{\\{", "{{").replace(" ", " ")


def inlines(txt: str, res: Resolver, tables=None, after: list | None = None) -> list:
    """Parse one paragraph of hsa-md into inline nodes; block content produced inside the
    paragraph (images, tables) is appended to `after`."""
    after = after if after is not None else []
    out: list = []
    marks: list[str] = []

    def emit_text(t):
        if not t:
            return
        node = {"t": "s", "v": t}
        if marks:
            node["m"] = sorted(set(marks))
        if out and out[-1].get("t") == "s" and out[-1].get("m") == node.get("m"):
            out[-1]["v"] += t
        else:
            out.append(node)

    txt, texs = _extract_tex(txt or "")
    for tok in TOKEN.split(txt):
        if not tok:
            continue
        if tok.startswith("\x00TEX"):
            out.append({"t": "m", "tex": texs[int(tok[4:-1])]})
        elif tok in ("**", "*"):
            m = MARK_OF[tok]
            if m in marks:
                marks.remove(m)
            else:
                marks.append(m)
        elif tok in ("<u>", "<sup>", "<sub>", "<s>"):
            marks.append(MARK_OF[tok[1:-1]])
        elif tok in ("</u>", "</sup>", "</sub>", "</s>"):
            m = MARK_OF[tok[2:-1]]
            if m in marks:
                marks.remove(m)
        elif tok == "{{redrawn}}":
            if res.redrawn:
                after.extend(res.redrawn())
        elif tok == "{{range}}":
            out.append({"t": "range"})
        elif tok.startswith("{{f:"):
            if res.formula:
                out.extend(res.formula(tok[4:-2]))
            else:
                res.issue("formula", f"formula {tok[4:-2]} unresolved")
                out.append({"t": "warn", "v": "công thức"})
        elif tok.startswith("{{img:"):
            if res.image:
                inl, blocks = res.image(tok[6:-2])
                out.extend(inl)
                after.extend(blocks)
            else:
                res.issue("missing_visual", f"image {tok[6:-2]} missing")
                out.append({"t": "warn", "v": "thiếu hình"})
        elif tok.startswith("{{image:"):
            if res.image_path:
                inl, blocks = res.image_path(tok[8:-2].strip())
                out.extend(inl)
                after.extend(blocks)
            else:
                res.issue("missing_visual", f"image {tok[8:-2]} missing")
                out.append({"t": "warn", "v": "thiếu hình"})
        elif tok.startswith("{{tbl:"):
            i = int(tok[6:-2])
            after.append(table_block(tables[i] if tables and i < len(tables) else None, res, tables))
        elif tok.startswith("{{unsupported:"):
            res.issue("visual", f"unsupported object {tok[14:-2]}")
            out.append({"t": "warn", "v": "đối tượng không hiển thị được"})
        elif tok == "\t":
            out.append({"t": "tab"})
        elif tok == "\n":
            out.append({"t": "br"})
        else:
            emit_text(_unescape(tok))
    return out


def _is_blank(inl: list) -> bool:
    for x in inl:
        if x["t"] in ("br", "tab"):
            continue
        if x["t"] == "s" and not x["v"].strip():
            continue
        return False
    return True


def _trim(inl: list) -> list:
    while inl and inl[0]["t"] in ("br", "tab"):
        inl = inl[1:]
    while inl and inl[-1]["t"] in ("br", "tab"):
        inl = inl[:-1]
    return inl


def blocks(md: str | None, res: Resolver, tables=None) -> list:
    out = []
    for ptxt in re.split(r"\n\s*\n", md or ""):
        if not ptxt.strip():
            continue
        m = re.fullmatch(r"\s*\{\{tbl:(\d+)\}\}\s*", ptxt)
        if m:
            i = int(m.group(1))
            out.append(table_block(tables[i] if tables and i < len(tables) else None, res, tables))
            continue
        after: list = []
        inl = _trim(inlines(ptxt, res, tables, after))
        if not _is_blank(inl):
            p = {"t": "p", "c": inl}
            if len(inl) == 1 and inl[0]["t"] == "m":
                p["display"] = True
            out.append(p)
        out.extend(after)
    return out


def table_block(t, res: Resolver, tables=None) -> dict:
    if not t or not t.get("rows"):
        res.issue("missing_visual", "table missing")
        return {"t": "warn", "v": "thiếu bảng"}
    rows_out = []
    open_cells: dict[int, dict] = {}  # grid column -> cell being vertically merged
    for r in t["rows"]:
        row_out, col = [], 0
        for c in r:
            span = int(c.get("colspan") or 1)
            vm = c.get("vmerge")
            if vm == "continue" and col in open_cells:
                open_cells[col]["rowspan"] = open_cells[col].get("rowspan", 1) + 1
            else:
                cell = {"c": blocks(c.get("md") or "", res, tables)}
                if span > 1:
                    cell["colspan"] = span
                row_out.append(cell)
                if vm == "restart":
                    open_cells[col] = cell
                else:
                    open_cells.pop(col, None)
            col += span
        rows_out.append(row_out)
    return {"t": "table", "rows": rows_out}


# ------------------------------------------------------------------------------------------------
# plain text (search, previews, heuristics)
# ------------------------------------------------------------------------------------------------
def plain_text(nodes) -> str:
    parts = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
            return
        t = n.get("t")
        if t == "s":
            parts.append(n["v"])
        elif t == "m":
            parts.append(" " + n["tex"] + " ")
        elif t in ("br", "tab"):
            parts.append(" ")
        elif t == "p":
            walk(n["c"])
            parts.append("\n")
        elif t == "table":
            for row in n["rows"]:
                for cell in row:
                    walk(cell["c"])
                    parts.append(" | ")
                parts.append("\n")
        elif t == "range":
            parts.append("…")

    walk(nodes)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


RANGE_RE = [
    # "trả lời các câu hỏi từ câu 66 - 70", "từ 81 đến 82", "câu 36 đến câu 40"
    re.compile(r"(?:từ\s+)?(?:câu\s+)?(?:số\s+)?\d{1,3}\s*(?:đến|tới|-|–|—)\s*(?:câu\s+)?\d{1,3}", re.I),
    # "questions 36 to 40", "from question 36 to 40"
    re.compile(r"(?:from\s+)?questions?\s+\d{1,3}\s*(?:to|-|–|—|and)\s*\d{1,3}", re.I),
]


def neutralize_group_header(md: str | None) -> str | None:
    """Replace source question-number ranges in a group header by a {{range}} token that clients
    fill with the positions of the group inside the current exam."""
    if not md:
        return md
    for rx in RANGE_RE:
        md = rx.sub("{{range}}", md)
    return md
