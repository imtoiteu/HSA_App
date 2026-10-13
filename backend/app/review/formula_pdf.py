"""Formula reconstruction for questions extracted from the text layer of LaTeX-typeset PDFs.

The upstream bank read these PDFs with `pdftotext`, which flattens mathematics (x² → "x2", fractions →
numerator and denominator on separate lines, √ → a lone sign). The PDF itself still holds everything needed
to rebuild the formulas: every glyph with its font (Computer Modern math fonts vs. the Vietnamese text fonts),
size and exact position, and the rules TeX drew (fraction bars, radical overlines). This module reads a
question's region of the source page(s) and rebuilds stem, options and solution as content blocks with
KaTeX-ready TeX. It never guesses: a glyph without a known meaning, a drawing it cannot explain (a figure,
a table), or a layout it does not recognise makes the whole question `unresolved` with the reason.

Pure functions only (no database); `remediate` decides what to do with the result.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------
# fonts
# ------------------------------------------------------------------------------------------------
TEXT_FONT = re.compile(r"^(?:VN|Antykwa|TeXGyre|LMSans|LMRoman)", re.I)
BOLD_FONT = re.compile(r"(?:^VNBX|Bold|^VNBI)", re.I)
ITALIC_FONT = re.compile(r"(?:^VNTI|^VNBI|Italic)", re.I)
HEADING_FONT = re.compile(r"^(?:TeXGyre|LMSans|Antykwa)", re.I)
# overlays injected by the site that redistributed the PDFs (stamps, rotated URL); never content
STAMP_FONT = re.compile(r"^(?:ArialMT|Helvetica|Times-Roman|TimesNewRoman|FontAwesome)", re.I)
MATH_FONT = re.compile(r"^(?:CM(?:MI|R|SY|EX|BX|TI|SS|TT|MIB|BSY)\d|MSAM|MSBM|TeX-vect|Yhcmex|rsfs|eufm)", re.I)


def base_font(name: str) -> str:
    return name.split("+", 1)[-1]


# ------------------------------------------------------------------------------------------------
# glyph tables (TeX font encodings; keys are character codes)
# ------------------------------------------------------------------------------------------------
CMSY = {
    0x00: "-", 0x01: r"\cdot", 0x02: r"\times", 0x03: "*", 0x04: r"\div", 0x05: r"\diamond", 0x06: r"\pm",
    0x07: r"\mp", 0x08: r"\oplus", 0x09: r"\ominus", 0x0A: r"\otimes", 0x0B: r"\oslash", 0x0C: r"\odot",
    0x0D: r"\bigcirc", 0x0E: r"\circ", 0x0F: r"\bullet", 0x10: r"\asymp", 0x11: r"\equiv", 0x12: r"\subseteq",
    0x13: r"\supseteq", 0x14: r"\le", 0x15: r"\ge", 0x18: r"\sim", 0x19: r"\approx", 0x1A: r"\subset",
    0x1B: r"\supset", 0x1C: r"\ll", 0x1D: r"\gg", 0x20: r"\leftarrow", 0x21: r"\rightarrow", 0x22: r"\uparrow",
    0x23: r"\downarrow", 0x24: r"\leftrightarrow", 0x27: r"\simeq", 0x28: r"\Leftarrow", 0x29: r"\Rightarrow",
    0x2C: r"\Leftrightarrow", 0x2F: r"\propto", 0x30: "'", 0x31: r"\infty", 0x32: r"\in", 0x33: r"\ni",
    0x34: r"\triangle", 0x36: "NOT", 0x38: r"\forall", 0x39: r"\exists", 0x3A: r"\neg", 0x3B: r"\emptyset",
    0x3E: r"\top", 0x3F: r"\perp", 0x5B: r"\cup", 0x5C: r"\cap", 0x5E: r"\wedge", 0x5F: r"\vee",
    0x62: r"\lfloor", 0x63: r"\rfloor", 0x64: r"\lceil", 0x65: r"\rceil", 0x66: r"\{", 0x67: r"\}",
    0x68: r"\langle", 0x69: r"\rangle", 0x6A: "|", 0x6B: r"\parallel", 0x6E: r"\setminus", 0x70: "SQRT",
    0x72: r"\nabla",
}
for _c in range(0x41, 0x5B):
    CMSY[_c] = r"\mathcal{%s}" % chr(_c)
NEGATED = {"=": r"\ne", r"\in": r"\notin", r"\equiv": r"\not\equiv", r"\subset": r"\not\subset",
           r"\parallel": r"\nparallel", r"\le": r"\nleq", r"\ge": r"\ngeq", "<": r"\nless", ">": r"\ngtr"}

# cmex10: ("delim", tex, side) | ("op", tex) | ("piece", delimiter, part) | ("sqrt",) | ("wide", accent)
CMEX: dict[int, tuple] = {}
for _codes, _tex in (((0x00, 0x10, 0x12, 0x20), "("), ((0x01, 0x11, 0x13, 0x21), ")"),
                     ((0x02, 0x14, 0x22, 0x68), "["), ((0x03, 0x15, 0x23, 0x69), "]"),
                     ((0x04, 0x16, 0x24, 0x6A), r"\lfloor"), ((0x05, 0x17, 0x25, 0x6B), r"\rfloor"),
                     ((0x06, 0x18, 0x26, 0x6C), r"\lceil"), ((0x07, 0x19, 0x27, 0x6D), r"\rceil"),
                     ((0x08, 0x1A, 0x28, 0x6E), r"\{"), ((0x09, 0x1B, 0x29, 0x6F), r"\}"),
                     ((0x0A, 0x1C, 0x2A, 0x44), r"\langle"), ((0x0B, 0x1D, 0x2B, 0x45), r"\rangle"),
                     ((0x0E, 0x1E, 0x2C), "/"), ((0x0F, 0x1F, 0x2D), r"\backslash")):
    for _c in _codes:
        CMEX[_c] = ("delim", _tex)
CMEX[0x0C] = ("piece", "|", "ex")
CMEX[0x0D] = ("piece", r"\|", "ex")
for _c, _d, _p in ((0x30, "(", "tp"), (0x40, "(", "bt"), (0x42, "(", "ex"), (0x31, ")", "tp"), (0x41, ")", "bt"),
                   (0x43, ")", "ex"), (0x32, "[", "tp"), (0x34, "[", "bt"), (0x36, "[", "ex"), (0x33, "]", "tp"),
                   (0x35, "]", "bt"), (0x37, "]", "ex"), (0x38, r"\{", "tp"), (0x3A, r"\{", "bt"),
                   (0x3C, r"\{", "mid"), (0x3E, "{}", "ex"), (0x39, r"\}", "tp"), (0x3B, r"\}", "bt"),
                   (0x3D, r"\}", "mid")):
    CMEX[_c] = ("piece", _d, _p)
for _c, _op in ((0x50, r"\sum"), (0x58, r"\sum"), (0x51, r"\prod"), (0x59, r"\prod"), (0x52, r"\int"),
                (0x5A, r"\int"), (0x48, r"\oint"), (0x49, r"\oint"), (0x53, r"\bigcup"), (0x5B, r"\bigcup"),
                (0x54, r"\bigcap"), (0x5C, r"\bigcap")):
    CMEX[_c] = ("op", _op)
for _c in (0x70, 0x71, 0x72, 0x73):
    CMEX[_c] = ("sqrt",)
for _c in (0x62, 0x63, 0x64):
    CMEX[_c] = ("wide", r"\widehat")
for _c in (0x65, 0x66, 0x67):
    CMEX[_c] = ("wide", r"\widetilde")
# horizontal braces (\underbrace{x.x...x}_{n số}) are pieces of one brace; not supported → unknown

# Adobe private-use code points some PDFs give for cmex pieces (ToUnicode) → cmex code
PUA_CMEX = {0xF8EB: 0x30, 0xF8EC: 0x42, 0xF8ED: 0x40, 0xF8F6: 0x31, 0xF8F7: 0x43, 0xF8F8: 0x41,
            0xF8EE: 0x32, 0xF8EF: 0x36, 0xF8F0: 0x34, 0xF8F9: 0x33, 0xF8FA: 0x37, 0xF8FB: 0x35,
            0xF8F1: 0x38, 0xF8F2: 0x3C, 0xF8F3: 0x3A, 0xF8F4: 0x3E, 0xF8FC: 0x39, 0xF8FD: 0x3D, 0xF8FE: 0x3B}

# yhmath's yhcmex (large delimiters, radicals, wide hats); keys are the characters the PDF maps the codes to.
# Identified by rendering every code found in the source books.
YHCMEX = {}
for _chs, _tex in (("ÅÄÇÑÜ", "("), ("ãäåéê", ")"), ("îïñ", "["), ("óòô", "]"), ("®ß¶", r"\{"), ("´™©", r"\}")):
    for _ch in _chs:
        YHCMEX[_ch] = ("delim", _tex)
for _ch in "»…":
    YHCMEX[_ch] = ("sqrt",)
for _ch in " ’÷”‘ÿ¤◊\x00Ÿ":
    YHCMEX[_ch] = ("wide", r"\widehat")

MSAM = {0x03: "QED", 0x36: r"\leqslant", 0x3E: r"\geqslant", 0x04: r"\square"}
MSBM_LETTERS = set("NZQRC")

UNI = {  # unicode the PDFs give for CMMI/CMR/CMSY glyphs → TeX
    "−": "-", "·": r"\cdot", "×": r"\times", "∗": "*", "±": r"\pm", "∓": r"\mp", "≤": r"\le", "≥": r"\ge",
    "≠": r"\ne", "≡": r"\equiv", "≈": r"\approx", "∼": r"\sim", "≃": r"\simeq", "∞": r"\infty", "∈": r"\in",
    "∉": r"\notin", "∋": r"\ni", "∀": r"\forall", "∃": r"\exists", "∅": r"\emptyset", "∪": r"\cup",
    "∩": r"\cap", "⊂": r"\subset", "⊃": r"\supset", "⊆": r"\subseteq", "⊇": r"\supseteq", "⊥": r"\perp",
    "∥": r"\parallel", "∨": r"\vee", "∧": r"\wedge", "→": r"\rightarrow", "←": r"\leftarrow",
    "↔": r"\leftrightarrow", "⇒": r"\Rightarrow", "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow",
    "◦": r"\circ", "•": r"\bullet", "∠": r"\angle", "△": r"\triangle", "∆": r"\Delta", "∇": r"\nabla",
    "∂": r"\partial", "ℓ": r"\ell", "{": r"\{", "}": r"\}", "\\": r"\setminus", "%": r"\%", "$": r"\$",
    "&": r"\&", "#": r"\#", "′": "'", "ı": r"\imath", "ȷ": r"\jmath",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ϵ": r"\epsilon", "ε": r"\varepsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "ϕ": r"\phi",
    "φ": r"\varphi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}
PLAIN_MATH = set("0123456789+=()[]<>,.;:/!?|'")
ACCENTS = {"ˆ": r"\hat", "^": r"\hat", "˜": r"\tilde", "~": r"\tilde", "¯": r"\bar", "˙": r"\dot",
           "¨": r"\ddot", "ˇ": r"\check", "˘": r"\breve", "´": r"\acute", "`": r"\grave"}
FUNCS = {"sin", "cos", "tan", "cot", "tg", "cotg", "log", "ln", "lg", "lim", "max", "min", "exp", "arcsin",
         "arccos", "arctan", "sup", "inf", "det", "deg", "gcd", "sinh", "cosh", "tanh"}
KATEX_FUNCS = {"sin", "cos", "tan", "cot", "log", "ln", "lg", "lim", "max", "min", "exp", "arcsin", "arccos",
               "arctan", "sup", "inf", "det", "deg", "gcd", "sinh", "cosh", "tanh"}
LIMIT_OPS = {r"\sum", r"\prod", r"\bigcup", r"\bigcap", r"\lim", r"\max", r"\min"}


class Unresolved(Exception):
    """The question cannot be rebuilt with certainty; the message says why."""


# every glyph must reach the output exactly once (a dropped or duplicated glyph means a misread layout)
_LEDGER: dict[int, int] = {}


_CTX = {"script": 0, "size": 0.0}  # script nesting depth while typesetting; body text size of the question


class script_ctx:
    """Typesetting inside a superscript/subscript, limit, root index or script-size fraction."""
    def __enter__(self):
        _CTX["script"] += 1

    def __exit__(self, *exc):
        _CTX["script"] -= 1


def scripted(items, **kw) -> str:
    with script_ctx():
        return math_tex(items, **kw)


class small_ctx:
    """Inner parts of a structure made only of script-size glyphs (√2 in an exponent): typeset as script
    material; the structure itself is then checked where it is placed."""
    def __init__(self, items):
        self.on = bool(items) and bool(_CTX["size"]) and max(i.size for i in items) < 0.85 * _CTX["size"]

    def __enter__(self):
        if self.on:
            _CTX["script"] += 1

    def __exit__(self, *exc):
        if self.on:
            _CTX["script"] -= 1


def emit(*items):
    for it in items:
        _LEDGER[id(it)] = _LEDGER.get(id(it), 0) + 1


# ------------------------------------------------------------------------------------------------
# page layout
# ------------------------------------------------------------------------------------------------
@dataclass
class Glyph:
    ch: str            # unicode text pdfminer gives ('(cid:N)' when the font has no mapping)
    code: int          # character code in the font
    font: str
    size: float
    x0: float
    x1: float
    top: float
    bot: float
    base: float        # baseline (top-down coordinates)
    upright: bool = True
    used: bool = False

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2

    @property
    def is_text(self):
        return bool(TEXT_FONT.match(self.font))

    @property
    def is_math(self):
        return bool(MATH_FONT.match(self.font))


@dataclass
class Rule:  # a thin horizontal stroke (fraction bar, radical overline, \overline)
    x0: float
    x1: float
    y: float
    used: bool = False


@dataclass
class Shape:  # any other drawing (curve, box, image); must be explained or the question is refused
    kind: str
    x0: float
    x1: float
    top: float
    bot: float
    used: bool = False


@dataclass
class Page:
    number: int
    width: float
    height: float
    glyphs: list
    rules: list
    shapes: list
    body_top: float = 0.0
    body_bot: float = 0.0
    pdf: str = ""
    _img: object = None

    def image(self, dpi: int = 144):
        """Grayscale render of the page (cached): used to measure glyphs whose metrics the PDF does not give."""
        if self._img is None:
            import subprocess
            import tempfile
            from PIL import Image
            with tempfile.TemporaryDirectory() as tmp:
                r = subprocess.run(["pdftoppm", "-f", str(self.number), "-l", str(self.number), "-r", str(dpi),
                                    "-gray", "-png", "-singlefile", self.pdf, f"{tmp}/p"], capture_output=True,
                                   timeout=120)
                if r.returncode != 0:
                    raise Unresolved("page could not be rendered")
                self._img = (Image.open(f"{tmp}/p.png").convert("L"), dpi / 72.0)
        return self._img

    def ink_span(self, x0: float, x1: float, y: float, size: float) -> tuple[float, float] | None:
        """Vertical extent of the continuous stroke in columns x0..x1 that starts near y (a hanging glyph)."""
        im, s = self.image()
        px = im.load()
        w, h = im.size
        a0, a1 = max(0, int((x0 + 0.25 * (x1 - x0)) * s)), min(w, int((x1 - 0.25 * (x1 - x0)) * s) + 1)
        if a1 <= a0:
            return None

        def dark(yy):
            return any(px[xx, yy] < 128 for xx in range(a0, a1))
        # the stroke of the delimiter is the longest continuous run of ink in its columns near its baseline
        lo, hi = max(0, int((y - 2.5 * size) * s)), min(h, int((y + 3.5 * size) * s))
        runs, cur = [], None
        for yy in range(lo, hi):
            if dark(yy):
                cur = [yy, yy] if cur is None else [cur[0], yy]
            elif cur is not None:
                runs.append(cur)
                cur = None
        if cur is not None:
            runs.append(cur)
        near = [r for r in runs if r[1] >= (y - 0.5 * size) * s and r[0] <= (y + 0.9 * size) * s]
        if not near:
            return None
        start, end = max(near, key=lambda r: r[1] - r[0])
        return start / s, (end + 1) / s


def read_pages(pdf_path, pages: set[int]) -> dict[int, Page]:
    """Glyphs, rules and shapes of the given 1-based pages (one pass over the file)."""
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LTChar, LTCurve, LTFigure, LTImage, LTLine, LTRect
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage

    class Agg(PDFPageAggregator):
        def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):
            adv = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)
            self.cur_item._objs[-1].cid = cid
            return adv

    out: dict[int, Page] = {}
    with open(pdf_path, "rb") as fp:
        rm = PDFResourceManager(caching=True)
        dev = Agg(rm, laparams=None)
        it = PDFPageInterpreter(rm, dev)
        want = {p - 1 for p in pages}
        for i, page in enumerate(PDFPage.get_pages(fp, pagenos=want)):
            it.process_page(page)
            lt = dev.get_result()
            H = lt.height
            glyphs, rules, shapes = [], [], []

            def walk(objs):
                for o in objs:
                    if isinstance(o, LTChar):
                        a, b, c, d, e, f = o.matrix
                        upright = abs(b) < 1e-3 and abs(c) < 1e-3 and a > 0 and d > 0
                        glyphs.append(Glyph(o.get_text(), getattr(o, "cid", -1), base_font(o.fontname), o.size,
                                            o.x0, o.x1, H - o.y1, H - o.y0, H - f, upright))
                    elif isinstance(o, LTImage):
                        shapes.append(Shape("image", o.x0, o.x1, H - o.y1, H - o.y0))
                    elif isinstance(o, LTFigure):
                        walk(o)
                    elif isinstance(o, (LTLine, LTRect, LTCurve)):
                        h = o.y1 - o.y0
                        if h <= 1.3 and o.x1 - o.x0 > 1.0 and (isinstance(o, (LTLine, LTRect)) or len(o.pts) <= 5):
                            rules.append(Rule(o.x0, o.x1, H - (o.y0 + o.y1) / 2))
                        else:
                            shapes.append(Shape(type(o).__name__, o.x0, o.x1, H - o.y1, H - o.y0))
            walk(lt)
            pg = Page(sorted(want)[i] + 1 if len(want) else i + 1, lt.width, H, glyphs, rules, shapes, pdf=str(pdf_path))
            frame(pg)
            out[pg.number] = pg
    return out


def frame(pg: Page):
    """Body area: between the full-width header and footer rules the book class draws on every page."""
    long_ = [r for r in pg.rules if r.x1 - r.x0 > 0.6 * pg.width]
    tops = [r.y for r in long_ if r.y < 0.15 * pg.height]
    bots = [r.y for r in long_ if r.y > 0.85 * pg.height]
    pg.body_top = max(tops) if tops else 0.06 * pg.height
    pg.body_bot = min(bots) if bots else 0.94 * pg.height
    for r in long_:
        if r.y in tops or r.y in bots:
            r.used = True


# ------------------------------------------------------------------------------------------------
# items: glyphs and rebuilt boxes, each with a TeX/text rendering
# ------------------------------------------------------------------------------------------------
@dataclass
class Item:
    x0: float
    x1: float
    top: float
    bot: float
    base: float
    size: float
    kind: str                 # text | math | box | delim | op | sqrt | wide | piece | qed | not | accent
    val: str = ""             # text for text items, TeX for math items/boxes
    font: str = ""
    bold: bool = False
    italic: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2


def glyph_item(g: Glyph) -> Item:
    f = g.font
    it = Item(g.x0, g.x1, g.top, g.bot, g.base, g.size, "math", font=f)
    ch = g.ch
    if ch.startswith("(cid:"):
        ch = ""
    if g.is_text:
        if not ch:
            raise Unresolved(f"text glyph without unicode in {f} (code {g.code})")
        it.kind, it.val = "text", unicodedata.normalize("NFC", ch)
        it.bold, it.italic = bool(BOLD_FONT.search(f)), bool(ITALIC_FONT.search(f))
        return it
    fam = re.match(r"[A-Za-z-]+", f).group(0).upper()
    if fam == "CMEX":
        code = PUA_CMEX.get(ord(ch), g.code) if len(ch) == 1 and 0xF8E0 <= ord(ch) <= 0xF8FF else g.code
        spec = CMEX.get(code)
        if spec is None:
            raise Unresolved(f"unknown big symbol (cmex code {code:#x})")
        return special(it, spec)
    if fam == "YHCMEX":
        spec = YHCMEX.get(ch)
        if spec is None:
            raise Unresolved(f"unknown yhmath glyph {ch!r}")
        return special(it, spec)
    if fam == "CMSY":
        tex = CMSY.get(g.code)
        if tex is None:
            raise Unresolved(f"unknown symbol (cmsy code {g.code:#x})")
        if tex == "SQRT":
            return special(it, ("sqrt",))
        if tex == "NOT":
            it.kind = "not"
            return it
        it.val = tex
        return it
    if fam == "MSAM":
        tex = MSAM.get(g.code)
        if tex is None:
            raise Unresolved(f"unknown AMS symbol (msam code {g.code:#x})")
        if tex == "QED":
            it.kind = "qed"
        it.val = tex
        return it
    if fam == "MSBM":
        if ch in MSBM_LETTERS:
            it.val = r"\mathbb{%s}" % ch
            return it
        if ch == "∅":
            it.val = r"\varnothing"
            return it
        raise Unresolved(f"unknown AMS symbol (msbm {ch!r})")
    if fam == "TEX-VECT":
        it.kind, it.val = "arrow", ch or "?"
        return it
    if fam == "RSFS":
        if ch.isalpha():
            it.val = r"\mathscr{%s}" % ch
            return it
        raise Unresolved(f"unknown script glyph {ch!r}")
    if fam in ("CMMI", "CMR", "CMBX", "CMTI", "CMSS", "CMMIB", "CMBSY", "CMTT"):
        if not ch:
            raise Unresolved(f"glyph without unicode in {f} (code {g.code})")
        if ch in ACCENTS and fam == "CMR":
            it.kind, it.val = "accent", ACCENTS[ch]
            return it
        if ch in ("ﬀ", "ﬁ", "ﬂ", "ﬃ", "ﬄ"):
            ch = unicodedata.normalize("NFKC", ch)
        if fam == "CMMI" and ch == "/":
            it.val = "/"
            return it
        if len(ch) == 1 and ch in UNI:
            it.val = UNI[ch]
        elif all(c.isascii() and (c.isalnum() or c in PLAIN_MATH) for c in ch):
            it.val = ch
            if fam == "CMR" and ch.isalpha():
                it.extra["upright"] = True
            if fam == "CMBX":
                it.extra["boldmath"] = True
        else:
            raise Unresolved(f"unknown math glyph {ch!r} in {f}")
        return it
    raise Unresolved(f"glyph in unsupported font {f}")


def special(it: Item, spec: tuple) -> Item:
    it.kind = spec[0]
    if spec[0] == "delim":
        it.val = spec[1]
    elif spec[0] == "op":
        it.val = spec[1]
    elif spec[0] == "piece":
        it.val, it.extra["part"] = spec[1], spec[2]
    elif spec[0] == "wide":
        it.val = spec[1]
    return it


# ------------------------------------------------------------------------------------------------
# TeX assembly of a horizontal list of items
# ------------------------------------------------------------------------------------------------
def grp(t: str) -> str:
    return t if len(t) == 1 and t.isalnum() else "{" + t + "}"


def join_tex(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out and re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z]", p):
            out += " "
        out += p
    return out


def math_tex(items: list[Item], base: float | None = None, size: float | None = None) -> str:
    """TeX for a horizontal list of math items (scripts recognised by size and baseline offset)."""
    items = sorted(items, key=lambda i: (i.x0, i.top))
    if not items:
        return ""
    if size is None:
        size = max(i.size for i in items)
    if base is None:
        full = [i for i in items if i.size >= 0.85 * size and i.kind in ("math", "text")]
        base = median([i.base for i in full]) if full else median([i.base for i in items])
    parts: list[str] = []
    k = 0
    pending_not = False
    while k < len(items):
        it = items[k]
        small = it.size < 0.85 * size and it.kind in ("math", "not", "text", "box")
        off = it.base - base

        def raised(j):  # a full-size fraction/radical lifted into an exponent (a^{\dfrac13})
            return j.kind == "box" and j.size >= 0.85 * size and parts and j.base < base - 0.45 * size
        if small or (it.kind in ("math",) and abs(off) > 0.28 * size) or raised(it):
            # a run of script items: split into superscript/subscript by baseline
            run = []
            while k < len(items):
                j = items[k]
                if (j.size < 0.85 * size and j.kind in ("math", "not", "text", "box")) or \
                        (j.kind in ("math", "box") and abs(j.base - base) > 0.28 * size) or raised(j):
                    if j.kind == "box" and j.size >= 0.85 * size and abs(j.base - base) <= 0.28 * size \
                            and not raised(j):
                        break
                    run.append(j)
                    k += 1
                else:
                    break
            axis = base - 0.25 * size

            def level(j):  # boxes (fractions, radicals) by their centre against the math axis
                if j.kind == "box":
                    c = (j.top + j.bot) / 2
                    return -1 if c < axis - 1.0 else (1 if c > axis + 1.0 else 0)
                return -1 if j.base < base - 0.5 else (1 if j.base > base + 0.5 else 0)
            sup = [j for j in run if level(j) < 0]
            sub = [j for j in run if level(j) > 0]
            far = [j for j in run if j.kind != "box" and not (-0.9 * size <= j.base - base <= 0.6 * size)]
            if far:
                raise Unresolved(f"script {far[0].val!r} too far from the baseline (belongs to another row)")
            if len(sup) + len(sub) != len(run):
                raise Unresolved(f"small glyph on the baseline (script position unclear): {[j.val for j in run]}")
            if sup and sub and min(j.x0 for j in sup) > max(j.x1 for j in sub) + 0.5 * size:
                raise Unresolved("subscript followed by a separate superscript")
            nucleus = parts.pop() if parts else "{}"
            s = nucleus
            if sub:
                s += "_" + grp(scripted(sub))
            if sup:
                st = scripted(sup)
                s += ("'" * st.count("'")) if st and set(st) == {"'"} else "^" + grp(st)
            parts.append(s)
            continue
        if it.kind == "not":
            emit(it)
            pending_not = True
            k += 1
            continue
        if it.kind == "text":
            # a Vietnamese word inside a formula (e.g. in cases rows or limits): \text{}
            run = []
            while k < len(items) and items[k].kind == "text" and abs(items[k].base - base) <= 0.28 * size:
                run.append(items[k])
                k += 1
            parts.append(r"\text{%s}" % text_of(run, mark=True).replace("\\", "").replace("{", "").replace("}", ""))
            continue
        tex = it.val
        if it.kind not in ("math", "box", "op"):
            raise Unresolved(f"unexpected {it.kind} inside a formula")
        if _CTX["size"] and it.size < 0.85 * _CTX["size"] and not _CTX["script"]:
            raise Unresolved(f"script-size {'formula' if it.kind == 'box' else 'glyph'} {it.val[:30]!r} read as "
                             "a normal symbol")
        if it.kind != "box":
            emit(it)
        if it.extra.get("upright"):
            # consecutive upright letters → a function name or \mathrm{}
            word = [it]
            while k + 1 < len(items) and items[k + 1].extra.get("upright") and \
                    items[k + 1].x0 - word[-1].x1 < 0.12 * size and abs(items[k + 1].base - it.base) < 0.5:
                k += 1
                word.append(items[k])
                emit(items[k])
            w = "".join(x.val for x in word)
            if w in KATEX_FUNCS:
                tex = "\\" + w
            elif w in FUNCS:
                tex = r"\operatorname{%s}" % w
            else:
                tex = r"\mathrm{%s}" % w
        if pending_not:
            if tex not in NEGATED:
                raise Unresolved(f"negation slash over {tex}")
            tex = NEGATED[tex]
            pending_not = False
        idx = items.index(it)
        prev_ = max(items[:idx], key=lambda j: j.x1) if idx else None
        gap_ = it.x0 - prev_.x1 if prev_ else 0
        if parts and prev_ and (gap_ > 0.8 * size or (gap_ > 0.4 * size and (prev_.extra.get("system")
                                                                              or it.extra.get("system")))):
            parts.append(r"\quad ")  # a wide space set on purpose: (t \in \mathbb{R}) after a system
        parts.append(tex)
        k += 1
    if pending_not:
        raise Unresolved("dangling negation slash")
    return join_tex(parts)


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def text_of(items: list[Item], mark=False) -> str:
    """Text of consecutive text glyphs; spaces from the gaps (the Vietnamese fonts carry no space glyphs)."""
    if mark:
        emit(*items)
    s = ""
    prev = None
    for it in sorted(items, key=lambda i: i.x0):
        if prev is not None and it.x0 - prev.x1 > 0.17 * max(it.size, prev.size):
            s += " "
        s += it.val
        prev = it
    return unicodedata.normalize("NFC", s)


# ------------------------------------------------------------------------------------------------
# structures (built innermost first, each becomes one "box" item)
# ------------------------------------------------------------------------------------------------
def overlap(a0, a1, b0, b1) -> float:
    return min(a1, b1) - max(a0, b0)


def build_structures(items: list[Item], rules: list[Rule], size: float) -> list[Item]:
    items = list(items)
    items = build_pieces(items)
    items = build_arrows(items, size)
    # radicals and fractions in order of rule width (inner structures first)
    for r in sorted([r for r in rules if not r.used], key=lambda r: r.x1 - r.x0):
        items = build_rule(items, r, size)
    items = build_wide(items, size)
    items = build_limits(items, size)
    items = build_accents(items, size)
    items = attach_delims(items, size)
    for it in items:
        if it.kind == "sqrt":
            raise Unresolved("radical sign without its overline")
        if it.kind == "arrow":
            raise Unresolved("vector arrow without a symbol under it")
        if it.kind == "wide":
            raise Unresolved("wide accent without a symbol under it")
        if it.kind == "piece":
            raise Unresolved("loose piece of a big delimiter")
    return items


OPENERS = ("(", "[", r"\{", r"\langle", r"\lfloor", r"\lceil")
CLOSERS = (")", "]", r"\}", r"\rangle", r"\rfloor", r"\rceil")


def attach_delims(items: list[Item], size: float) -> list[Item]:
    """A big delimiter glyph hangs from its own baseline; it belongs to the line of the symbol it encloses
    (its neighbour on the inner side)."""
    others = [i for i in items if i.kind not in ("delim", "op") and i.size >= 0.85 * size]
    for d in [i for i in items if (i.kind == "delim" and not i.extra.get("built")) or
              (i.kind == "op" and i.size >= 0.85 * size and i.font.upper().startswith(("CMEX", "YHCMEX")))]:
        cands = []
        if d.kind == "delim":  # measured on the page image: the enclosed symbols sit within the stroke
            lo_b, hi_b = d.top + 0.3 * size, d.bot + 0.3 * size
        else:  # ∫ hangs from its baseline
            lo_b, hi_b = d.base + 0.4 * size, d.base + 2.6 * size
        if d.val not in CLOSERS:
            reach = 2.5 * size if d.kind == "op" else 1.2 * size  # past the limits of ∫
            cands += [(i.x0 - d.x1, i) for i in others if d.x1 - 1.0 <= i.x0 <= d.x1 + reach
                      and lo_b <= i.base <= hi_b]
        if d.val not in OPENERS and d.kind == "delim":
            cands += [(d.x0 - i.x1, i) for i in others if d.x0 - 1.2 * size <= i.x1 <= d.x0 + 1.0
                      and lo_b <= i.base <= hi_b]
        if not cands:
            raise Unresolved(f"big delimiter {d.val} without a neighbour")
        gap = min(c[0] for c in cands)
        near = [c[1] for c in cands if c[0] - gap < 0.3]
        if len({round(n.base) for n in near}) > 1:
            if d.kind != "delim" or d.bot - d.top <= 1.6 * size:
                raise Unresolved(f"big delimiter {d.val} between two rows")
            # a tall { or [ in front of stacked rows: a system, rebuilt by systems() around its centre
            centre = (d.top + d.bot) / 2 + 0.25 * size
            near = [min(near, key=lambda n: abs(n.base - centre))]
        d.base = near[0].base
        if d.kind == "op":
            # the limits of a displayed ∫ sit beside it, well above/below the line
            lims = [i for i in items if i.size < 0.85 * size and i.kind in ("math", "box", "not")
                    and d.x0 - 0.2 * size <= i.x0 <= d.x1 + 1.0 * size
                    and d.top - 0.6 * size <= i.base <= d.base + 1.9 * size and abs(i.base - d.base) > 0.3 * size]
            hi = [i for i in lims if i.base < d.base]
            lo = [i for i in lims if i.base > d.base]
            if lims:
                emit(d)
                tex = d.val + ("_" + grp(scripted(lo)) if lo else "") + ("^" + grp(scripted(hi)) if hi else "")
                b = box([d] + lims, tex, base=d.base)
                b.size = d.size
                items = [i for i in items if i is not d and i not in lims] + [b]
    for d in [i for i in items if i.kind == "delim"]:
        for b in others:
            if b.kind == "box" and b.x0 + 0.5 < d.xc < b.x1 - 0.5 and abs(b.base - d.base) < 0.5 * size:
                raise Unresolved(f"big delimiter {d.val} inside a fraction/radical")
    return items


def build_pieces(items: list[Item]) -> list[Item]:
    """Stacked pieces of an extensible delimiter (top/extension/middle/bottom) → one delimiter."""
    pieces = sorted([i for i in items if i.kind == "piece"], key=lambda i: (i.top, i.x0))
    rest = [i for i in items if i.kind != "piece"]
    while pieces:
        p = pieces.pop(0)
        col = [p]
        for q in list(pieces):
            if abs(q.x0 - p.x0) < 1.5 and 0 < q.base - col[-1].base <= 2.0 * q.size and q.val in (p.val, "{}") \
                    and col[-1].extra["part"] != "bt":
                col.append(q)
                pieces.remove(q)
        vals = {c.val for c in col} - {"{}"}
        if len(vals) != 1:
            if vals == set() and all(c.val == "{}" for c in col):
                raise Unresolved("brace extension without its brace")
            raise Unresolved("mixed delimiter pieces")
        val = vals.pop()
        parts = [c.extra["part"] for c in col]
        if val in ("|", r"\|"):
            pass
        elif parts[0] != "tp" or parts[-1] != "bt":
            raise Unresolved(f"incomplete extensible delimiter {val}")
        emit(*col)
        # cmex pieces hang from their baseline: the drawn brace runs from the first baseline down
        top, bot = col[0].base, col[-1].base + 0.9 * col[-1].size
        d = Item(min(c.x0 for c in col), max(c.x1 for c in col), top, bot, (top + bot) / 2, col[0].size,
                 "delim", val)
        d.extra["built"] = True
        rest.append(d)
    return rest


def under(items, x0, x1, top, depth, size):
    return [i for i in items if x0 - 1.0 <= i.xc <= x1 + 1.0 and i.top >= top - 1.0 and i.top <= top + depth]


def build_arrows(items: list[Item], size: float) -> list[Item]:
    arrows = sorted([i for i in items if i.kind == "arrow"], key=lambda i: i.x0)
    rest = [i for i in items if i.kind != "arrow"]
    # pieces of one arrow share a baseline and touch each other
    groups = []
    for a in arrows:
        # an arrow is drawn from its tail ('#') through extension pieces to its head ('»')
        if groups and a.val != "#" and groups[-1][-1].val != "»" and abs(groups[-1][-1].base - a.base) < 0.5 \
                and a.x0 - groups[-1][-1].x1 < 0.8 * size:
            groups[-1].append(a)
        else:
            groups.append([a])
    for g in groups:
        if g[0].val != "#" or g[-1].val != "»":
            raise Unresolved("vector arrow without its tail or head")
        x0, x1 = min(a.x0 for a in g), max(a.x1 for a in g)
        top, bot = min(a.top for a in g), max(a.bot for a in g)
        # the glyph box of the arrow is tall; its stroke sits near the baseline of the arrow font
        stroke = max(a.base for a in g)
        cov = [i for i in rest if x0 - 1.5 <= i.xc <= x1 + 1.5 and i.top >= stroke - 0.45 * size
               and i.top <= stroke + 0.6 * size and i.kind in ("math", "box")]
        if not cov:
            raise Unresolved("vector arrow over nothing")
        rest = [i for i in rest if i not in cov]
        emit(*g)
        with small_ctx(cov):
            inner = math_tex_delims(cov)
        rest.append(box(cov, r"\overrightarrow{%s}" % inner, extra_top=top))
    return rest


def box(members: list[Item], tex: str, extra_top=None, extra_bot=None, base=None, x0=None, x1=None) -> Item:
    full = [m for m in members if m.size >= 0.85 * max(x.size for x in members)] or members
    return Item(min([m.x0 for m in members] + ([x0] if x0 is not None else [])),
                max([m.x1 for m in members] + ([x1] if x1 is not None else [])),
                min([m.top for m in members] + ([extra_top] if extra_top is not None else [])),
                max([m.bot for m in members] + ([extra_bot] if extra_bot is not None else [])),
                base if base is not None else median([m.base for m in full]),
                max(m.size for m in members), "box", tex)


def build_rule(items: list[Item], r: Rule, size: float) -> list[Item]:
    w = r.x1 - r.x0
    # radical: the sign ends where the overline starts
    for s in items:
        # TeX hangs the radical sign from the overline: its baseline is the rule's height
        if s.kind == "sqrt" and abs(s.x1 - r.x0) < 1.6 and (abs(s.base - r.y) < 2.0 or abs(s.top - r.y) < 2.0):
            rad = [i for i in items if i is not s and i.kind in ("math", "box", "op", "not")
                   and r.x0 - 0.5 <= i.xc <= r.x1 + 0.5 and r.y - 1.0 <= i.top and i.top <= r.y + 0.9 * size]
            if not rad:
                raise Unresolved("empty radical")
            rb = median([i.base for i in rad if i.size >= 0.85 * size] or [i.base for i in rad])
            idx = [i for i in items if i is not s and i not in rad and i.size < 0.85 * size and i.kind == "math"
                   and i.x0 >= s.x0 - 0.5 and i.xc > s.x0 and i.x1 <= s.x0 + 0.75 * (s.x1 - s.x0)
                   and i.base < rb - 1.0 and i.bot >= r.y - 0.5 * size]
            with small_ctx(rad):
                inner = math_tex_delims(rad)
            emit(s)
            tex = r"\sqrt[%s]{%s}" % (scripted(idx), inner) if idx else r"\sqrt{%s}" % inner
            r.used = True
            members = rad + idx + [s]
            b = box(rad, tex, extra_top=r.y - 0.5, base=rb, x0=min(m.x0 for m in members), x1=r.x1)
            return [i for i in items if i not in members] + [b]
    inside = [i for i in items if i.kind in ("math", "box", "op", "not") and
              i.x0 >= r.x0 - 1.5 and i.x1 <= r.x1 + 1.5]
    # numerator baselines sit above the bar, denominator baselines below it (TeX fraction shifts)
    num = [i for i in inside if r.y - 1.6 * size <= i.base <= r.y - 1.0 and i.bot <= r.y + 1.0]
    den = [i for i in inside if r.y + 1.0 <= i.base <= r.y + 2.2 * size and i.top >= r.y - 1.0]
    num = keep_stack(num, r.y, above=True, size=size)
    den = keep_stack(den, r.y, above=False, size=size)
    if num and den:
        if set(map(id, num)) & set(map(id, den)):
            raise Unresolved("fraction parts overlap")
        small = max(i.size for i in num + den) < 0.85 * size
        if small:
            with script_ctx():
                tex = r"\frac{%s}{%s}" % (math_tex_delims(num), math_tex_delims(den))
        else:
            tex = r"\dfrac{%s}{%s}" % (math_tex_delims(num), math_tex_delims(den))
        r.used = True
        members = num + den
        return [i for i in items if i not in members] + [
            box(members, tex, base=r.y + 0.25 * size, x0=r.x0, x1=r.x1)]
    if not num and not den:
        # \overline{...}: the stroke lies on top of the symbols
        cov = [i for i in items if r.x0 - 1.0 <= i.xc <= r.x1 + 1.0 and r.y + 0.5 <= i.base <= r.y + 1.3 * size
               and i.kind in ("math", "box") and i.x0 >= r.x0 - 1.5 and i.x1 <= r.x1 + 1.5]
        if cov:
            r.used = True
            with small_ctx(cov):
                tex = r"\overline{%s}" % math_tex(cov)
            return [i for i in items if i not in cov] + [box(cov, tex, extra_top=r.y)]
    if w < 2.0:
        return items  # a stray hairline; left unexplained → refused later only if it carries ink
    return items


def keep_stack(part, y, above, size):
    """The row next to the bar plus the scripts touching it; anything else belongs to another line."""
    if not part:
        return part
    full = [i for i in part if i.size >= 0.85 * max(j.size for j in part)]
    near = min(full, key=lambda i: abs(i.base - y)).base
    row = [i for i in full if abs(i.base - near) <= 0.25 * size or
           (i.kind == "box" and overlap(i.top, i.bot, y - 2 * size, y + 2 * size) > 0 and abs(i.base - near) <= 1.2 * size)]
    rest = [i for i in part if i not in row]
    grown = True
    while grown:  # scripts must touch the row (x² in a numerator), not merely lie under the bar
        grown = False
        for i in list(rest):
            # a script follows its nucleus, within TeX's script shifts (sup ≲ 0.65 em up, sub ≲ 0.45 em down)
            if -0.65 * size <= i.base - near <= 0.45 * size and any(-1.0 <= i.x0 - j.x1 < 0.4 * size for j in row):
                row.append(i)
                rest.remove(i)
                grown = True
    return row


def build_wide(items: list[Item], size: float) -> list[Item]:
    for w in [i for i in items if i.kind == "wide"]:
        # the accent hangs from its baseline just above the symbols it covers
        cov = [i for i in items if i is not w and w.x0 - 2.0 <= i.xc <= w.x1 + 2.0 and i.kind in ("math", "box", "delim")
               and w.base - 0.2 * size <= i.base <= w.base + 1.2 * size]
        if not cov:
            raise Unresolved("wide accent over nothing")
        items = [i for i in items if i is not w and i not in cov]
        emit(w)
        with small_ctx(cov):
            tex = "%s{%s}" % (w.val, math_tex_delims(cov))
        items.append(box(cov, tex, extra_top=w.top))
    return items


def build_accents(items: list[Item], size: float) -> list[Item]:
    for a in [i for i in items if i.kind == "accent"]:
        cov = [i for i in items if i is not a and i.kind in ("math", "box") and overlap(i.x0, i.x1, a.x0, a.x1) > 0
               and i.top >= a.top - 0.5 and i.top <= a.bot + 0.3 * size and i.base > a.base + 1.0]
        if len(cov) != 1:
            raise Unresolved("accent position unclear")
        items = [i for i in items if i is not a and i not in cov]
        emit(a)
        with small_ctx(cov):
            tex = "%s{%s}" % (a.val, math_tex(cov))
        items.append(box(cov, tex, extra_top=a.top))
    return items


LIMIT_WORDS = ("lim", "max", "min", "sup", "inf")


def build_limits(items: list[Item], size: float) -> list[Item]:
    """Limits stacked under/over ∑, ∏, lim, max, min (display style)."""
    out = list(items)
    # upright lim/max/min with a limit written under it
    ups = sorted([i for i in out if i.extra.get("upright")], key=lambda i: (round(i.base), i.x0))
    words, cur = [], []
    for u in ups:
        if cur and abs(u.base - cur[-1].base) < 0.5 and u.x0 - cur[-1].x1 < 0.12 * size:
            cur.append(u)
        else:
            if cur:
                words.append(cur)
            cur = [u]
    if cur:
        words.append(cur)
    for w in words:
        name = "".join(x.val for x in w)
        if name not in LIMIT_WORDS:
            continue
        x0, x1, base = w[0].x0, w[-1].x1, w[0].base
        lo = [i for i in out if i not in w and i.size < 0.85 * w[0].size and x0 - 0.8 * size <= i.xc <= x1 + 0.8 * size
              and base + 0.35 * size <= i.base <= base + 1.4 * size]
        if not lo:
            continue
        if min(i.x0 for i in lo) < x0 - 1.2 * size or max(i.x1 for i in lo) > x1 + 1.2 * size:
            raise Unresolved(f"limit under {name} wider than expected")
        emit(*w)
        tex = "\\%s_{%s}" % (name, scripted(lo))
        members = w + lo
        out = [i for i in out if i not in members]
        b = box(members, tex, base=base)
        b.size = w[0].size
        out.append(b)
    for op in [i for i in items if i.kind == "op" and i.val in (r"\sum", r"\prod", r"\bigcup", r"\bigcap")]:
        hang = op.font.upper().startswith(("CMEX", "YHCMEX"))
        top, bot = (op.base, op.base + 1.5 * op.size) if hang else (op.top, op.bot)
        lo = [i for i in out if i is not op and i.size < 0.85 * op.size and op.x0 - 0.6 * size <= i.xc <= op.x1 + 0.6 * size
              and bot - 0.3 * size <= i.base <= bot + 1.2 * size]
        hi = [i for i in out if i is not op and i.size < 0.85 * op.size and op.x0 - 0.6 * size <= i.xc <= op.x1 + 0.6 * size
              and top - 0.9 * size <= i.base <= top + 0.2 * size]
        if not lo and not hi:
            continue
        emit(op)
        tex = op.val + ("_" + grp(scripted(lo)) if lo else "") + ("^" + grp(scripted(hi)) if hi else "")
        members = [op] + lo + hi
        out = [i for i in out if i not in members]
        b = box(members, tex, base=op.base)
        b.size = op.size
        out.append(b)
    return out


def math_tex_delims(items: list[Item]) -> str:
    return math_tex([pair_delims(items)] if any(i.kind == "delim" for i in items) else items)


def pair_delims(items: list[Item]) -> Item:
    return box(items, delim_tex(items))


def delim_tex(items: list[Item], base: float | None = None, size: float | None = None) -> str:
    """A horizontal list that contains big delimiters: \\left…\\right pairs around the inner parts."""
    items = sorted(items, key=lambda i: i.x0)
    out: list = []
    stack: list[list] = [[]]
    for it in items:
        if it.kind == "delim":
            emit(it)
            v = it.val
            opening = v in ("(", "[", r"\{", r"\langle", r"\lfloor", r"\lceil")
            closing = v in (")", "]", r"\}", r"\rangle", r"\rfloor", r"\rceil")
            if opening:
                stack.append([("open", v)])
            elif closing:
                if len(stack) < 2:
                    raise Unresolved(f"unbalanced big delimiter {v}")
                grp_ = stack.pop()
                inner = [x for x in grp_[1:]]
                stack[-1].append(("tex", r"\left%s %s \right%s" % (grp_[0][1], seq_tex(inner, base, size), v)))
            elif v in ("|", r"\|"):
                # |…| pairs: the second one closes
                if len(stack) >= 2 and stack[-1][0] == ("open", v):
                    grp_ = stack.pop()
                    stack[-1].append(("tex", r"\left%s %s \right%s" % (v, seq_tex(grp_[1:], base, size), v)))
                else:
                    stack.append([("open", v)])
            else:
                raise Unresolved(f"big {v} delimiter")
        else:
            stack[-1].append(("item", it))
    if len(stack) != 1:
        raise Unresolved("unbalanced big delimiters")
    return seq_tex(stack[0], base, size)


def seq_tex(seq, base=None, size=None) -> str:
    """Items between big delimiters, typeset against the baseline of the whole formula (a run that starts
    with a script after \right) — (…)² — must not be read on its own)."""
    if base is None:
        full = [v for k, v in seq if k == "item" and v.kind in ("math", "text") and
                (size is None or v.size >= 0.85 * size)]
        if full:
            base = median([v.base for v in full])
            size = size or max(v.size for v in full)
    parts, run = [], []

    def flush():
        t = math_tex(run, base=base, size=size)
        if parts and re.match(r"\{\}[\^_]", t):
            parts[-1] += t[2:]  # the script belongs to the delimited group before it
        elif t:
            parts.append(t)
    for kind, v in seq:
        if kind == "item":
            run.append(v)
        else:
            if run:
                flush()
                run = []
            parts.append(v)
    if run:
        flush()
    return " ".join(p for p in parts if p)


# ------------------------------------------------------------------------------------------------
# lines → paragraphs
# ------------------------------------------------------------------------------------------------
@dataclass
class Line:
    base: float
    items: list
    top: float = 0.0
    bot: float = 0.0
    x0: float = 0.0
    x1: float = 0.0


def make_lines(items: list[Item], size: float) -> list[Line]:
    main = sorted([i for i in items if i.kind in ("text", "math", "qed") and i.size >= 0.85 * size],
                  key=lambda i: i.base)
    bases: list[list[float]] = []
    for i in main:
        if bases and i.base - bases[-1][-1] < 0.25 * size:
            bases[-1].append(i.base)
        else:
            bases.append([i.base])
    lbase = [median(b) for b in bases]
    lines = [Line(b, []) for b in lbase]
    tall = []
    is_small = lambda i: i.size < 0.85 * size and i.kind in ("math", "not")  # noqa: E731
    placed: dict[int, Line] = {}
    # full-size items first: a script then follows the structure it is attached to
    for i in sorted(items, key=lambda i: (is_small(i), i.x0)):
        if not lines:
            break
        if is_small(i):
            prev = [j for j in items if id(j) in placed and j.kind in ("box", "delim") and -1.0 <= i.x0 - j.x1 < 0.6 * size
                    and j.top - 0.6 * size <= i.base <= j.bot + 0.6 * size and j.bot - j.top > 1.4 * size]
            run_host = [j for j in items if id(j) in placed and is_small(j) and abs(j.base - i.base) < 0.5
                        and -1.0 <= i.x0 - j.x1 < 0.3 * size and placed[id(j)] is not None and j.extra.get("hosted")]
            if len(prev) == 1 or run_host:
                L = placed[id(prev[0])] if len(prev) == 1 else placed[id(run_host[0])]
                L.items.append(i)
                placed[id(i)] = L
                i.extra["hosted"] = True
                continue
        near = min(lines, key=lambda L: abs(L.base - i.base))
        tol = 0.3 * size if i.kind in ("text", "math", "qed", "not") else 0.45 * size
        if i.kind in ("text", "math", "not") and i.size < 0.85 * size:
            tol = 0.75 * size
        if abs(near.base - i.base) <= tol:
            near.items.append(i)
            placed[id(i)] = near
            continue
        spans = [L for L in lines if i.kind == "box" and i.top <= L.base <= i.bot]
        if len(spans) == 1:  # e.g. a script-size fraction used as a subscript (log base)
            spans[0].items.append(i)
            placed[id(i)] = spans[0]
        else:
            tall.append(i)
    if tall:
        # a script of a tall structure (…)², √…² sits far above/below the line: it follows that structure
        smalls = sorted([i for i in tall if i.size < 0.85 * size and i.kind in ("math", "not")], key=lambda i: i.x0)
        runs: list[list[Item]] = []
        for i in smalls:  # a script is a run of small glyphs on one baseline (2018, n+1)
            if runs and abs(runs[-1][-1].base - i.base) < 0.5 and i.x0 - runs[-1][-1].x1 < 0.3 * size:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            i = run[0]
            hosts = [(L, j) for L in lines for j in L.items if j.kind in ("box", "delim") and -1.0 <= i.x0 - j.x1 < 0.6 * size
                     and j.top - 0.6 * size <= i.base <= j.bot + 0.6 * size]
            if len(hosts) == 1:
                hosts[0][0].items.extend(run)
                for x in run:
                    tall.remove(x)
        # boxes/delimiters whose axis is not on any text line (e.g. a displayed system): own line
        for i in tall:
            lines.append(Line(i.base, [i]))
    lines = [L for L in lines if L.items]
    merged: list[Line] = []
    for L in sorted(lines, key=lambda L: L.base):
        L.top, L.bot = min(i.top for i in L.items), max(i.bot for i in L.items)
        L.x0, L.x1 = min(i.x0 for i in L.items), max(i.x1 for i in L.items)
        merged.append(L)
    return merged


def systems(lines: list[Line], size: float) -> list[Line]:
    """A big { or [ followed by several stacked rows → cases / bracket system (one box on the middle line)."""
    cands = sorted([(d, L) for L in lines for d in L.items if d.kind == "delim" and d.val in (r"\{", "[")
                    and d.bot - d.top > 1.6 * size], key=lambda t: t[0].x0)  # left to right: nested systems
    for d, L in cands:
        if d not in L.items:
            continue
        if True:
            rows = [R for R in lines if R is not L and d.top - 0.2 * size <= R.base <= d.bot + 0.2 * size] + [L]
            right_close = [i for R in rows for i in R.items if i.kind == "delim" and i.x0 > d.x1 and
                           i.val in (r"\}", "]") and abs(i.top - d.top) < 2 and abs(i.bot - d.bot) < 2]
            if right_close:
                continue  # a set in big braces: handled as \left\{ … \right\}
            # the system ends before the next option label or the next big brace on any of its rows
            stop = []
            for R in rows:
                ordered = sorted(R.items, key=lambda i: i.x0)
                stop += [i.x0 for j, i in enumerate(ordered) if i.x0 > d.x1 and (
                    (i.kind == "delim" and i.val in (r"\{", "[") and i.bot - i.top > 1.6 * size) or
                    (i.kind == "box" and i.extra.get("system")) or
                    (i.kind == "text" and i.bold and i.val in OPT_LABELS and j + 1 < len(ordered)
                     and ordered[j + 1].val == "."))]
            right = min(stop) if stop else 1e9
            row_items = []
            for R in sorted(rows, key=lambda R: R.base):
                its = [i for i in R.items if i.x0 >= d.x1 - 0.5 and i.x1 <= right and i.top >= d.top - 0.6 * size
                       and i.bot <= d.bot + 0.6 * size]
                row_items.append((R, its))
            # the system ends where the rows' math ends; text after it on the middle line stays outside
            mx = max((i.x1 for R, its in row_items for i in its if i.kind != "text"), default=None)
            if mx is None:
                continue
            body_rows = []
            # a row of the system starts right after the brace; a line starting further right (⇔ on the
            # system's axis line) is what follows the system
            row_items = [(R, its) for R, its in row_items if its and min(i.x0 for i in its) <= d.x1 + 1.5 * size]
            mx = max((i.x1 for R, its in row_items for i in its if i.kind != "text"), default=None)
            if mx is None:
                continue
            # the line the system sits in (text before the brace): what follows the rows there is the rest
            # of the sentence; the system's width comes from its other rows
            host = {id(R) for R, its in row_items if any(i.x1 <= d.x0 + 0.5 for i in R.items)}
            others = [i for R, its in row_items if id(R) not in host for i in its if i.kind != "text"]
            if others:
                mx = max(i.x1 for i in others)
            for R, its in row_items:
                if id(R) in host:
                    inside = []
                    for i in sorted(its, key=lambda i: i.x0):
                        if i.kind == "text" and i.x0 >= mx - 0.3 * size:
                            break  # the sentence continues after the system
                        if inside and i.x0 >= mx - 0.3 * size and i.x0 - max(j.x1 for j in inside) >= 0.5 * size:
                            break  # set apart by a wide space: "(t ∈ ℝ)" written after the system
                        inside.append(i)
                else:
                    # text after the rows' formulas (punctuation, words) stays outside the system
                    inside = [i for i in its if i.x0 <= mx + 0.3 * size and not (i.kind == "text" and i.x0 > mx - 0.5 * size)]
                if any(i.kind != "text" for i in inside):
                    body_rows.append(inside)
                elif inside:
                    raise Unresolved("text inside a system of equations")
            if len(body_rows) < 2:
                continue
            env = "cases" if d.val == r"\{" else "aligned"
            rows_tex = r" \\ ".join(row_tex(r, size) for r in body_rows)
            tex = r"\begin{cases} %s \end{cases}" % rows_tex if env == "cases" else \
                r"\left[\begin{aligned} %s \end{aligned}\right." % rows_tex
            emit(d) if not d.extra.get("built") else None
            members = [d] + [i for r in body_rows for i in r]
            for R in lines:
                R.items = [i for i in R.items if i not in members]
            # the system is centred on the math axis of the line it belongs to
            centre = (d.top + d.bot) / 2 + 0.25 * size
            home = min(lines, key=lambda R: abs(R.base - centre))
            if abs(home.base - centre) > 0.5 * size:
                home = L
            b = box(members, tex, base=home.base)
            b.size = size
            b.extra["system"] = True
            home.items.append(b)
    out = [R for R in lines if R.items]
    for R in out:
        R.top, R.bot = min(i.top for i in R.items), max(i.bot for i in R.items)
        R.x0, R.x1 = min(i.x0 for i in R.items), max(i.x1 for i in R.items)
    return out


def row_tex(items: list[Item], size: float) -> str:
    items = sorted(items, key=lambda i: i.x0)
    if any(i.kind == "delim" for i in items):
        return delim_tex(items)
    return line_math(items, size)


def line_math_base(items: list[Item], size: float, base: float) -> str:
    if any(i.kind == "delim" for i in items):
        return delim_tex(items, base, size)
    full = [i for i in items if i.size >= 0.85 * size and i.kind in ("math",)]
    return math_tex(items, base=None if full else base, size=size)


def line_math(items: list[Item], size: float) -> str:
    if any(i.kind == "delim" for i in items):
        return delim_tex(items, None, size)
    return math_tex(items, size=size)


def inline_nodes(items: list[Item], size: float) -> list[dict]:
    """A line's items → inline nodes: text runs (bold/italic marks) and math runs."""
    items = sorted(items, key=lambda i: i.x0)
    nodes: list[dict] = []
    k = 0
    prev_x1 = None

    def add_text(s, marks):
        if nodes and nodes[-1]["t"] == "s" and nodes[-1].get("m", []) == marks:
            nodes[-1]["v"] += s
        else:
            n = {"t": "s", "v": s}
            if marks:
                n["m"] = marks
            nodes.append(n)

    while k < len(items):
        it = items[k]
        gap = (it.x0 - prev_x1) if prev_x1 is not None else 0
        sep = " " if prev_x1 is not None and gap > 0.17 * size else ""
        if it.kind == "text" and abs(it.base - median([i.base for i in items if i.kind == "text"] or [it.base])) < 0.3 * size \
                and it.size >= 0.85 * size:
            run = [it]
            while k + 1 < len(items) and items[k + 1].kind == "text" and items[k + 1].size >= 0.85 * size \
                    and (items[k + 1].bold, items[k + 1].italic) == (it.bold, it.italic):
                k += 1
                run.append(items[k])
            marks = (["b"] if it.bold else []) + (["i"] if it.italic else [])
            add_text(sep + text_of(run, mark=True), marks)
            prev_x1 = run[-1].x1
            k += 1
            continue
        if it.kind == "qed":
            emit(it)
            k += 1
            prev_x1 = it.x1
            continue
        run = [it]
        while k + 1 < len(items) and items[k + 1].kind != "text" and items[k + 1].kind != "qed":
            k += 1
            run.append(items[k])
        tb = [i.base for i in items if i.kind == "text" and i.size >= 0.85 * size]
        lb = median(tb) if tb else None
        if lb is not None and nodes and nodes[-1]["t"] == "s" and not sep and \
                all(i.kind == "math" and i.size < 0.85 * size for i in run) and \
                all(abs(i.base - run[0].base) < 0.5 for i in run) and abs(run[0].base - lb) > 1.0:
            # a unit's exponent/index written after a text word (cm³, H₂O)
            emit(*run)
            nodes.append({"t": "s", "v": "".join(i.val for i in run),
                          "m": ["sup" if run[0].base < lb else "sub"]})
            if not all(re.fullmatch(r"[0-9A-Za-z+\-]", i.val) for i in run):
                raise Unresolved("symbol written as a script of a text word")
            prev_x1 = max(i.x1 for i in run)
            k += 1
            continue
        tex = line_math(run, size) if lb is None else line_math_base(run, size, lb)
        if sep:
            add_text(" ", [])
        nodes.append({"t": "m", "tex": tex})
        prev_x1 = max(i.x1 for i in run)
        k += 1
    if nodes and nodes[0]["t"] == "s":
        nodes[0]["v"] = nodes[0]["v"].lstrip()
        if not nodes[0]["v"]:
            nodes.pop(0)
    return nodes


# ------------------------------------------------------------------------------------------------
# question region and structure
# ------------------------------------------------------------------------------------------------
@dataclass
class Result:
    content: dict | None = None
    reason: str = ""
    pages: list = field(default_factory=list)
    answer_in_solution: str | None = None
    tex: list = field(default_factory=list)
    boxes: list = field(default_factory=list)   # (page, x0, top, x1, bot) of every consumed glyph/rule
    shapes_used: int = 0
    bands: dict = field(default_factory=dict)  # page → (top, bottom) of the region checked
    stats: dict = field(default_factory=dict)


LABEL_RE = re.compile(r"^Câu\s*(\d+)\s*[.:]")
OPT_LABELS = "ABCD"


def body_glyphs(pg: Page):
    """Content glyphs of a page: inside the body frame, upright, not a stamp overlay."""
    out, dropped = [], 0
    for g in pg.glyphs:
        if STAMP_FONT.match(g.font) or not g.upright:
            dropped += 1
            continue
        if g.base < pg.body_top + 2 or g.top > pg.body_bot - 1:
            continue
        if g.ch == " " and not g.is_math:
            continue
        out.append(g)
    return out


def rough_lines(glyphs: list[Glyph]) -> list[list[Glyph]]:
    gs = sorted(glyphs, key=lambda g: g.base)
    lines: list[list[Glyph]] = []
    for g in gs:
        if lines and g.base - lines[-1][-1].base < 1.0:
            lines[-1].append(g)
        else:
            lines.append([g])
    return [sorted(L, key=lambda g: g.x0) for L in lines]


def line_text(L: list[Glyph]) -> str:
    s, prev = "", None
    for g in L:
        if prev is not None and g.x0 - prev.x1 > 0.17 * g.size:
            s += " "
        s += g.ch
        prev = g
    return unicodedata.normalize("NFC", s)


def starts_question(L: list[Glyph]) -> int | None:
    lead = [g for g in L if g.is_text][:8]
    if not lead or not BOLD_FONT.search(lead[0].font):
        return None
    m = LABEL_RE.match(line_text([g for g in lead if BOLD_FONT.search(g.font)]))
    return int(m.group(1)) if m else None


def is_heading(L: list[Glyph]) -> bool:
    return any(HEADING_FONT.match(g.font) for g in L[:3])


def is_dots(L: list[Glyph]) -> bool:
    return all(g.ch in ".… " for g in L) and len(L) > 10


def region(pages: dict[int, Page], first: int, number: int, size_hint=None):
    """Glyphs/rules/shapes of question `number` starting on page `first`: from its 'Câu N.' line up to the
    next question, a section heading or the end of its solution (□)."""
    if first not in pages:
        raise Unresolved(f"page {first} not read")
    pg = pages[first]
    lines = rough_lines(body_glyphs(pg))
    starts = [k for k, L in enumerate(lines) if starts_question(L) == number]
    if len(starts) != 1:
        raise Unresolved(f"'Câu {number}.' found {len(starts)}× on page {first}")
    parts = []  # (page, [glyph lines])
    k0 = starts[0]
    sz = median([g.size for g in lines[k0] if g.is_text]) or 11.0
    # numerators/superscripts of the label line sit just above its baseline
    def anchor(L):  # a line of running text (or worksheet dots), not formula parts hanging around a line
        return is_dots(L) or any(g.is_text and g.size >= 0.85 * sz for g in L)

    def ybase(L):  # radical signs and big delimiters hang from their baseline: their ink lies below it
        hang = all(g.font.upper().startswith(("CMEX", "YHCMEX")) or (g.font.upper().startswith("CMSY")
                                                                      and g.code == 0x70) for g in L)
        return L[0].base + (0.8 * sz if hang else 0.0)

    while k0 > 0 and not anchor(lines[k0 - 1]):
        prev = [L for L in lines[:k0 - 1] if anchor(L)]
        d_label = lines[starts[0]][0].base - ybase(lines[k0 - 1])
        if prev and ybase(lines[k0 - 1]) - prev[-1][0].base <= d_label:
            break
        k0 -= 1
    pno = first
    cur = lines[k0:]
    done = False
    while True:
        keep = []
        stop_y = None
        for j, L in enumerate(cur):
            if (keep or pno != first) and (starts_question(L) is not None or is_heading(L)) and \
                    not (pno == first and j == starts[0] - k0):
                done = True
                stop_y = min(g.top for g in L)
                # the next question's own numerators/superscripts belong to it
                while keep and not anchor(keep[-1]):
                    prev = [K for K in keep[:-1] if anchor(K)]
                    if prev and is_dots(prev[-1]):
                        keep.pop()  # nothing follows a worksheet's answer lines
                        continue
                    if prev and ybase(keep[-1]) - prev[-1][0].base < L[0].base - ybase(keep[-1]):
                        break
                    keep.pop()
                break
            keep.append(L)
            if any(g.font.startswith("MSAM") and g.code == 0x03 for g in L):
                done = True  # □ closes the solution
                rest = cur[j + 1:]
                while rest and rest[0][0].base - L[0].base < 0.9 * sz and not any(g.is_text for g in rest[0]):
                    keep.append(rest.pop(0))
                stop_y = max(g.bot for K in keep[-2:] for g in K) + 1
                break
        if keep and stop_y is not None:
            # trailing formula parts that were popped back to the next question stay outside the band
            stop_y = min(stop_y, min(g.top for K in cur[len(keep):len(keep) + 1] for g in K) if
                         len(cur) > len(keep) else stop_y)
        if keep:
            # an extensible delimiter is one drawing: pull in the rest of a column of pieces the region cuts
            kept = {id(g) for K in keep for g in K}
            pool = lines if pno == first else cur  # the whole page
            pieces = [g for K in pool for g in K if g.font.upper().startswith("CMEX")]
            grown = True
            while grown:
                grown = False
                for g in pieces:
                    if id(g) in kept:
                        continue
                    if any(abs(h.x0 - g.x0) < 1.5 and abs(h.base - g.base) <= 2.0 * h.size and id(h) in kept
                           for h in pieces):
                        keep[0 if g.base < keep[0][0].base else -1].append(g)
                        kept.add(id(g))
                        grown = True
            # limits/scripts of a hanging glyph (∫, big delimiter, √) the region holds belong to it, even when
            # they sit nearer the previous question's last line
            hang = [g for K in keep for g in K if g.font.upper().startswith(("CMEX", "YHCMEX"))
                    or (g.font.upper().startswith("CMSY") and g.code == 0x70)]
            for K in pool:
                for g in K:
                    if id(g) in kept or g.size >= 0.85 * sz or g.is_text:
                        continue
                    if any(h.x0 - 0.3 * sz <= g.x0 <= h.x1 + 1.0 * sz and h.base - 1.2 * sz <= g.base <= h.base + 2.6 * sz
                           for h in hang):
                        keep[0 if g.base < keep[0][0].base else -1].append(g)
                        kept.add(id(g))
        parts.append((pno, keep, stop_y))
        if done:
            break
        pno += 1
        if pno not in pages or pno > first + 2:
            raise Unresolved("question runs past the pages read")
        cur = rough_lines(body_glyphs(pages[pno]))
    return parts


def layout_question(pages: dict[int, Page], first: int, number: int) -> Result:
    res = Result()
    _LEDGER.clear()
    _CTX.update(script=0, size=0.0)
    glyph_items: list[Item] = []
    try:
        parts = region(pages, first, number)
        res.pages = [p for p, _, _ in parts]
        all_items: list[tuple[int, Line]] = []
        size = None
        for n_part, (pno, glines, stop_y) in enumerate(parts):
            pg = pages[pno]
            glyphs = [g for L in glines for g in L if not is_dots(L)]
            dots = [g for L in glines for g in L if is_dots(L)]
            for g in dots:
                g.used = True
                res.boxes.append((pno, g.x0, g.top, g.x1, g.bot))
            if not glyphs:
                continue
            # the band checked for strokes/drawings/ink: from the question's first line (or the top of a
            # continuation page) down to the next question (or the end of the page body)
            top = min(g.top for g in glyphs) - 2 if n_part == 0 else pg.body_top + 1
            bot = (stop_y - 1) if stop_y is not None else pg.body_bot - 1
            bot = max(bot, max(g.bot for g in glyphs) + 2)
            res.bands[pno] = (top, bot)
            if size is None:
                txt = [g.size for g in glyphs if g.is_text]
                size = median(txt) if txt else median([g.size for g in glyphs])
                _CTX["size"] = size
            items = [glyph_item(g) for g in glyphs]
            glyph_items.extend(items)
            for it in items:
                if it.kind == "delim" and it.font.upper().startswith(("CMEX", "YHCMEX")):
                    # a single big delimiter glyph: its real height comes from the page image
                    span = pg.ink_span(it.x0, it.x1, it.base, it.size)
                    if span is None:
                        raise Unresolved(f"big delimiter {it.val} has no ink on the page")
                    it.top, it.bot = span
            for g in glyphs:
                g.used = True
                res.boxes.append((pno, g.x0, g.top, g.x1, g.bot))
            rules = [r for r in pg.rules if not r.used and top <= r.y <= bot and r.x1 > 0 and r.x0 < pg.width]
            shapes = [s for s in pg.shapes if overlap(s.top, s.bot, top, bot) > 0]
            items = build_structures(items, rules, size)
            loose = [r for r in rules if not r.used]
            if loose:
                raise Unresolved(f"{len(loose)} unexplained stroke(s) on page {pno} (table/figure/underline) at "
                                 + ", ".join(f"{r.x0:.0f}-{r.x1:.0f}@{r.y:.0f}" for r in loose[:3]))
            for r in rules:
                res.boxes.append((pno, r.x0, r.y - 1, r.x1, r.y + 1))
            if shapes:
                circ = answer_circles(shapes, items, size)
                if len(circ) != len(shapes):
                    raise Unresolved(f"{len(shapes) - len(circ)} drawing(s)/image(s) in the question on page {pno}")
                for s in circ:
                    s.used = True
                    res.boxes.append((pno, s.x0, s.top, s.x1, s.bot))
                res.shapes_used += len(circ)
            lines = systems(make_lines(items, size), size)
            # no symbol of one line may sit inside a structure placed on another line (a misread layout)
            for L1 in lines:
                for a_ in L1.items:
                    if a_.kind != "box" or a_.bot - a_.top < 1.2 * size:
                        continue
                    for L2 in lines:
                        if L2 is L1:
                            continue
                        for b_ in L2.items:
                            if a_.x0 + 1 < b_.xc < a_.x1 - 1 and a_.top + 1 < (b_.top + b_.bot) / 2 < a_.bot - 1:
                                raise Unresolved("a symbol lies inside a formula of another line (layout misread)")
            for L in lines:
                all_items.append((pno, L))
        res.content, res.answer_in_solution = structure(all_items, number, size)
        bad = [i for i in glyph_items if _LEDGER.get(id(i), 0) != 1]
        if bad:
            res.content = None
            n0 = sum(1 for i in bad if _LEDGER.get(id(i), 0) == 0)
            raise Unresolved(f"{n0} glyph(s) lost, {len(bad) - n0} duplicated while rebuilding "
                             f"(e.g. {''.join(i.val or '?' for i in bad[:8])!r})")
        res.stats["size"] = size
    except Unresolved as ex:
        res.content, res.reason = None, str(ex)
    except RecursionError:
        res.content, res.reason = None, "formula nesting too deep"
    return res


def answer_circles(shapes, items, size):
    """The books circle the answer letter in 'Chọn đáp án X': a small closed curve around one glyph."""
    out = []
    for s in shapes:
        if s.kind != "LTCurve" or not (0.8 * size <= s.x1 - s.x0 <= 2.2 * size and 0.8 * size <= s.bot - s.top <= 2.2 * size):
            continue
        inside = [i for i in items if i.kind == "text" and s.x0 <= i.xc <= s.x1 and s.top <= (i.top + i.bot) / 2 <= s.bot]
        if len(inside) == 1 and inside[0].val in OPT_LABELS:
            out.append(s)
    return out


def structure(lines: list[tuple[int, Line]], number: int, size: float):
    """Lines of one question → stem / options A–D / solution blocks."""
    seq = []  # (page, line, nodes-with-positions)
    stem, sol = [], []
    options: dict[str, list] = {}
    state = "stem"
    cur_opt = None
    answer_said = None
    first = True
    body_x0 = min(L.x0 for _, L in lines)
    body_x1 = max(L.x1 for _, L in lines)
    para: list | None = None

    def flush(target):
        nonlocal para
        if para:
            target.append({"t": "p", "c": para})
        para = None

    pending_opts = []
    for pno, L in lines:
        items = sorted(L.items, key=lambda i: i.x0)
        if first:
            # drop the 'Câu N.' label
            lab = [i for i in items if i.kind == "text" and i.bold]
            lab_txt, cut = "", 0
            for i in items:
                if i.kind == "text" and i.bold:
                    lab_txt = text_of(items[:cut + 1] if False else [x for x in items[:items.index(i) + 1]])
                    if re.fullmatch(r"Câu\s*\d+\s*[.:]", lab_txt):
                        cut = items.index(i) + 1
                        break
                else:
                    break
            if not cut:
                raise Unresolved("question label not at the start of its line")
            emit(*items[:cut])
            items = items[cut:]
            first = False
        # option labels: bold A/B/C/D followed by '.' at the left of a cell
        labels = []
        for j, i in enumerate(items):
            if i.kind == "text" and i.bold and i.val in OPT_LABELS and j + 1 < len(items) and \
                    items[j + 1].kind == "text" and items[j + 1].val == "." and (j == 0 or i.x0 - items[j - 1].x1 > 0.8 * size):
                labels.append(j)
        txt_all = text_of([i for i in items if i.kind == "text"])
        if state in ("stem", "options") and re.match(r"^\s*Lời giải\s*[.:]?\s*$", txt_all) and \
                all(i.kind == "text" and i.bold for i in items):
            state = "solution"
            emit(*items)
            continue
        if state == "solution":
            m = re.search(r"Chọn đáp án\s*([A-D])\b", txt_all)
            if m:
                answer_said = m.group(1)
        if labels and state != "solution":
            if state == "stem":
                flush(stem)
                pre = items[:labels[0]]
                if pre:
                    raise Unresolved("text before the first option label on its line")
            state = "options"
            bounds = labels + [len(items)]
            for a, b in zip(bounds, bounds[1:]):
                lab = items[a].val
                if lab in options:
                    raise Unresolved(f"option {lab} twice")
                emit(items[a], items[a + 1])
                options[lab] = [{"t": "p", "c": inline_nodes(items[a + 2:b], size)}]
                cur_opt = lab
            continue
        if not items:
            continue
        tag = None
        tl = [i for i in items if i.kind == "text"]
        if tl and state != "options":
            # an equation number at the right margin: "(1.1)" after a displayed formula
            tail = []
            for i in reversed(items):
                if i.kind == "text":
                    tail.insert(0, i)
                else:
                    break
            rest = items[:len(items) - len(tail)]
            if tail and rest and re.fullmatch(r"\(\d+(?:\.\d+)*\)", text_of(tail)) and \
                    tail[0].x0 - max(i.x1 for i in rest) > 4 * size and all(i.kind != "text" for i in rest):
                tag = text_of(tail, mark=True)
                items = rest
        nodes = inline_nodes(items, size)
        if tag and len(nodes) == 1 and nodes[0]["t"] == "m":
            nodes[0]["tex"] += r" \qquad " + tag
        elif tag:
            raise Unresolved("equation number after a line that is not a formula")
        if not nodes:
            continue
        display = len(nodes) == 1 and nodes[0]["t"] == "m" and (L.x0 > body_x0 + 3 * size or tag)
        if state == "options":
            # continuation of the last option (a long option wraps onto the next line)
            if cur_opt is None or display:
                raise Unresolved("unexpected line among the options")
            options[cur_opt][-1]["c"].append({"t": "s", "v": " "})
            options[cur_opt][-1]["c"].extend(nodes)
            continue
        target = stem if state == "stem" else sol
        if state == "solution" and re.match(r"\s*Chọn đáp án", txt_all):
            flush(target)  # the conclusion is its own paragraph
        if display:
            flush(target)
            target.append({"t": "p", "c": nodes, "display": True})
            continue
        if para is None:
            para = nodes
        else:
            para.append({"t": "s", "v": " "})
            para.extend(nodes)
        # a line that stops well before the right margin ends its paragraph
        if L.x1 < body_x1 - 4 * size:
            flush(target)
    flush(stem if state == "stem" else sol)
    if sorted(options) != list(OPT_LABELS):
        raise Unresolved(f"options found: {''.join(sorted(options)) or 'none'}")
    for o in options.values():
        if not o[0]["c"]:
            raise Unresolved("empty option")
    for blocks in [stem, sol] + list(options.values()):
        for b in blocks:
            b["c"] = tidy(b["c"])
    if not stem:
        raise Unresolved("empty stem")
    content = {"stem": stem, "options": [{"label": k, "content": options[k]} for k in OPT_LABELS],
               "solution": sol or None}
    return content, answer_said


def tidy(nodes: list[dict]) -> list[dict]:
    out = []
    for n in nodes:
        if n["t"] == "s":
            n["v"] = re.sub(r"\s+", " ", n["v"])
            if out and out[-1]["t"] == "s" and out[-1].get("m") == n.get("m"):
                out[-1]["v"] += n["v"]
                continue
        out.append(n)
    while out and out[-1]["t"] == "s" and not out[-1]["v"].strip():
        out.pop()
    if out and out[-1]["t"] == "s":
        out[-1]["v"] = out[-1]["v"].rstrip()
    if out and out[0]["t"] == "s":
        out[0]["v"] = out[0]["v"].lstrip()
    # punctuation glued after a formula: no space before it
    for a, b in zip(out, out[1:]):
        if a["t"] == "m" and b["t"] == "s" and re.match(r"\s+[.,;:)]", b["v"]):
            b["v"] = b["v"].lstrip()
    return [n for n in out if not (n["t"] == "s" and n["v"] == "")]


def all_tex(content: dict) -> list[str]:
    out = []

    def walk(blocks):
        for b in blocks or []:
            for n in b.get("c") or []:
                if n.get("t") == "m":
                    out.append(n["tex"])
    walk(content.get("stem"))
    walk(content.get("solution"))
    for o in content.get("options") or []:
        walk(o["content"])
    return out


def plain(content_part) -> str:
    """Letters/digits of a content part (formulas included as their visible characters) for comparisons."""
    out = []
    for b in content_part or []:
        for n in b.get("c") or []:
            if n.get("t") == "s":
                out.append(n.get("v", ""))
            elif n.get("t") == "m":
                out.append(re.sub(r"\\(?:[A-Za-z]+)", " ", n.get("tex", "")))
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFC", "".join(out))).lower()


# ------------------------------------------------------------------------------------------------
# page-image cross-check
# ------------------------------------------------------------------------------------------------
HANGING = re.compile(r"^(?:CMEX|Yhcmex|CMSY)", re.I)


def ink_check(pdf_path, res: Result, pages: dict[int, Page], dpi: int = 100, limit: int = 12) -> str | None:
    """Render the question's region of each page and look for ink that no consumed glyph or stroke explains
    (an image, a vector drawing, a glyph the text layer does not carry). None = clean, else the reason."""
    import subprocess
    import tempfile
    from PIL import Image

    s = dpi / 72.0
    for pno in res.pages:
        bx = [b for b in res.boxes if b[0] == pno]
        if not bx:
            continue
        pg = pages[pno]
        top, bot = res.bands.get(pno, (min(b[2] for b in bx) - 3, max(b[4] for b in bx) + 3))
        top, bot = max(pg.body_top + 1, top), min(pg.body_bot - 1, bot)
        with tempfile.TemporaryDirectory() as tmp:
            out = f"{tmp}/p"
            r = subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", str(dpi), "-gray", "-png",
                                "-singlefile", "-x", "0", "-y", str(int(top * s)), "-W", str(int(pg.width * s)),
                                "-H", str(max(1, int((bot - top) * s))), str(pdf_path), out],
                               capture_output=True, timeout=120)
            if r.returncode != 0:
                return "page could not be rendered"
            im = Image.open(out + ".png").convert("L")
        w, h = im.size
        px = im.load()
        mask = bytearray(w * h)

        def cover(x0, y0, x1, y1, pad=1.5):
            a0, a1 = max(0, int((x0 - pad) * s)), min(w, int((x1 + pad) * s) + 1)
            b0, b1 = max(0, int((y0 - top - pad) * s)), min(h, int((y1 - top + pad) * s) + 1)
            for yy in range(b0, b1):
                mask[yy * w + a0:yy * w + a1] = b"\x01" * (a1 - a0)

        for b in bx:
            cover(b[1], b[2], b[3], b[4])
        # glyphs that hang from their baseline (big delimiters, radicals, big operators): ink lies below
        for g in pg.glyphs:
            if g.used and HANGING.match(g.font) and g.top < bot and g.base > top - 3 * g.size:
                cover(g.x0, g.base - 0.2 * g.size, g.x1, g.base + 2.8 * g.size)
        # outside the body (header/footer) and stamps are not part of the question
        for g in pg.glyphs:
            if STAMP_FONT.match(g.font) or not g.upright:
                cover(g.x0, g.top, g.x1, g.bot, pad=3)
        for sh in pg.shapes:
            if sh.used:
                cover(sh.x0, sh.top, sh.x1, sh.bot)
        # glyphs of neighbouring questions whose boxes reach into the band are not this question's ink
        for g in pg.glyphs:
            if not g.used and (g.bot < top + 4 or g.top > bot - 4):
                cover(g.x0, g.top, g.x1, g.bot)
        stray = 0
        for yy in range(h):
            row = yy * w
            for xx in range(w):
                if not mask[row + xx] and px[xx, yy] < 110:
                    stray += 1
        if stray > limit:
            return f"{stray} px of ink on page {pno} not explained by the text layer (figure/drawing/image)"
    return None


def text_words(content: dict, parts=("stem", "options")) -> list[str]:
    """Words of the running text (formulas excluded), for comparing two versions of a question."""
    out = []

    def walk(blocks):
        for b in blocks or []:
            for n in b.get("c") or []:
                if n.get("t") == "s":
                    out.extend(w.lower() for w in re.findall(r"[^\W\d_]{2,}", unicodedata.normalize("NFC", n["v"])))
                elif n.get("t") == "p" or n.get("c"):
                    walk([n])
    for k in parts:
        if k == "options":
            for o in content.get("options") or []:
                walk(o.get("content"))
        else:
            walk(content.get(k))
    return out


def same_question(stored: dict, rebuilt: dict) -> float:
    """Share of the rebuilt stem/options words that the stored (flattened) version also has."""
    a = set(text_words(stored, ("stem", "options", "solution", "explanation")))
    b = text_words(rebuilt)
    if not b:
        return 1.0
    return sum(1 for w in b if w in a) / len(b)
