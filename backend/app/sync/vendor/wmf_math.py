# Vendored verbatim from HSA-question-bank scripts/hsa/wmf_math.py (upstream commit 8628c79).
# Keep in sync with upstream; do not edit here except to re-vendor.
"""Reconstruct text / mathematics from vector WMF pictures (MathType or Equation Editor
formulas that a source document stores as pictures, e.g. "14 km", "x > 0", systems, fractions).

Output: {"kind": "text", "text": "14 km"} for plain numbers/units/words, or
        {"kind": "latex", "latex": "..."} for mathematics,
        or None when the picture cannot be reconstructed with every glyph and every line
        accounted for (then the caller keeps NEEDS_FORMULA_REVIEW — nothing is guessed).

Method: exact geometry from the WMF records (ExtTextOut with per-glyph advances at the
current position, font heights/italics, LineTo fraction bars, polyline radicals, Symbol-font
brace/paren pieces) -> recursive layout analysis (systems -> rows -> fractions/radicals ->
baseline scripts) -> LaTeX. Deterministic; no OCR.
"""
import collections
import re
import struct

SYMBOL = {  # Symbol font (8-bit) -> (unicode, latex)
    0x2D: ("−", "-"), 0x2B: ("+", "+"), 0x3D: ("=", "="), 0x3C: ("<", "<"), 0x3E: (">", ">"), 0x28: ("(", "("),
    0x29: (")", ")"), 0x5B: ("[", "["), 0x5D: ("]", "]"), 0x7B: ("{", r"\{"), 0x7D: ("}", r"\}"), 0x7C: ("|", "|"),
    0x2C: (",", ","), 0x2E: (".", "."), 0x3B: (";", ";"), 0x3A: (":", ":"), 0x2F: ("/", "/"), 0x21: ("!", "!"),
    0xA2: ("′", "'"), 0xB2: ("″", "''"), 0xA3: ("≤", r"\le "), 0xB3: ("≥", r"\ge "), 0xB9: ("≠", r"\ne "),
    0xB1: ("±", r"\pm "), 0xB4: ("×", r"\times "), 0xB8: ("÷", r"\div "), 0xB0: ("°", r"^{\circ}"),
    0xA5: ("∞", r"\infty "), 0xCE: ("∈", r"\in "), 0xCF: ("∉", r"\notin "), 0xC7: ("∩", r"\cap "),
    0xC8: ("∪", r"\cup "), 0xC6: ("∅", r"\varnothing "), 0xD0: ("∠", r"\angle "), 0xB6: ("∂", r"\partial "),
    0xBB: ("≈", r"\approx "), 0xBA: ("≡", r"\equiv "), 0xAE: ("→", r"\to "), 0xDE: ("⇒", r"\Rightarrow "),
    0xDB: ("⇔", r"\Leftrightarrow "), 0xDC: ("⇐", r"\Leftarrow "), 0xAC: ("←", r"\leftarrow "),
    0xAB: ("↔", r"\leftrightarrow "), 0xAD: ("↑", r"\uparrow "), 0xAF: ("↓", r"\downarrow "),
    0xD7: ("⋅", r"\cdot "), 0xBC: ("…", r"\ldots "), 0xCC: ("⊂", r"\subset "), 0xC9: ("⊃", r"\supset "),
    0xCD: ("⊆", r"\subseteq "), 0xCA: ("⊇", r"\supseteq "), 0x5E: ("⊥", r"\perp "), 0x22: ("∀", r"\forall "),
    0x24: ("∃", r"\exists "), 0xE5: ("∑", r"\sum "), 0xD5: ("∏", r"\prod "), 0xF2: ("∫", r"\int "),
    0xD6: ("√", r"\surd "), 0xBD: ("|", "|"), 0xE1: ("⟨", r"\langle "), 0xF1: ("⟩", r"\rangle "),
    0xB5: ("∝", r"\propto "), 0x7E: ("∼", r"\sim "), 0x40: ("≅", r"\cong "), 0xBE: ("—", "-"),
}
GREEK = dict(zip("abgdezhqiklmnxoprstufcyw", ["alpha", "beta", "gamma", "delta", "varepsilon", "zeta", "eta",
                                              "theta", "iota", "kappa", "lambda", "mu", "nu", "xi", "o", "pi", "rho",
                                              "sigma", "tau", "upsilon", "varphi", "chi", "psi", "omega"]))
GREEK.update({"j": "varphi", "v": "varpi", "J": "vartheta", "G": "Gamma", "D": "Delta", "Q": "Theta", "L": "Lambda",
              "X": "Xi", "P": "Pi", "S": "Sigma", "U": "Upsilon", "F": "Phi", "Y": "Psi", "W": "Omega"})
# MT Extra glyphs verified against MathType MTEF sources (preview glyph <-> structured character)
MTEXTRA = {0xA1: ("ℝ", r"\mathbb{R}")}
BRACE_PIECES = {0xEC: "top", 0xED: "mid", 0xEE: "bot", 0xEF: "ext"}
RBRACE_PIECES = {0xFC, 0xFD, 0xFE}
LPAREN_PIECES = {0xE6, 0xE7, 0xE8}
RPAREN_PIECES = {0xF6, 0xF7, 0xF8}
LBRACK_PIECES = {0xE9, 0xEA, 0xEB}
RBRACK_PIECES = {0xF9, 0xFA, 0xFB}
INTEGRAL_PIECES = {0xF3, 0xF4, 0xF5}
FUNCS = {"sin", "cos", "tan", "cot", "log", "ln", "lg", "exp", "lim", "max", "min", "sup", "inf", "det"}
CP = {0: "cp1252", 1: "cp1252", 161: "cp1253", 163: "cp1258", 238: "cp1250", 204: "cp1251"}


def _wf(c, face):
    """Approximate advance width / em for Times New Roman and Symbol glyphs."""
    ch = chr(c)
    if face.lower().startswith("symbol"):
        return 0.55 if c in (0x2B, 0x3D, 0x2D, 0xA3, 0xB3, 0xB9, 0x3C, 0x3E) else (0.95 if c in (0xDB, 0xDE, 0xAE) else 0.5)
    if ch in "il.,;:'!|jftI()[]":
        return 0.3
    if ch in "mwMW":
        return 0.75
    return 0.5


class G:
    __slots__ = ("ch", "code", "x0", "x1", "y", "h", "italic", "font", "used")

    def __init__(self, ch, code, x0, x1, y, h, italic, font):
        self.ch, self.code, self.x0, self.x1, self.y, self.h, self.italic, self.font = ch, code, x0, x1, y, h, italic, font
        self.used = False

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2


def parse(data):
    """-> (glyphs, hbars, radicals, unknown_primitives)"""
    off = 22 if data[:4] == b"\xd7\xcd\xc6\x9a" else 0
    if len(data) < off + 18:
        return None
    i = off + struct.unpack_from("<H", data, off + 2)[0] * 2
    objs, free, nxt = {}, [], 0
    font = ("", 0, 0, 0)
    cur = [0, 0]
    align = 0
    glyphs, segs, polys = [], [], []
    unknown = collections.Counter()
    while i + 6 <= len(data):
        size, func = struct.unpack_from("<IH", data, i)
        if size < 3:
            break
        b = data[i + 6:i + size * 2]
        i += size * 2
        if func in (0x02FB, 0x02FC, 0x00F7, 0x02FA, 0x06FF, 0x0142, 0x01F9):
            slot = min(free) if free else nxt
            if free:
                free.remove(slot)
            else:
                nxt += 1
            objs[slot] = (b[18:50].split(b"\0")[0].decode("latin-1"), abs(struct.unpack_from("<h", b)[0]), b[10], b[13]) \
                if func == 0x02FB else None
        elif func == 0x012D:
            o = objs.get(struct.unpack_from("<H", b)[0])
            if o:
                font = o
        elif func == 0x01F0:
            s = struct.unpack_from("<H", b)[0]
            objs.pop(s, None)
            free.append(s)
        elif func == 0x012E:
            align = struct.unpack_from("<H", b)[0]
        elif func == 0x0214:
            cur = [struct.unpack_from("<h", b, 2)[0], struct.unpack_from("<h", b, 0)[0]]
        elif func == 0x0213:
            p = [struct.unpack_from("<h", b, 2)[0], struct.unpack_from("<h", b, 0)[0]]
            segs.append((cur[0], cur[1], p[0], p[1]))
            cur = p
        elif func in (0x0325, 0x0324):
            n = struct.unpack_from("<h", b)[0]
            polys.append(([struct.unpack_from("<hh", b, 2 + 4 * k) for k in range(n)], func == 0x0324))
        elif func == 0x0A32:
            y, x, n, opt = struct.unpack_from("<hhhH", b)
            p = 8 + (8 if opt & 0x0006 else 0)
            raw = b[p:p + n]
            q = p + n + (n & 1)
            dx = [struct.unpack_from("<h", b, q + 2 * k)[0] for k in range(n)] if len(b) >= q + 2 * n else [0] * n
            face, h, ital, cs = font
            if align & 1:   # TA_UPDATECP: draw at the current position
                x, y = cur
            xx = x
            for k, c in enumerate(raw):
                est = int(h * _wf(c, face))
                w = min(dx[k], est) if dx[k] > 0 else est
                adv = dx[k] if dx[k] else est
                sym = face.lower().startswith("symbol")
                if face.lower().startswith("mt extra") and c in MTEXTRA:
                    ch = MTEXTRA[c][0]
                    glyphs.append(G(ch, 0x10000 + c, xx, xx + int(h * 0.6), y, h, False, "mt extra"))
                    xx += dx[k] if dx[k] else int(h * 0.6)
                    continue
                if face.lower().startswith(("mt extra", "wingdings")):
                    ch = f"\\u{c:02x}"
                elif sym:
                    ch = chr(0xF000 + c)
                else:
                    ch = bytes([c]).decode(CP.get(cs, "cp1252"), "replace")
                glyphs.append(G(ch, c if sym else None, xx, xx + w, y, h, bool(ital), face.lower()))
                xx += adv
            if align & 1:
                cur = [xx, y]
        elif func in (0x0F43, 0x0B41, 0x0940, 0x0922):
            unknown["bitmap"] += 1
    hbars = [(min(a, c), max(a, c), b) for a, b, c, d in segs if abs(b - d) <= 2 and abs(c - a) > 20]
    other_segs = [s for s in segs if not (abs(s[1] - s[3]) <= 2 and abs(s[2] - s[0]) > 20)]
    rads = []
    for pts, filled in polys:
        top = min(p[1] for p in pts)
        tops = [p for p in pts if abs(p[1] - top) <= 3]
        if len(tops) >= 2 and max(p[0] for p in tops) - min(p[0] for p in tops) > 40:
            rads.append((min(p[0] for p in pts), max(p[0] for p in tops), top, max(p[1] for p in pts)))
    # radical outlines are drawn twice (polyline + filled polygon): dedupe by geometry
    uniq = []
    for r in sorted(rads):
        if not any(abs(r[0] - u[0]) < 15 and abs(r[1] - u[1]) < 15 for u in uniq):
            uniq.append(r)
    # line segments that are part of a radical outline are consumed by that radical
    rest = []
    for s in other_segs:
        if not any(u[0] - 10 <= min(s[0], s[2]) and max(s[0], s[2]) <= u[1] + 10 and
                   u[2] - 10 <= min(s[1], s[3]) and max(s[1], s[3]) <= u[3] + 10 for u in uniq):
            rest.append(s)
    hb2 = []
    for hb in hbars:  # radical overbars drawn as line segments
        if any(abs(hb[2] - u[2]) <= 12 and hb[1] >= u[1] - 15 and hb[0] <= u[1] for u in uniq):
            continue
        hb2.append(hb)
    main_h = collections.Counter(g.h for g in glyphs).most_common(1)[0][0] if glyphs else 300
    left = []
    for s_ in rest:
        x0_, y0_, x1_, y1_ = s_
        if abs(x1_ - x0_) <= 3 and abs(y1_ - y0_) >= 0.6 * main_h:
            glyphs.append(G("|", 0x7C, min(x0_, x1_) - 20, max(x0_, x1_) + 20, max(y0_, y1_) - 0.2 * main_h,
                            main_h, False, "symbol"))
        else:
            left.append(s_)
    if left:
        unknown["non_horizontal_lines"] += len(left)
    return glyphs, hb2, uniq, unknown


# ---------------------------------------------------------------------------------------------
def _gl(g):
    """LaTeX for one glyph (letters handled by word grouping)."""
    if g.code is not None and g.code >= 0x10000:
        return MTEXTRA[g.code - 0x10000][1]
    if g.code is not None:
        c = g.code
        if chr(c) in GREEK and (0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A):
            return "\\" + GREEK[chr(c)] + " "
        if c in SYMBOL:
            return SYMBOL[c][1]
        return None
    if g.ch.startswith("\\u"):
        return None
    ch = g.ch
    return {"%": r"\%", "{": r"\{", "}": r"\}", "#": r"\#", "&": r"\&", "_": r"\_", "~": r"\sim ", "°": r"^{\circ}",
            "–": "-", "−": "-", "·": r"\cdot ", "×": r"\times "}.get(ch, ch)


class Fail(Exception):
    pass


def collect_items(glyphs, bars, rads, H):
    """Claim radicals and fraction bars, return row items (x0, baseline, h, obj, kind)."""
    items, used = [], set()
    bars = list(bars)
    rads = list(rads)
    for b in sorted(bars, key=lambda b: -(b[1] - b[0])):
        if b not in bars:
            continue
        span = [g for g in glyphs if id(g) not in used and b[0] - 8 <= g.xc <= b[1] + 8]
        num = [g for g in span if b[2] - 1.7 * H < g.y <= b[2] + 2]
        den = [g for g in span if b[2] < g.y - 0.55 * g.h and g.y < b[2] + 2.0 * H]
        if not num or not den:
            raise Fail("fraction bar without numerator/denominator")
        inner = [x for x in bars if x != b and b[0] - 5 <= x[0] and x[1] <= b[1] + 5 and abs(x[2] - b[2]) < 2.2 * H]
        nb = [x for x in inner if x[2] < b[2]]
        db = [x for x in inner if x[2] > b[2]]
        # glyphs of inner fractions belong to numerator / denominator too
        num = [g for g in span if b[2] - (1.7 + 1.2 * len(nb)) * H < g.y <= b[2] + 2]
        den = [g for g in span if b[2] < g.y - 0.55 * g.h and g.y < b[2] + (2.0 + 1.2 * len(db)) * H]
        for g in num + den:
            used.add(id(g))
        bars = [x for x in bars if x not in inner and x != b]
        nr = [r for r in rads if b[0] - 8 <= r[0] and r[1] <= b[1] + 8 and r[3] <= b[2] + 5]
        dr = [r for r in rads if b[0] - 8 <= r[0] and r[1] <= b[1] + 8 and r[2] >= b[2] - 5]
        rads = [r for r in rads if r not in nr and r not in dr]
        tex = r"\frac{%s}{%s}" % (layout(num, nb, nr, H), layout(den, db, dr, H))
        items.append((b[0], b[2] + 0.3 * H, H, tex, "box"))
    for r in sorted(rads, key=lambda r: -(r[1] - r[0])):
        inside = [g for g in glyphs if id(g) not in used and r[0] < g.x0 and g.xc <= r[1] + 5
                  and r[2] - 5 <= g.y - 0.35 * g.h <= r[3]]
        idx = [g for g in glyphs if id(g) not in used and g.x1 <= r[0] + (r[1] - r[0]) * 0.2 + 30 and g.h < 0.85 * H
               and r[2] - 0.5 * H < g.y < r[2] + 0.8 * H and g not in inside and g.x0 >= r[0] - 0.6 * H]
        for g in inside + idx:
            used.add(id(g))
        ib = [b for b in bars if r[0] <= b[0] and b[1] <= r[1] + 5]
        bars = [b for b in bars if b not in ib]
        body = layout(inside, ib, [], H)
        tex = (r"\sqrt[%s]{%s}" % (layout(idx, [], [], H), body)) if idx else r"\sqrt{%s}" % body
        items.append((r[0], r[3] - 0.22 * H, H, tex, "box"))
    for g in glyphs:
        if id(g) in used:
            continue
        if g.code in LPAREN_PIECES | RPAREN_PIECES | LBRACK_PIECES | RBRACK_PIECES:
            continue
        items.append((g.x0, g.y, g.h, g, "glyph"))
    for pieces, tex in ((LPAREN_PIECES, r"\left("), (RPAREN_PIECES, r"\right)"), (LBRACK_PIECES, r"\left["),
                        (RBRACK_PIECES, r"\right]")):
        cols = collections.defaultdict(list)
        for g in glyphs:
            if g.code in pieces and id(g) not in used:
                cols[round(g.x0 / 25)].append(g)
        for gs in cols.values():
            ys = [g.y for g in gs]
            items.append((min(g.x0 for g in gs), (min(ys) + max(ys)) / 2 + 0.3 * H, H, tex + " ", "delim"))
    return items


def assemble(items, H):
    if not items:
        return ""
    items = sorted(items, key=lambda it: it[0])
    full = [it for it in items if it[4] != "glyph" or it[2] >= 0.85 * H]
    base = collections.Counter(round(it[1] / 10) * 10 for it in full).most_common(1)[0][0] if full else items[0][1]
    out, k, delim_open = [], 0, 0
    while k < len(items):
        x, y, h, obj, kind = items[k]
        if kind == "glyph" and h < 0.85 * H and out:
            up = y < base - 0.12 * H
            grp = [obj]
            while k + 1 < len(items) and items[k + 1][4] == "glyph" and items[k + 1][2] < 0.85 * H and \
                    (items[k + 1][1] < base - 0.12 * H) == up:
                k += 1
                grp.append(items[k][3])
            out.append(("^{%s}" if up else "_{%s}") % tokens(grp))
            k += 1
            continue
        if kind == "glyph":
            run = [obj]
            while k + 1 < len(items) and items[k + 1][4] == "glyph" and items[k + 1][2] >= 0.85 * H:
                k += 1
                run.append(items[k][3])
            out.append(tokens(run))
        elif kind == "delim":
            if obj.startswith(r"\left"):
                delim_open += 1
                out.append(obj)
            elif delim_open:
                delim_open -= 1
                out.append(obj)
            else:
                out.append(obj.replace(r"\right", ""))
        else:
            out.append(obj)
        k += 1
    return "".join(out) + r"\right." * delim_open


def rows_of(items, H):
    """Cluster items into rows by baseline; returns (rows, outliers)."""
    full = sorted((it for it in items if it[4] != "glyph" or it[2] >= 0.85 * H), key=lambda it: it[1])
    if not full:
        return [items], []
    centers, groups = [], []
    for it in full:
        if centers and it[1] - centers[-1] <= 0.6 * H:
            groups[-1].append(it[1])
            centers[-1] = sum(groups[-1]) / len(groups[-1])
        else:
            centers.append(it[1])
            groups.append([it[1]])
    # singleton clusters lying between rows (e.g. ⇔ on the axis) are outliers, not rows
    real = [c for c, g in zip(centers, groups) if len(g) > 1] or centers
    rows = [[] for _ in real]
    out = []
    for it in items:
        yy = it[1] if (it[4] != "glyph" or it[2] >= 0.85 * H) else it[1] + 0.3 * H
        d = [abs(c - yy) for c in real]
        i = min(range(len(real)), key=lambda j: d[j])
        if d[i] > 0.45 * H and len(real) > 1:
            out.append(it)
        else:
            rows[i].append(it)
    return rows, out


def _glyph_rows(glyphs, bars, rads, H):
    """Rows of a system: cluster baselines of full-size glyphs outside fractions (fraction axes
    count as one baseline); glyphs lying between rows (e.g. ⇔ on the axis) become trailing items."""
    in_frac = set()
    for b in bars:
        for g in glyphs:
            if b[0] - 8 <= g.xc <= b[1] + 8 and abs(g.y - b[2]) < 1.7 * H:
                in_frac.add(id(g))
    marks = sorted([g.y for g in glyphs if g.h >= 0.85 * H and id(g) not in in_frac and g.code not in
                    BRACE_PIECES and g.code not in LBRACK_PIECES] + [b[2] + 0.3 * H for b in bars])
    groups = []
    for y in marks:
        if groups and y - groups[-1][-1] <= 0.6 * H:
            groups[-1].append(y)
        else:
            groups.append([y])
    centers = [sum(g) / len(g) for g in groups if len(g) > 1] or [sum(g) / len(g) for g in groups]
    if not centers:
        return [(glyphs, bars, rads, True)]
    # a nested opener column (e.g. a brace inside a bracket system) makes the rows it spans one row
    nested = [g for g in glyphs if g.code in BRACE_PIECES]
    if nested:
        cols = []
        for g in sorted(nested, key=lambda g: g.x0):
            if cols and abs(g.x0 - cols[-1][0].x0) < 60:
                cols[-1].append(g)
            else:
                cols.append([g])
        for c in cols:
            lo, hi = min(g.y for g in c) - H, max(g.y for g in c) + 0.2 * H
            inside = [x for x in centers if lo <= x <= hi]
            if len(inside) > 1:
                merged = sum(inside) / len(inside)
                centers = sorted([x for x in centers if x not in inside] + [merged])
    rows = [([], [], [], True) for _ in centers]
    trail = ([], [], [], False)

    def pick(y, small=False):
        d = [abs(c - y) for c in centers]
        i = min(range(len(centers)), key=lambda j: d[j])
        return rows[i] if d[i] <= (1.8 * H if small else 1.3 * H) or len(centers) == 1 else trail
    for g in glyphs:
        if id(g) in in_frac:
            b = min(bars, key=lambda b: abs(g.y - b[2]) if b[0] - 8 <= g.xc <= b[1] + 8 else 1e9)
            pick(b[2] + 0.3 * H)[0].append(g)
        else:
            pick(g.y if g.h >= 0.85 * H else g.y + 0.3 * H, small=g.h < 0.85 * H)[0].append(g)
    for b in bars:
        pick(b[2] + 0.3 * H)[1].append(b)
    for r in rads:
        pick(r[3] - 0.22 * H)[2].append(r)
    return [r for r in rows if r[0] or r[1] or r[2]] + ([trail] if trail[0] else [])


def layout(glyphs, bars, rads, H):
    if not glyphs and not bars and not rads:
        return ""
    opener = BRACE_PIECES.keys() | (LBRACK_PIECES if not any(g.code in RBRACK_PIECES for g in glyphs) else set())
    brace = [g for g in glyphs if g.code in opener]
    if not brace:
        return assemble(collect_items(glyphs, bars, rads, H), H)
    cols = []
    for g in sorted(brace, key=lambda g: g.x0):
        if cols and g.x0 - cols[-1][-1].x0 < 60 and (g.code in BRACE_PIECES) == (cols[-1][0].code in BRACE_PIECES):
            cols[-1].append(g)
        else:
            cols.append([g])

    def span(c):
        return min(g.y for g in c) - H, max(g.y for g in c)
    top = []
    for c in cols:
        a0, a1 = span(c)
        nested = any(t is not c and t[0].code in LBRACK_PIECES and c[0].code in BRACE_PIECES
                     and min(g.x0 for g in t) < min(g.x0 for g in c) and span(t)[0] <= a0 + 5
                     and a1 <= span(t)[1] + 5 and (span(t)[1] - span(t)[0]) > 1.25 * (a1 - a0) for t in cols)
        if not nested:
            top.append(c)
    cols = top
    brace = [g for c in cols for g in c]
    rest = [g for g in glyphs if g not in brace]
    edges = [min(g.x0 for g in c) for c in cols] + [10 ** 9]
    pre = [g for g in rest if g.xc < edges[0]]
    tex = assemble(collect_items(pre, [b for b in bars if b[1] <= edges[0] + 5],
                                 [r for r in rads if r[1] <= edges[0] + 5], H), H)
    for i, c in enumerate(cols):
        lo, hi = max(g.x1 for g in c) - 5, edges[i + 1]
        seg = [g for g in rest if lo <= g.xc < hi]
        sb = [b for b in bars if lo <= b[0] and b[0] < hi]
        sr = [r for r in rads if lo <= r[0] and r[0] < hi]
        # rows are found from baselines, then every row is laid out again (nested systems allowed)
        rows = _glyph_rows(seg, sb, sr, H)
        delim = r"\{" if c[0].code in BRACE_PIECES else "["
        parts, trailing = [], []
        for rg, rb, rr, is_row in rows:
            (parts if is_row else trailing).append(layout(rg, rb, rr, H))
        tex += r"\left%s \begin{array}{l}" % delim + r" \\ ".join(parts) + r"\end{array} \right."
        tex += "".join(trailing)
    return tex


def tokens(glyphs):
    """Consecutive glyphs -> LaTeX, grouping upright letter words (functions / text units)."""
    out, word = [], []

    def flush():
        if word:
            w = "".join(g.ch for g in word)
            if w in FUNCS:
                out.append("\\" + w + " ")
            elif word[0].italic or len(w) == 1:
                out.append(w)
            else:
                out.append(r"\text{%s}" % w)
            word.clear()
    prev = None
    for g in glyphs:
        if prev is not None and g.x0 - prev.x1 > 0.2 * g.h and not out[-1:] == ["\\ "] \
                and prev.code is None and g.code is None and prev.ch.isalnum() and g.ch.isalnum():
            flush()
            out.append("\\ ")
        prev = g
        if g.ch == "\ufffd" and g.code is None:
            raise Fail("undecodable glyph")
        if g.code is None and not g.ch.startswith("\\u") and g.ch.isalpha():
            if word and word[-1].italic != g.italic:
                flush()
            word.append(g)
            continue
        flush()
        t = _gl(g)
        if t is None:
            raise Fail(f"unmapped glyph {g.font}:{g.ch!r}")
        if t.strip():
            out.append(t)
    flush()
    return "".join(out)


def cosmetic(latex):
    """Typographic normalisation of reconstructed LaTeX (no change of mathematical content)."""
    latex = re.sub(r"\\(sin|cos|tan|cot|log|ln|lg|lim|exp|max|min) \\ ", r"\\\1 ", latex)
    # multi-letter units are set upright (MathType's default italic is only a style choice)
    latex = re.sub(r"(?<![A-Za-z\\{])(km/h|m/s|km|cm|mm|dm|kg|mol|ml)(?![A-Za-z}])", r"\\text{\1}", latex)
    return latex


PLAIN = re.compile(r"^[0-9A-Za-zÀ-ỹ\s,./%°:;()\-−+]+$")


def reconstruct(data):
    parsed = parse(data)
    if not parsed:
        return None, "unparseable WMF"
    glyphs, bars, rads, unknown = parsed
    if unknown:
        return None, f"unsupported primitives: {dict(unknown)}"
    glyphs = [g for g in glyphs if g.font != "system" and g.ch.strip()]
    if not glyphs:
        return None, "no glyphs"
    H = collections.Counter(g.h for g in glyphs).most_common(1)[0][0]
    try:
        latex = layout(glyphs, bars, rads, H)
    except Fail as ex:
        return None, str(ex)
    except Exception as ex:  # noqa: BLE001
        return None, f"{type(ex).__name__}: {ex}"
    latex = re.sub(r"\s+", " ", latex).strip()
    latex = cosmetic(latex)
    if not latex:
        return None, "empty"
    # plain text when there is no mathematical structure: numbers, units, words
    simple = not bars and not rads and not any(g.code in BRACE_PIECES for g in glyphs) and \
        all(g.h >= 0.85 * H for g in glyphs) and all(g.code is None or g.code in (0x2D, 0x2C, 0x2E, 0x28, 0x29) for g in glyphs)
    if simple:
        xs = sorted(glyphs, key=lambda g: g.x0)
        text, last = "", None
        for g in xs:
            ch = "−" if g.code == 0x2D else g.ch
            if last is not None and (g.x0 - last.x1 > 0.18 * H or (last.ch.isdigit() and g.ch.isalpha())):
                text += " "   # "14 km", "56 km/h": number followed by a unit
            text += ch
            last = g
        if PLAIN.match(text):
            return {"kind": "text", "text": text.strip()}, "ok"
    return {"kind": "latex", "latex": latex}, "ok"
