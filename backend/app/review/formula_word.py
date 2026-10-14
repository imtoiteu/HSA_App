"""Rebuild the flattened formulas of questions taken from the text layer of Word/MathType PDFs.

Word documents print formulas as MathType (or Equation Editor) objects. In the PDF every character of an
equation is a positioned glyph, and the structure is geometry:

* variables are italic Times New Roman (or Euclid/Palatino), digits and function names upright;
* operators, relations, Greek letters and the pieces of big delimiters come from the Symbol font (Symbol,
  SymbolMT, Euclid Symbol), whose private-use codes U+F0xx carry the Adobe Symbol encoding in their low byte;
* ℝ ℤ ℕ ℓ, vector arrows and wide hats come from MT Extra (Euclid Extra), placed on a baseline of their own above
  the letters they cover;
* scripts are smaller glyphs on a raised/lowered baseline, fraction bars are horizontal strokes, radicals are a
  drawn sign (outline + strokes, or a √ glyph) with an overline.

This module rebuilds a question's region from that geometry. It shares nothing TeX-specific with
`formula_pdf` (the LaTeX engine): only the result format and the format-independent checks
(`same_question`, `all_tex`). Anything it cannot explain is refused with a reason (`Unresolved`).
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field

from .formula_pdf import Result, Unresolved, all_tex, same_question  # noqa: F401  (re-exported for the batch)

# ------------------------------------------------------------------------------------------------
# fonts
# ------------------------------------------------------------------------------------------------
SYMBOL_FAMILIES = {"SYMBOL", "SYMBOLMT", "EUCLIDSYMBOL", "SYMBOLPS"}
EXTRA_FAMILIES = {"MTEXTRA", "MT", "EUCLIDEXTRA"}
TEXT_FAMILIES = {"TIMESNEWROMAN", "TIMESNEWROMANPSMT", "TIMESNEWROMANPS", "TIMES", "EUCLID", "PALATINOLINOTYPE",
                 "CAMBRIA", "CALIBRI", "ARIAL", "ARIALMT", "CALISTOMT", "GEORGIA", "TAHOMA", "VERDANA",
                 "MICROSOFTSANSSERIF", "HELVETICA", "BOOKANTIQUA", "SEGOEUI", "CALIBRILIGHT", "CAMBRIAMATH",
                 "ARIALUNICODEMS"}
LEGACY_FAMILIES = re.compile(r"^(?:VNI|\.?VN|VN)", re.I)   # Vietnamese 8-bit encodings: text is not Unicode


def family(font: str) -> str:
    f = re.sub(r"^[A-Z]{6}\+", "", font)
    f = re.split(r"[,]", f)[0]
    f = re.sub(r"[-\s](?:Bold|Italic|BoldItalic|Regular|Roman|Oblique|It|BoldMT|ItalicMT|BoldItalicMT)\b.*$", "",
               f, flags=re.I)
    return re.sub(r"[\s\-_.]", "", f).upper()


def is_italic(font: str) -> bool:
    return bool(re.search(r"Italic|Oblique|(?:-|,)It\b", font, re.I))


def is_bold(font: str) -> bool:
    return bool(re.search(r"Bold|(?:-|,)Bd\b|Black|Heavy", font, re.I))


# Adobe Symbol encoding (low byte of the PUA code) → Unicode
SYMBOL_ENC = {
    0x20: " ", 0x21: "!", 0x22: "∀", 0x23: "#", 0x24: "∃", 0x25: "%", 0x26: "&", 0x27: "∋", 0x28: "(", 0x29: ")",
    0x2A: "∗", 0x2B: "+", 0x2C: ",", 0x2D: "−", 0x2E: ".", 0x2F: "/", 0x3A: ":", 0x3B: ";", 0x3C: "<", 0x3D: "=",
    0x3E: ">", 0x3F: "?", 0x40: "≅", 0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ", 0x47: "Γ",
    0x48: "Η", 0x49: "Ι", 0x4A: "ϑ", 0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ",
    0x52: "Ρ", 0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5B: "[",
    0x5C: "∴", 0x5D: "]", 0x5E: "⊥", 0x5F: "_", 0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ",
    0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π",
    0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ",
    0x7B: "{", 0x7C: "|", 0x7D: "}", 0x7E: "∼", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ",
    0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°",
    0xB1: "±", 0xB2: "″", 0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡",
    0xBB: "≈", 0xBC: "…", 0xBF: "↵", 0xC0: "ℵ", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃",
    0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇", 0xD5: "∏", 0xD6: "√",
    0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊",
    0xE1: "〈", 0xE5: "∑", 0xF1: "〉", 0xF2: "∫",
}
for _c in range(0x30, 0x3A):
    SYMBOL_ENC[_c] = chr(_c)
# pieces of big delimiters (Symbol 0xE6–0xFE, or their Unicode forms): (delimiter, part)
PIECES = {
    0xE6: ("(", "top"), 0xE7: ("(", "ext"), 0xE8: ("(", "bot"), 0xE9: ("[", "top"), 0xEA: ("[", "ext"),
    0xEB: ("[", "bot"), 0xEC: ("{", "top"), 0xED: ("{", "mid"), 0xEE: ("{", "bot"), 0xEF: ("{", "ext"),
    0xF3: ("∫", "top"), 0xF4: ("∫", "ext"), 0xF5: ("∫", "bot"),
    0xF6: (")", "top"), 0xF7: (")", "ext"), 0xF8: (")", "bot"), 0xF9: ("]", "top"), 0xFA: ("]", "ext"),
    0xFB: ("]", "bot"), 0xFC: ("}", "top"), 0xFD: ("}", "mid"), 0xFE: ("}", "bot"), 0xBD: ("|", "ext"),
}
UNI_PIECES = {"⎛": 0xE6, "⎜": 0xE7, "⎝": 0xE8, "⎡": 0xE9, "⎢": 0xEA, "⎣": 0xEB, "⎧": 0xEC, "⎨": 0xED, "⎩": 0xEE,
              "⎪": 0xEF, "⌠": 0xF3, "⎮": 0xF4, "⌡": 0xF5, "⎞": 0xF6, "⎟": 0xF7, "⎠": 0xF8, "⎤": 0xF9, "⎥": 0xFA,
              "⎦": 0xFB, "⎫": 0xFC, "⎬": 0xFD, "⎭": 0xFE}
# MT Extra / Euclid Extra (low byte of the PUA code), identified on the bank's documents
EXTRA_ENC = {0xA1: ("m", r"\mathbb{R}"), 0xA2: ("m", r"\mathbb{Z}"), 0xA5: ("m", r"\mathbb{N}"),
             0x6C: ("m", r"\ell"), 0x72: ("acc", "arrow"), 0x75: ("acc", "arrow"), 0xB7: ("acc", "hat")}

# Unicode → TeX for math symbols (anything else non-alphanumeric in a math font is refused)
UNI_TEX = {
    "−": "-", "-": "-", "+": "+", "=": "=", "<": "<", ">": ">", "(": "(", ")": ")", "[": "[", "]": "]",
    "{": r"\{", "}": r"\}", "|": "|", "/": "/", ",": ",", ";": ";", ":": ":", ".": ".", "!": "!", "'": "'",
    "≤": r"\le", "≥": r"\ge", "≠": r"\ne", "≈": r"\approx", "≡": r"\equiv", "≅": r"\cong", "∼": r"\sim",
    "∝": r"\propto", "±": r"\pm", "×": r"\times", "÷": r"\div", "⋅": r"\cdot", "·": r"\cdot", "•": r"\bullet",
    "∗": "*", "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "⊂": r"\subset", "⊃": r"\supset", "⊆": r"\subseteq",
    "⊇": r"\supseteq", "⊄": r"\not\subset", "∩": r"\cap", "∪": r"\cup", "∅": r"\varnothing", "∀": r"\forall",
    "∃": r"\exists", "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee", "⇔": r"\Leftrightarrow", "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow", "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow", "↑": r"\uparrow",
    "↓": r"\downarrow", "⇑": r"\Uparrow", "⇓": r"\Downarrow", "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla",
    "∠": r"\angle", "⊥": r"\perp", "∥": r"\parallel", "°": r"^{\circ}", "′": "'", "″": "''", "…": r"\ldots",
    "⊕": r"\oplus", "⊗": r"\otimes", "∴": r"\therefore", "∫": r"\int", "∑": r"\sum", "∏": r"\prod",
    "〈": r"\langle", "〉": r"\rangle", "ℵ": r"\aleph", "ƒ": "f", "ϑ": r"\vartheta", "ϕ": r"\varphi",
    "ς": r"\varsigma", "ϖ": r"\varpi", "ℓ": r"\ell", "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "∆": r"\Delta", "Ω": r"\Omega", "µ": r"\mu", "◊": r"\Diamond",
}
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "varepsilon", "ζ": "zeta", "η": "eta",
         "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "o",
         "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "varphi", "χ": "chi", "ψ": "psi",
         "ω": "omega", "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi",
         "Σ": "Sigma", "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega", "ϒ": "Upsilon"}
for _k, _v in GREEK.items():
    UNI_TEX[_k] = "o" if _v == "o" else "\\" + _v
for _k, _v in {"Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
               "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X"}.items():
    UNI_TEX[_k] = r"\mathrm{%s}" % _v
RELS = {"=", r"\le", r"\ge", r"\ne", "<", ">", r"\approx", r"\equiv", r"\in", r"\notin", r"\subset", r"\Rightarrow",
        r"\Leftrightarrow", r"\to"}
FUNCS = {"sin", "cos", "tan", "cot", "log", "ln", "lg", "lim", "max", "min", "exp", "sup", "inf", "det", "arg",
         "deg", "gcd", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan"}
OTHER_FUNCS = {"tg": r"\operatorname{tg}", "cotg": r"\operatorname{cotg}", "arccot": r"\operatorname{arccot}",
               "sgn": r"\operatorname{sgn}"}
OPEN, CLOSE = "([{", ")]}"
UNITS = {"cm", "mm", "km", "dm", "nm", "pm", "kg", "mg", "Hz", "kHz", "MHz", "GHz", "rad", "mol", "eV", "MeV", "keV",
         "GeV", "kJ", "kW", "kV", "mV", "mA", "kPa", "atm", "lit", "Wb", "ms", "min", "cal", "kcal"}


# ------------------------------------------------------------------------------------------------
# reading
# ------------------------------------------------------------------------------------------------
@dataclass
class Glyph:
    ch: str
    font: str
    size: float
    x0: float
    x1: float
    top: float
    bot: float
    base: float
    upright: bool = True
    used: bool = False

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2


@dataclass
class Stroke:
    kind: str          # hline | line | curve | rect | image
    x0: float
    x1: float
    top: float
    bot: float
    fill: bool = False
    used: bool = False
    role: str = ""
    dark: bool = True
    rising: bool | None = None   # a diagonal line: True when it goes up to the right
    obj: object = None           # the pdfminer image (images only)

    @property
    def y(self):
        return (self.top + self.bot) / 2

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.bot - self.top


@dataclass
class Page:
    number: int
    width: float
    height: float
    glyphs: list
    strokes: list
    pdf: str = ""
    body_top: float = 0.0


def read_pages(pdf_path, pages: set[int]) -> dict[int, Page]:
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LTChar, LTCurve, LTFigure, LTImage, LTLine, LTRect
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage

    out: dict[int, Page] = {}
    want = sorted(p - 1 for p in pages if p >= 1)
    with open(pdf_path, "rb") as fp:
        rm = PDFResourceManager(caching=True)
        dev = PDFPageAggregator(rm, laparams=None)
        it = PDFPageInterpreter(rm, dev)
        for i, page in enumerate(PDFPage.get_pages(fp, pagenos=set(want))):
            it.process_page(page)
            lt = dev.get_result()
            H = lt.height
            glyphs, strokes = [], []

            def walk(objs):
                for o in objs:
                    if isinstance(o, LTChar):
                        a, b, c, d, e, f = o.matrix
                        # rotated text (diagonal watermarks) is not the question's; a sheared glyph (MathType's
                        # slanted Greek letters) is
                        upright = abs(b) < 0.02 * abs(a) and a > 0 and d > 0
                        glyphs.append(Glyph(o.get_text(), re.sub(r"^[A-Z]{6}\+", "", o.fontname), o.size, o.x0, o.x1,
                                            H - o.y1, H - o.y0, H - f, upright))
                    elif isinstance(o, LTImage):
                        strokes.append(Stroke("image", o.x0, o.x1, H - o.y1, H - o.y0, obj=o))
                    elif isinstance(o, LTFigure):
                        walk(o)
                    elif isinstance(o, (LTLine, LTRect, LTCurve)):
                        w, h = o.x1 - o.x0, o.y1 - o.y0
                        kind = "curve" if type(o) is LTCurve else ("rect" if isinstance(o, LTRect) else "line")
                        if h <= 1.3 and w > 1.0 and (kind != "curve" or len(o.pts) <= 5):
                            kind = "hline"
                        st = Stroke(kind, o.x0, o.x1, H - o.y1, H - o.y0, fill=bool(getattr(o, "fill", False)))
                        cols = []
                        if getattr(o, "stroke", False) or kind in ("line", "hline"):
                            cols.append(getattr(o, "stroking_color", None))
                        if getattr(o, "fill", False):
                            cols.append(getattr(o, "non_stroking_color", None))
                        st.dark = any(_dark(c) for c in cols) if cols else False
                        if kind == "line" and len(getattr(o, "pts", [])) == 2:
                            (ax, ay), (bx, by) = o.pts
                            st.rising = (bx - ax) * (by - ay) > 0
                        strokes.append(st)
            walk(lt)
            pno = want[i] + 1
            out[pno] = Page(pno, lt.width, H, glyphs, strokes, pdf=str(pdf_path))
    return out


def image_is_dark(st: "Stroke") -> bool:
    """Does an embedded image carry dark ink (a figure, an equation or accent drawn as a picture)? A light
    image (watermark, background) does not. An image that cannot be decoded counts as dark."""
    import tempfile

    from PIL import Image
    try:
        from pdfminer.image import ImageWriter
        with tempfile.TemporaryDirectory() as tmp:
            fn = ImageWriter(tmp).export_image(st.obj)
            im = Image.open(f"{tmp}/{fn}").convert("L")
            im.thumbnail((400, 400))
            return any(p < 128 for p in im.getdata())
    except Exception:  # noqa: BLE001
        return True


def _dark(c) -> bool:
    """Is a PDF colour (gray, rgb or cmyk tuple) dark enough to be ink rather than a background?"""
    if c is None:
        return True   # default colour: black
    if isinstance(c, (int, float)):
        c = (c,)
    try:
        c = tuple(float(x) for x in c)
    except (TypeError, ValueError):
        return True   # pattern/unknown colour space: treat as ink
    if len(c) == 1:
        lum = c[0]
    elif len(c) == 3:
        lum = 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]
    elif len(c) == 4:
        lum = (1 - min(1.0, c[0] + c[3])) * 0.3 + (1 - min(1.0, c[1] + c[3])) * 0.59 + (1 - min(1.0, c[2] + c[3])) * 0.11
    else:
        return True
    return lum < 0.55


# ------------------------------------------------------------------------------------------------
# atoms: glyphs and rebuilt boxes
# ------------------------------------------------------------------------------------------------
@dataclass
class Atom:
    x0: float
    x1: float
    top: float
    bot: float
    base: float
    size: float
    kind: str           # t (text char) | v (italic latin letter) | n (digit/punct) | m (math symbol) | sp | delim
                        # | piece | acc | sqrt | box
    val: str = ""
    italic: bool = False
    bold: bool = False
    glyphs: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2

    @property
    def mathy(self):
        return self.kind in ("v", "m", "delim", "box", "sqrt", "piece", "acc")


def symbol_char(ch: str) -> tuple[str, int | None]:
    """A Symbol-font glyph's meaning: (unicode, symbol code or None)."""
    if len(ch) == 1:
        o = ord(ch)
        if 0xF020 <= o <= 0xF0FF:
            o -= 0xF000
            return SYMBOL_ENC.get(o, ""), o
        if o < 0x100 and not ("0" <= ch <= "9") and ch not in "+=()[],.;:!/<>|-":
            return SYMBOL_ENC.get(o, ""), o   # an 8-bit code given as Latin-1 (Euclid Symbol): the Symbol glyph
        if ch in UNI_PIECES:
            return ch, UNI_PIECES[ch]
    return ch, None


def is_piece(g: Glyph) -> bool:
    if family(g.font) not in SYMBOL_FAMILIES:
        return False
    _, code = symbol_char(unicodedata.normalize("NFC", g.ch))
    return code is not None and code in PIECES


def atom_of(g: Glyph) -> Atom:
    fam = family(g.font)
    a = Atom(g.x0, g.x1, g.top, g.bot, g.base, g.size, "m", glyphs=[g],
             italic=is_italic(g.font), bold=is_bold(g.font))
    ch = unicodedata.normalize("NFC", g.ch)
    if LEGACY_FAMILIES.match(fam):
        raise Unresolved(f"text in a legacy Vietnamese font ({g.font})")
    if fam in SYMBOL_FAMILIES:
        u, code = symbol_char(ch)
        if code is not None and code in PIECES:
            a.kind, a.val = "piece", PIECES[code][0]
            a.extra["part"] = PIECES[code][1]
            return a
        if u == " ":
            a.kind = "sp"
            return a
        if u == "√":
            a.kind, a.val = "sqrt", "√"
            return a
        if u in ("(", ")", "[", "]", "{", "}", "|"):
            a.kind, a.val = "delim", u
            return a
        if len(u) == 1 and "0" <= u <= "9":
            a.kind, a.val = "n", u
            return a
        tex = UNI_TEX.get(u)
        if tex is None:
            raise Unresolved(f"unknown symbol {ch!r} in {g.font}")
        a.val = tex
        return a
    if fam in EXTRA_FAMILIES:
        if len(ch) == 1 and 0xF020 <= ord(ch) <= 0xF0FF and ord(ch) - 0xF000 in EXTRA_ENC:
            a.kind, a.val = EXTRA_ENC[ord(ch) - 0xF000]
            return a
        if ch in UNI_TEX and ch.isalpha():
            a.val = UNI_TEX[ch]
            return a
        raise Unresolved(f"unknown MT Extra glyph {ch!r}")
    if fam in TEXT_FAMILIES or fam.startswith("TIMES"):
        if ch.startswith("(cid:") or not ch:
            raise Unresolved(f"glyph without unicode in {g.font}")
        if ch.isspace():
            a.kind = "sp"
            return a
        if len(ch) > 1:  # ligatures
            ch = unicodedata.normalize("NFKC", ch)
        if len(ch) == 1 and 0x1D400 <= ord(ch) <= 0x1D7FF:  # mathematical alphanumerics (Cambria Math)
            ch, a.italic = unicodedata.normalize("NFKC", ch), True
        if all(c.isalpha() for c in ch):
            a.kind = "v" if a.italic and ch.isascii() else "t"
            a.val = ch
            if ch in GREEK or ch in "∆Ω":
                a.kind, a.val = "m", UNI_TEX[ch]
            return a
        if ch.isdigit() or ch in ".,;:!?'\"%&#()[]{}/+-=<>|_*@":
            a.kind, a.val = ("delim" if ch in "()[]{}|" else "n"), ch
            return a
        tex = UNI_TEX.get(ch)
        if tex is not None:
            a.val = tex
            return a
        a.kind, a.val = "t", ch   # other text punctuation (quotes, dashes): plain text
        return a
    if fam == "WINGDINGS":
        raise Unresolved("Wingdings glyph in the question")
    raise Unresolved(f"glyph in unsupported font {g.font}")


def box(members: list[Atom], tex: str, base: float | None = None, x0=None, x1=None, top=None, bot=None,
        size=None) -> Atom:
    gl = [g for m in members for g in m.glyphs]
    b = Atom(min(m.x0 for m in members) if x0 is None else x0, max(m.x1 for m in members) if x1 is None else x1,
             min(m.top for m in members) if top is None else top, max(m.bot for m in members) if bot is None else bot,
             base if base is not None else statistics.median(m.base for m in members),
             size if size is not None else max(m.size for m in members), "box", tex, glyphs=gl)
    return b


# ------------------------------------------------------------------------------------------------
# TeX of a horizontal list (scripts by size and baseline)
# ------------------------------------------------------------------------------------------------
def grp(t: str) -> str:
    return t if len(t) == 1 and t.isalnum() else "{" + t + "}"


def tok_tex(a: Atom) -> str:
    if a.val in ("%", "#", "&", "$"):
        return "\\" + a.val   # "87,5%m₀": a bare % would start a TeX comment
    if a.kind in ("v", "t", "n"):
        return a.val
    if a.kind == "delim":
        return {"{": r"\{", "}": r"\}"}.get(a.val, a.val)
    return a.val


def join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out and re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z]", p):
            out += " "
        out += p
    return out


TEXT_WORDS = {"khi", "hay"}


def word_tex(run: list[Atom]) -> list[str]:
    """Upright letter runs: function names → \\ln…, others → \\mathrm{…}; italic letters stay as variables."""
    out, i = [], 0
    while i < len(run):
        a = run[i]
        if a.kind == "t" and a.val.isascii():
            j = i
            while j < len(run) and run[j].kind == "t" and run[j].val.isascii() and \
                    (j == i or run[j].x0 - run[j - 1].x1 < 0.25 * a.size):
                j += 1
            w = "".join(x.val for x in run[i:j])
            chain = split_funcs(w)
            if chain and len(chain) > 1:   # "lnsin": two function names written without a space
                out.extend(OTHER_FUNCS.get(f, "\\" + f) for f in chain)
                i = j
                continue
            k = next((n for n in sorted(FUNCS | set(OTHER_FUNCS), key=len, reverse=True) if w.startswith(n)), None)
            if k and (len(w) == len(k) or not w[len(k):].isalpha() or w in FUNCS):
                out.append(OTHER_FUNCS.get(k, "\\" + k))
                rest = w[len(k):]
                if rest:
                    out.append(r"\mathrm{%s}" % rest)
            elif len(w) <= 2 or w in UNITS:
                out.append(r"\mathrm{%s}" % w)   # element symbols, units: upright, no spacing
            else:   # a word inside the equation ("hay", "khi"): text, spaced from its neighbours
                out.append(r"\text{%s%s%s}" % (" " if i > 0 or a.extra.get("after") else "", w,
                                                 " " if j < len(run) or a.extra.get("before") else ""))
            i = j
            continue
        if a.kind == "t":
            if a.extra.get("script"):
                j = i
                while j < len(run) and run[j].kind == "t" and not run[j].val.isascii() or \
                        (j > i and j < len(run) and run[j].kind == "t" and run[j].x0 - run[j - 1].x1 < 0.25 * a.size):
                    j += 1
                out.append(r"\text{%s}" % "".join(x.val for x in run[i:max(j, i + 1)]))
                i = max(j, i + 1)
                continue
            raise Unresolved(f"text letter {a.val!r} inside a formula: " + "".join(x.val for x in run)[:30])
        out.append(tok_tex(a))
        i += 1
    return out


def split_funcs(w: str) -> list[str] | None:
    """A run of letters made only of function names (lnsin → [ln, sin]); None otherwise."""
    names = sorted(FUNCS | set(OTHER_FUNCS), key=len, reverse=True)
    out, i = [], 0
    while i < len(w):
        n = next((x for x in names if w.startswith(x, i)), None)
        if n is None:
            return None
        out.append(n)
        i += len(n)
    return out


GREEK_RE = re.compile(r"\\(?:var)?(?:alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|"
                      r"sigma|tau|upsilon|phi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)")


def math_tex(atoms: list[Atom], base: float | None = None, size: float | None = None) -> str:
    """TeX of atoms laid out on one baseline with their scripts (recursively)."""
    atoms = [a for a in atoms if a.kind != "sp"]
    if not atoms:
        return ""
    atoms = sorted(atoms, key=lambda a: a.x0)
    if size is None:
        # letters, digits and boxes set the size (MathType draws parentheses and some operators larger)
        core = [a.size for a in atoms if a.kind in ("v", "t", "n", "box") or GREEK_RE.fullmatch(a.val)] or \
            [a.size for a in atoms]
        size = max(core)
    if base is None:
        mains = [a for a in atoms if a.size >= 0.82 * size]
        base = statistics.median(a.base for a in mains)
    tol = 0.22 * size

    def is_main(a):
        if a.kind == "m" and a.val in BIG_OPS and a.size >= 1.2 * size and a.top < base - 0.3 * size < a.bot:
            return True   # a big ∫ / ∑ spans the line's axis
        if a.kind == "delim" and a.val in "()[]{}|" and a.size >= 1.1 * size and a.top < base - 0.5 * size and \
                a.bot > base and abs(a.base - base) <= 0.4 * size:
            return True   # an enlarged bracket around a radical: drawn lower, it still encloses the row
        return abs(a.base - base) <= tol and not a.extra.get("prime") and \
            (a.size >= 0.82 * size or (a.kind == "delim" and a.extra.get("tall")))
    mains = [a for a in atoms if is_main(a)]
    if not mains:
        raise Unresolved("formula without a baseline")
    parts = []
    # scripts between main atoms
    idx = {id(a): k for k, a in enumerate(mains)}
    buckets: dict[int, list[Atom]] = {k: [] for k in range(-1, len(mains))}
    for a in atoms:
        if id(a) in idx:
            continue
        k = max((idx[id(m)] for m in mains if m.x0 <= a.x0 + 0.5), default=-1)
        buckets[k].append(a)
    # limits written under lim/max/min (MathType puts them below the word, centred)
    limits: dict[int, list[Atom]] = {}
    word = "".join(m.val if m.kind == "t" else " " for m in mains)
    for f in ("lim", "max", "min", "sup", "inf"):
        for mt in re.finditer(f, word):
            ws_ = mains[mt.start():mt.end()]
            under = [a for k_, b in buckets.items() for a in b
                     if ws_[0].x0 - 0.3 * size <= a.xc <= ws_[-1].x1 + 0.3 * size and a.base > base + 0.25 * size]
            if under:
                limits[id(ws_[0])] = under
                for k_ in buckets:
                    buckets[k_] = [a for a in buckets[k_] if a not in under]
    pre = buckets[-1]
    k = 0
    while k < len(mains):
        m = mains[k]
        if k == 0 and pre:
            parts.append(script_tex(pre, base, size, pre_of=m))
        if m.kind == "v":
            # an italic Vietnamese word written in the equation font ("… khi x ≠ 1"), set apart by spaces
            j = k
            while j + 1 < len(mains) and mains[j + 1].kind == "v" and mains[j + 1].x0 - mains[j].x1 < 0.12 * size:
                j += 1
            w = "".join(x.val for x in mains[k:j + 1])
            if w in TEXT_WORDS and not any(buckets[i] for i in range(k, j + 1)) and \
                    (k == 0 or m.x0 - mains[k - 1].x1 >= 0.12 * size) and \
                    (j + 1 == len(mains) or mains[j + 1].x0 - mains[j].x1 >= 0.15 * size):
                parts.append(r"\text{%s%s }" % (" " if k > 0 else "", w))
                k = j + 1
                continue
        if m.kind == "t":
            # a run of upright letters with no script inside: one word (ln, log, min, cm)
            j = k
            while j + 1 < len(mains) and mains[j + 1].kind == "t" and (not buckets[j] or limits.get(id(mains[j]))) and \
                    mains[j + 1].x0 - mains[j].x1 < 0.3 * size:
                j += 1
            mains[k].extra["after"] = k > 0 or bool(pre)
            mains[k].extra["before"] = j + 1 < len(mains) or bool(buckets[j])
            parts.extend(word_tex(mains[k:j + 1]))
            lim = [a for x in mains[k:j + 1] for a in limits.get(id(x), [])]
            if lim:
                parts.append(r"\limits_{%s}" % math_tex(lim))
            k = j
            m = mains[k]
        else:
            parts.append(tok_tex(m))
        sc = buckets[k]
        if sc:
            nxt = mains[k + 1] if k + 1 < len(mains) else None
            # small atoms right before the next main atom, set apart from this one (or after an operator, which
            # takes no scripts): pre-scripts of the next (¹₀n, ²³⁵₉₂U)
            gap_before, gap_after = min(s.x0 for s in sc) - m.x1, (nxt.x0 - max(s.x1 for s in sc)) if nxt else 99
            if nxt is not None and gap_after < 0.3 * size and \
                    (gap_before > 0.5 * size or (m.kind == "m" and m.val in RELS | {"+", "-", r"\pm", r"\to"}
                                                 and gap_after < gap_before)):
                parts.append(script_tex(sc, base, size, pre_of=nxt))
                k += 1
                continue
            if min(s.x0 for s in sc) - m.x1 > 0.6 * size:
                raise Unresolved(f"small glyph {sc[0].val!r} far from its nucleus")
            if m.kind == "m" and m.val in BIG_OPS and m.size >= 1.2 * size:
                parts.append(script_tex(sc, m.base, m.size))   # limits of ∫ / ∑: measured on the big sign
            else:
                parts.append(script_tex(sc, base, size))
        k += 1
    # upright letter runs among the mains (function names)
    return fix_words(parts, mains)


def fix_words(parts: list[str], mains: list[Atom]) -> str:
    # merge consecutive single upright letters of a function name (l n → \ln)
    out = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if re.fullmatch(r"[A-Za-z]", p):
            j = i
            while j < len(parts) and re.fullmatch(r"[A-Za-z]", parts[j]):
                j += 1
            out.append("".join(parts[i:j]))
            i = j
            continue
        out.append(p)
        i += 1
    return join(out)


def script_tex(sc: list[Atom], base: float, size: float, pre_of: Atom | None = None) -> str:
    for a in sc:
        a.extra["script"] = True
    primes = [a for a in sc if a.extra.get("prime")]
    sc = [a for a in sc if not a.extra.get("prime")]
    if primes and pre_of is not None:
        raise Unresolved("prime before its nucleus")
    if not sc:
        return "'" * len(primes)
    if primes and min(p.x0 for p in primes) > min(a.x0 for a in sc):
        raise Unresolved("prime after a script")
    sup = [a for a in sc if a.base < base - 0.08 * size]
    sub = [a for a in sc if a.base >= base - 0.08 * size]
    for part, up in ((sup, True), (sub, False)):
        for a in part:
            off = (base - a.base) if up else (a.base - base)
            if off > 1.0 * size:
                raise Unresolved(f"script {a.val!r} too far from the baseline")
    t = ""
    if sub:
        s = max(a.size for a in sub)
        t += "_" + grp(math_tex(sub, base=statistics.median(a.base for a in sub if a.size >= 0.82 * s), size=s))
    if sup:
        s = max(a.size for a in sup)
        sup_tex = math_tex(sup, base=statistics.median(a.base for a in sup if a.size >= 0.82 * s), size=s)
        t += "^" + grp(sup_tex)
    if pre_of is not None:
        return "{}" + t
    return "'" * len(primes) + t


# ------------------------------------------------------------------------------------------------
# structures (innermost first)
# ------------------------------------------------------------------------------------------------
def build_accents(atoms: list[Atom], S: float) -> list[Atom]:
    accs = sorted([a for a in atoms if a.kind == "acc"], key=lambda a: (round(a.base), a.x0))
    groups: list[list[Atom]] = []
    for a in accs:
        if groups and groups[-1][-1].val == a.val and abs(groups[-1][-1].base - a.base) < 1.0 and \
                a.x0 - groups[-1][-1].x1 < 1.5:
            groups[-1].append(a)
        else:
            groups.append([a])
    for gr in groups:
        x0, x1, ab = min(a.x0 for a in gr), max(a.x1 for a in gr), statistics.median(a.base for a in gr)
        cov = [a for a in atoms if a.kind not in ("acc", "sp") and x0 - 1.5 <= a.xc <= x1 + 1.5
               and ab + 0.35 * S <= a.base <= ab + 1.6 * S]
        if not cov:
            raise Unresolved("accent over nothing")
        cb = min(a.base for a in cov)
        cov = [a for a in cov if abs(a.base - cb) <= 0.25 * S or (a.size < 0.82 * S and a.base < cb + 0.5 * S)]
        tex = (r"\overrightarrow{%s}" if gr[0].val == "arrow" else r"\widehat{%s}") % math_tex(cov)
        b = box(cov + gr, tex, base=statistics.median(a.base for a in cov if a.size >= 0.82 * max(c.size for c in cov)),
                size=max(a.size for a in cov))
        atoms = [a for a in atoms if a not in cov and a not in gr] + [b]
    return atoms


def radical_signs(page: Page, band: tuple[float, float], atoms: list[Atom]) -> list[tuple]:
    """(sign x0, overline stroke, sign bottom, parts) for every radical drawn in the band."""
    out = []
    hl = [s for s in page.strokes if s.kind == "hline" and not s.used and band[0] <= s.y <= band[1]]
    for c in page.strokes:
        if c.kind != "curve" or c.used or not (band[0] <= c.y <= band[1]) or c.h < 4:
            continue
        ov = [s for s in hl if abs(s.y - c.top) < 1.3 and abs(s.x1 - c.x1) < 2.0 and s.x0 > c.x0 + 2]
        if not ov:
            continue
        o = max(ov, key=lambda s: s.w)
        parts = [s for s in page.strokes if s is not c and s is not o and s.kind in ("line", "hline", "rect")
                 and s.x0 >= c.x0 - 1 and s.x1 <= o.x0 + 1.5 and s.top >= c.top - 1.5 and s.bot <= c.bot + 1.5]
        out.append((c.x0, o, c.bot, [c] + parts))
    # a sign drawn with lines only (Word equations, some MathType versions): the long stroke rises to the
    # overline's left end, the short tick strokes lie left of it
    taken = {id(x[1]) for x in out}
    for o in hl:
        if id(o) in taken:
            continue
        rise = [s for s in page.strokes if s.kind == "line" and not s.used and abs(s.x1 - o.x0) < 1.5
                and abs(s.top - o.y) < 1.5 and s.h >= 4]
        if not rise:
            continue
        r = max(rise, key=lambda s: s.h)
        parts = [s for s in page.strokes if s is not o and s.kind in ("line", "hline") and not s.used
                 and s.x0 >= r.x0 - 0.8 * r.h and s.x1 <= o.x0 + 1.0 and s.top >= o.y - 1.0 and s.bot <= r.bot + 1.0]
        parts = parts or [r]
        out.append((min(p.x0 for p in parts), o, r.bot, parts))
    for a in atoms:
        if a.kind != "sqrt":
            continue
        ov = [s for s in hl if abs(s.x0 - a.x1) < 2.5 and a.top - 3 <= s.y <= a.top + 0.5 * a.size]
        if not ov:
            raise Unresolved("radical sign without its overline")
        o = min(ov, key=lambda s: abs(s.x0 - a.x1))
        out.append((a.x0, o, a.bot, [a]))
    return out


def build_radical(atoms, sign_x0, ov: Stroke, sign_bot, parts, S) -> list[Atom]:
    inside = [a for a in atoms if a.kind not in ("sp", "sqrt") and ov.x0 - 1.0 <= a.xc <= ov.x1 + 1.0
              and a.top >= ov.y - 1.5 and a.base <= sign_bot + 1.0]
    if not inside:
        raise Unresolved("empty radical")
    rb = statistics.median(a.base for a in inside if a.size >= 0.82 * max(i.size for i in inside))
    # an index sits in the crook of the sign: within its height, above its middle
    idx = [a for a in atoms if a not in inside and a.kind in ("n", "v", "m") and a.x0 >= sign_x0 - 1.5
           and a.x1 <= ov.x0 + 1 and a.size < 0.82 * S and a.base < rb - 0.2 * S
           and a.base >= ov.y - 0.1 * S and a.top >= ov.y - 0.6 * S]
    tex = r"\sqrt[%s]{%s}" % (math_tex(idx), math_tex(inside)) if idx else r"\sqrt{%s}" % math_tex(inside)
    sq = [a for a in atoms if a.kind == "sqrt" and a in parts]
    for p in parts:
        if isinstance(p, Stroke):
            p.used = True
    ov.used = True
    members = inside + idx + sq
    b = box(members, tex, base=rb, x0=sign_x0, x1=ov.x1, top=ov.y, size=max(a.size for a in inside))
    return [a for a in atoms if a not in members] + [b]


def stack(part: list[Atom], y: float, above: bool, S: float) -> list[Atom]:
    """The row next to the bar plus the scripts touching it."""
    if not part:
        return part
    core = [a for a in part if a.kind != "delim"] or part
    full = max(a.size for a in core)
    core = [a for a in core if a.size >= 0.82 * full or a.kind == "box"]   # scripts never set the row
    near_b = min(core, key=lambda a: abs(a.base - y) if above else abs(a.top - y))
    big = max(a.size for a in core if abs(a.base - near_b.base) <= 0.3 * S)
    row = [a for a in part if abs(a.base - near_b.base) <= 0.25 * big and
           (a.size >= 0.82 * big or a.kind in ("box", "delim"))]
    if not row:
        row = [near_b]
    rest = [a for a in part if a not in row]
    grown = True
    while grown:
        grown = False
        for a in list(rest):
            if a.size < 0.82 * big and -0.9 * big <= a.base - near_b.base <= 0.6 * big and \
                    any(-1.5 <= a.x0 - r.x1 < 0.5 * big or (r.x0 <= a.xc <= r.x1) for r in row):
                row.append(a)
                rest.remove(a)
                grown = True
    return row


def build_bar(atoms: list[Atom], s: Stroke, S: float) -> list[Atom]:
    span = [a for a in atoms if a.kind not in ("sp",) and s.x0 - 1.5 <= a.xc <= s.x1 + 1.5
            and a.x0 >= s.x0 - 3 and a.x1 <= s.x1 + 3]
    num = [a for a in span if a.bot <= s.y + 1.0 and a.base >= s.y - 1.7 * S and a.base < s.y]
    den = [a for a in span if a.top >= s.y - 1.0 and a.base <= s.y + 2.4 * S and a.base > s.y]
    near_above = max((a.bot for a in num if a.kind != "sp"), default=None)
    num, den = stack(num, s.y, True, S), stack(den, s.y, False, S)

    def formula_part(part):
        if any(a.kind == "t" and not a.val.isascii() for a in part):
            return False   # running text above/below a stroke: a table rule or an underline, not a fraction
        lettered = [a for a in part if a.kind in ("t", "v", "n")]
        if not any(a.kind in ("v", "n", "m", "box", "delim", "sqrt") or (a.kind == "t" and not a.bold)
                   for a in part) or (lettered and all(a.bold for a in lettered)):
            return False   # an underlined label ("Chọn C") over the next line is no fraction
        c = (min(a.x0 for a in part) + max(a.x1 for a in part)) / 2
        return abs(c - (s.x0 + s.x1) / 2) <= 0.25 * s.w + 1.5
    if num and den and s.y - near_above > 0.6 * S and min(a.top for a in den) - s.y < 0.3 * S:
        return atoms   # touching what is under it, far from what is above: an overline (conjugate z̄), no fraction
    if num and den and formula_part(num) and formula_part(den):
        small = max(a.size for a in num + den) < 0.82 * S
        tex = (r"\frac{%s}{%s}" if small else r"\dfrac{%s}{%s}") % (math_tex(num), math_tex(den))
        s.used, s.role = True, "fraction"
        # parentheses inside a numerator are drawn larger: the fraction's size is that of its letters and digits
        size = max((a.size for a in num + den if a.kind != "delim"), default=max(a.size for a in num + den))
        b = box(num + den, tex, base=s.y + 0.28 * size, x0=s.x0, x1=s.x1, size=size)
        return [a for a in atoms if a not in num and a not in den] + [b]
    return atoms


def build_bars(page: Page, band, atoms: list[Atom], S: float) -> list[Atom]:
    """Fraction bars and radicals, smallest first (an inner structure is a box before the outer one is built)."""
    jobs = [("bar", s.w, s) for s in page.strokes if s.kind == "hline" and not s.used and band[0] <= s.y <= band[1]]
    for sign_x0, ov, sign_bot, parts in radical_signs(page, band, atoms):
        jobs.append(("rad", ov.x1 - sign_x0, (sign_x0, ov, sign_bot, parts)))
    rad_ov = {id(j[2][1]) for j in jobs if j[0] == "rad"}
    jobs = [j for j in jobs if not (j[0] == "bar" and id(j[2]) in rad_ov)]
    for kind, _, job in sorted(jobs, key=lambda j: j[1]):
        if kind == "rad":
            atoms = build_radical(atoms, *job, S)
        else:
            atoms = build_bar(atoms, job, S)
    return atoms


def slashes(page: Page, band, S: float) -> list[Atom]:
    """MathType draws some slashes (2/9, A/2) as a short rising stroke between the characters."""
    out = []
    for st in page.strokes:
        if st.kind == "line" and st.rising and not st.used and band[0] <= st.y <= band[1] and \
                st.w <= 0.7 * S and 0.5 * S <= st.h <= 1.5 * S:
            st.used, st.role = True, "slash"
            a = Atom(st.x0, st.x1, st.top, st.bot, st.bot - 0.12 * st.h, S, "n", "/")
            out.append(a)
    return out


def build_underlines(page: Page, band, atoms: list[Atom], S: float):
    """A stroke right under text (an underlined answer label, an underlined word) is formatting only."""
    for s in page.strokes:
        if s.kind != "hline" or s.used or not (band[0] <= s.y <= band[1]):
            continue
        # an underline runs just under the baseline of what it underlines (a fraction bar sits on the math axis,
        # well above the denominator's baseline and ≥0.3 em under the numerator's)
        over = [a for a in atoms if a.kind != "sp" and s.x0 - 1 <= a.xc <= s.x1 + 1 and a.size >= 0.82 * S
                and -0.05 * a.size <= s.y - a.base <= 0.28 * a.size]
        if over:
            s.used, s.role = True, "underline"


def build_overlines(page: Page, band, atoms: list[Atom], S: float) -> list[Atom]:
    for s in page.strokes:
        if s.kind != "hline" or s.used or not (band[0] <= s.y <= band[1]):
            continue
        cov = [a for a in atoms if a.kind not in ("sp",) and s.x0 - 1.5 <= a.xc <= s.x1 + 1.5 and
               -1.0 <= a.top - s.y <= 0.5 * S and a.base - s.y <= 1.3 * S]
        above = [a for a in atoms if a.kind != "sp" and s.x0 - 1 <= a.xc <= s.x1 + 1 and 0 <= s.y - a.bot <= 0.4 * S]
        if cov and not above and all(a.mathy or a.kind == "n" for a in cov):
            s.used, s.role = True, "overline"
            b = box(cov, r"\overline{%s}" % math_tex(cov), size=max(a.size for a in cov))
            atoms = [a for a in atoms if a not in cov] + [b]
    return atoms


def build_pieces(atoms: list[Atom], S: float) -> list[Atom]:
    """Stacked pieces of an extensible delimiter → one tall delimiter."""
    # columns of pieces (same delimiter, same x), then runs without a gap down each column
    cols: list[list[Atom]] = []
    for p in sorted([a for a in atoms if a.kind == "piece"], key=lambda a: a.x0):
        c = next((c for c in cols if c[0].val == p.val and abs(c[0].x0 - p.x0) < 1.5), None)
        if c is None:
            cols.append([p])
        else:
            c.append(p)
    groups: list[list[Atom]] = []
    for c in cols:
        c.sort(key=lambda a: a.top)
        for p in c:
            if groups and groups[-1][0] in c and p.top - max(q.bot for q in groups[-1]) < 0.6 * S:
                groups[-1].append(p)
            else:
                groups.append([p])
    out = [a for a in atoms if a.kind != "piece"]
    for g in groups:
        parts = {p.extra["part"] for p in g}
        v = g[0].val
        if v == "∫":
            if not {"top", "bot"} <= parts:
                raise Unresolved("broken integral sign")
            b = Atom(min(p.x0 for p in g), max(p.x1 for p in g), min(p.top for p in g), max(p.bot for p in g),
                     0, max(p.size for p in g), "m", r"\int", glyphs=[x for p in g for x in p.glyphs])
            b.base = (b.top + b.bot) / 2 + 0.3 * S
            out.append(b)
            continue
        need = {"top", "bot"} if v in "()[]" else ({"top", "mid", "bot"} if v in "{}" else {"ext"})
        if not need <= parts:
            raise Unresolved(f"incomplete big delimiter {v}")
        b = Atom(min(p.x0 for p in g), max(p.x1 for p in g), min(p.top for p in g), max(p.bot for p in g),
                 0, max(p.size for p in g), "delim", v, glyphs=[x for p in g for x in p.glyphs])
        b.extra["tall"] = True
        b.base = (b.top + b.bot) / 2 + 0.3 * S
        out.append(b)
    return out


# ------------------------------------------------------------------------------------------------
# lines
# ------------------------------------------------------------------------------------------------
@dataclass
class Line:
    atoms: list
    base: float
    page: int = 0

    @property
    def x0(self):
        return min(a.x0 for a in self.atoms)

    @property
    def x1(self):
        return max(a.x1 for a in self.atoms)

    @property
    def top(self):
        return min(a.top for a in self.atoms)

    @property
    def bot(self):
        return max(a.bot for a in self.atoms)


BIG_OPS = {r"\int", r"\sum", r"\prod", r"\oint", r"\iint"}


def make_lines(atoms: list[Atom], S: float) -> list[Line]:
    """Main-size atoms grouped by baseline; small atoms (scripts) join the line of the nucleus they follow."""
    for a in atoms:
        if a.val == "'" and a.kind in ("m", "n"):
            a.extra["prime"] = True
        if a.kind == "m" and a.val in BIG_OPS and a.size >= 1.2 * S and not a.glyphs[1:]:
            # a big ∫/∑ glyph sits lower than the text: its line is the one through its middle (like built pieces)
            a.base = (a.top + a.bot) / 2 + 0.3 * S
    mains = sorted([a for a in atoms if (a.size >= 0.82 * S or (a.kind == "delim" and a.extra.get("tall")))
                    and a.kind != "sp" and not a.extra.get("prime")],
                   key=lambda a: a.base)
    lines: list[Line] = []
    for a in mains:
        if lines and abs(a.base - lines[-1].base) <= 0.3 * S:
            lines[-1].atoms.append(a)
        else:
            lines.append(Line([a], a.base))
    for L in lines:
        L.base = statistics.median(a.base for a in L.atoms if a.kind not in ("box", "delim")) \
            if any(a.kind not in ("box", "delim") for a in L.atoms) else statistics.median(a.base for a in L.atoms)
    smalls = [a for a in atoms if a.kind != "sp" and not any(a in L.atoms for L in lines)]
    for a in smalls:
        cands = [L for L in lines if -1.0 * S <= a.base - L.base <= 0.75 * S]
        if not cands:   # the limits over / under a big ∫ / ∑ stand further out than any script
            cands = [L for L in lines if any(b.kind == "m" and b.val in BIG_OPS and b.size >= 1.2 * S and
                                             b.x0 - 0.3 * S <= a.xc <= b.x1 + 0.6 * S and
                                             (b.top - 1.0 * S <= a.base <= b.top + 0.3 * S or
                                              b.bot - 0.3 * S <= a.base <= b.bot + 1.0 * S) for b in L.atoms)]
        if not cands:
            raise Unresolved(f"small glyph {a.val!r} on no line")
        # the line whose atom ends right before it (its nucleus), else the nearest baseline
        def score(L):
            # the nucleus is a symbol, never the tall brace of a system (whose box spans several rows)
            near = min((abs(a.x0 - b.x1) for b in L.atoms if b.x1 <= a.x0 + 1.5 and
                        not (b.kind == "delim" and b.extra.get("tall"))), default=99)
            return (near > 0.6 * S, abs(a.base - L.base))
        best = min(cands, key=score)
        best.atoms.append(a)
    spaces = [a for a in atoms if a.kind == "sp"]
    for a in spaces:
        L = min(lines, key=lambda L: abs(a.base - L.base), default=None)
        if L is not None and abs(a.base - L.base) <= 0.4 * S:
            L.atoms.append(a)
        else:
            for g in a.glyphs:
                g.used = True
    for L in lines:
        L.atoms.sort(key=lambda a: a.x0)
    return lines


def build_systems(lines: list[Line], S: float) -> list[Line]:
    """A tall { or [ with stacked rows to its right and no closing partner → cases / bracket system."""
    for L in list(lines):
        for d in [a for a in L.atoms if a.kind == "delim" and a.val in "{[" and a.extra.get("tall")]:
            if d not in L.atoms:
                continue
            closer = [a for M in lines for a in M.atoms if a.kind == "delim" and a.val == {"{": "}", "[": "]"}[d.val]
                      and a.x0 > d.x1 and a.extra.get("tall") and abs(a.top - d.top) < 2 and abs(a.bot - d.bot) < 2]
            if closer:
                continue
            rows = [M for M in lines if M is not L and d.top - 0.2 * S <= M.base <= d.bot + 0.3 * S]
            if L.base <= d.bot + 0.3 * S and any(a.x0 >= d.x1 - 0.5 and a.kind != "sp" for a in L.atoms if a is not d):
                rows.append(L)
            # a second system to the right (⇔ {…}) ends this one; the relation before it stays outside
            stops = [a for M in rows for a in M.atoms if a is not d and a.x0 > d.x1 and
                     ((a.kind == "delim" and a.val in "{[" and a.extra.get("tall")) or a.extra.get("system"))
                     and a.top < d.bot and a.bot > d.top]
            right = min((a.x0 for a in stops), default=1e9)
            body, cands = [], []
            # a second column ("… khi x ≠ 1"): items starting at the same x after a gap in at least two rows
            starts = []
            for M in rows:
                its = sorted([a for a in M.atoms if a.xc >= d.x1 and a.kind != "sp" and a.x0 < right - 0.5],
                             key=lambda a: a.x0)
                starts += [(M, b.x0) for a, b in zip(its, its[1:]) if b.x0 - a.x1 >= 0.6 * S and b.x0 - d.x1 >= 2 * S]
            cols = sorted({round(x, 1) for M, x in starts
                           if len({id(N) for N, y in starts if abs(y - x) < 1.5}) >= 2})
            col = cols[0] if cols else None
            for M in sorted(rows, key=lambda M: M.base):
                its = sorted([a for a in M.atoms if a.xc >= d.x1 and a.kind != "sp"], key=lambda a: a.x0)
                row, label = [], []
                for n, a in enumerate(its):
                    if a.x0 >= right - 0.5:
                        break
                    if row and a.x0 - row[-1].x1 > 1.5 * S and col is not None and abs(a.x0 - col) < 1.5:
                        row.append(a)
                        continue
                    if row and a.x0 - row[-1].x1 > 1.5 * S:
                        # an equation label "(1)" / "(I)" after a wide gap still belongs to the row
                        rest = [b for b in its[n:] if b.x0 < right - 0.5]
                        lab = "".join(b.val for b in rest)
                        if a.x0 - row[-1].x1 < 5 * S and re.fullmatch(r"\(\s*(?:\d{1,2}|[IVX]{1,4}|[a-z])\s*\)\.?", lab):
                            label = [b for b in rest if b.val != "."] if lab.endswith(".") else rest
                        break
                    if a.kind == "t" and not a.val.isascii():
                        # the word that starts here is text after the system: give back its first letters
                        while row and row[-1].kind == "t" and a.x0 - row[-1].x1 < 0.15 * S:
                            a = row.pop()
                        break
                    row.append(a)
                if stops and row and row[-1].kind == "m" and row[-1].val in RELS | {r"\Leftarrow"} and \
                        right - row[-1].x1 < 1.5 * S and len(row) > 1:
                    row.pop()
                if row and not label:
                    # a label written closer: "… ≥ 0 (2)."
                    for k in range(len(row) - 1, 0, -1):
                        tail = "".join(b.val for b in row[k:])
                        if row[k].val == "(" and row[k].x0 - row[k - 1].x1 > 0.25 * S and \
                                re.fullmatch(r"\(\s*(?:\d{1,2}|[IVX]{1,4})\s*\)\.?", tail):
                            row, label = row[:k], [b for b in row[k:] if b.val != "."]
                            break
                if row:
                    cands.append((M, row, label))
            anchored = [c for c in cands if min(a.x0 for a in c[1]) <= d.x1 + 1.2 * S]
            if anchored:
                # rows centred under the widest row (MathType centres the rows of some systems)
                wide = max(anchored, key=lambda c: max(a.x1 for a in c[1]) - min(a.x0 for a in c[1]))[1]
                w0, w1 = min(a.x0 for a in wide), max(a.x1 for a in wide)
                for c in cands:
                    r0, r1 = min(a.x0 for a in c[1]), max(a.x1 for a in c[1])
                    if c in anchored or (r1 <= w1 + 0.5 * S and abs((r0 + r1) / 2 - (w0 + w1) / 2) < 1.0 * S):
                        body.append(c)
            if len(body) < 2:
                continue
            def row_tex(r):
                k = next((i for i, a in enumerate(r) if i > 0 and col is not None and abs(a.x0 - col) < 1.5
                          and a.x0 - r[i - 1].x1 >= 0.6 * S), None)
                if k is None:
                    return math_tex(r)
                return math_tex(r[:k]) + (" & " if d.val == "{" else r" \quad ") + math_tex(r[k:])
            tex_rows = [row_tex(r) + (r" \quad " + math_tex(lab) if lab else "") for _, r, lab in body]
            tex = (r"\begin{cases} %s \end{cases}" if d.val == "{" else r"\left[\begin{aligned} %s \end{aligned}\right.") \
                % r" \\ ".join(tex_rows)
            members = [d] + [a for _, r, lab in body for a in r + lab]
            for M in lines:
                M.atoms = [a for a in M.atoms if a not in members]
            centre = (d.top + d.bot) / 2 + 0.3 * S
            live = [M for M in lines if any(a.kind != "sp" for a in M.atoms)]
            home = min(live, key=lambda M: abs(M.base - centre)) if live else None
            b = box(members, tex, base=home.base if home and abs(home.base - centre) < 0.8 * S else centre, size=S)
            b.extra["system"] = True
            if home is None or abs(home.base - centre) >= 0.8 * S:
                home = Line([], centre)
                lines.append(home)
            home.atoms.append(b)
            home.atoms.sort(key=lambda a: a.x0)
    return sorted([M for M in lines if any(a.kind != "sp" for a in M.atoms)], key=lambda M: M.base)


# ------------------------------------------------------------------------------------------------
# a line → content nodes (text and formulas)
# ------------------------------------------------------------------------------------------------
def words_of(L: Line, S: float) -> list[list[Atom]]:
    """Split a line at space glyphs and wide gaps; scripts stay with their nucleus."""
    out, cur = [], []
    prev = None
    for a in L.atoms:
        if a.kind == "sp":
            if cur:
                out.append(cur)
            cur, prev = [], None
            continue
        main = abs(a.base - L.base) <= 0.3 * S
        if prev is not None and main and a.x0 - prev.x1 > 0.45 * S and cur:
            out.append(cur)
            cur = []
        cur.append(a)
        prev = a if main else (prev or a)
    if cur:
        out.append(cur)
    out = [p for w in out for p in split_viet_gap(w, S)]
    for w in out:
        absorb_letters(w)
    return [p for w in out for p in split_mixed(w, L, S)]


def split_viet_gap(w: list[Atom], S: float) -> list[list[Atom]]:
    """A gap without a space glyph between a number/formula and a Vietnamese word ("3 là") is a space too."""
    for i in range(1, len(w)):
        a, b = w[i - 1], w[i]
        if b.x0 - a.x1 <= 0.12 * S or (a.kind == "t") == (b.kind == "t"):
            continue
        side = w[i:] if b.kind == "t" else w[:i]
        letters = []
        for c in (side if b.kind == "t" else reversed(side)):
            if c.kind != "t":
                break
            letters.append(c)
        if any(not c.val.isascii() for c in letters):
            return [w[:i]] + split_viet_gap(w[i:], S)
    return [w]


def absorb_letters(w: list[Atom]):
    """Italic Latin letters touching Vietnamese letters of the same style and size are one (italic) word."""
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(w):
            if a.kind != "v":
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(w):
                    b = w[j]
                    if b.kind == "t" and b.italic == a.italic and b.bold == a.bold and abs(b.size - a.size) < 0.3 and \
                            abs(b.base - a.base) < 0.5 and (b.x0 - a.x1 if j > i else a.x0 - b.x1) < 0.15 * a.size and \
                            (not b.val.isascii() or b.extra.get("viet_word")):
                        a.kind = "t"
                        a.extra["viet_word"] = True
                        changed = True
                        break


def split_mixed(w: list[Atom], L: Line, S: float) -> list[list[Atom]]:
    """A formula glued to a Vietnamese word ("(C)tâm", "hệ{…}", "mvới"): split where math atoms meet a run of
    letters holding a Vietnamese letter. ASCII letter runs (ln, log, units) stay with the formula."""
    def cls(a):
        if a.kind == "t":
            return "T"
        if a.kind in ("v", "m", "box", "sqrt", "acc") or (a.kind == "delim" and a.extra.get("tall")):
            return "M"
        return "N"
    segs: list[list[Atom]] = []
    last = None   # class of the last main-line atom
    for a in w:
        main = abs(a.base - L.base) <= 0.3 * S
        c = cls(a)
        after_punct = segs and segs[-1][-1].kind == "n" and segs[-1][-1].val in ":;," and \
            any(x.kind == "t" and not x.val.isascii() for x in segs[-1])
        if segs and after_punct:
            segs.append([a])   # "nhân:²₁D": the sentence punctuation ends the Vietnamese word
        elif segs and main and last is not None and {c, last} == {"T", "M"}:
            segs.append([a])
        elif segs:
            segs[-1].append(a)
        else:
            segs.append([a])
        if main and c != "N":
            last = c
    out: list[list[Atom]] = []
    out_viet: list[bool] = []
    for sgm in segs:
        viet = any(a.kind == "t" and not a.val.isascii() for a in sgm)
        if out and not viet and not out_viet[-1]:
            out[-1].extend(sgm)
        else:
            out.append(list(sgm))
            out_viet.append(viet)
    return out


def word_kind(w: list[Atom], S: float | None = None) -> str:
    """'t' text word, 'm' formula word, 'n' neutral (numbers, punctuation)."""
    if any(a.kind in ("m", "box", "sqrt", "acc", "piece") or (a.kind == "delim" and a.extra.get("tall")) for a in w):
        return "m"
    if S is not None and any(a.size < 0.82 * S for a in w) and any(a.size >= 0.82 * S for a in w):
        return "m"   # scripts (5^x, W_đ, 10^6): a formula
    letters = [a for a in w if a.kind in ("t", "v")]
    if any(a.kind == "t" and not a.val.isascii() for a in w):
        if len({(a.italic, a.bold) for a in letters}) > 1:
            return "x"   # Vietnamese letters glued to letters of another style: unclear
        for a in letters:
            a.kind = "t"   # an italic Vietnamese word is text
        return "t"
    if any(a.kind == "v" for a in w):
        return "m"
    if letters:
        return "t"
    return "n"


def sentence_end(ws: list[list[Atom]], kinds: list[str], j: int) -> bool:
    """"… AD = a. SA vuông …": a formula word ending in a letter and a full stop, then a space and one capitalised
    formula word followed by text, ends the sentence ("4. W" in "W = 4. W_đ = …" is a product)."""
    w, nxt = ws[j], ws[j + 1]
    return len(w) > 1 and w[-1].val == "." and w[-2].kind == "v" and bool(nxt) and nxt[0].val[:1].isupper() and \
        (j + 2 >= len(ws) or kinds[j + 2] == "t")


def line_nodes(L: Line, S: float) -> list[dict]:
    ws = words_of(L, S)
    kinds = [word_kind(w, S) for w in ws]
    # an italic Latin word among italic Vietnamese words is text (an italic sentence), not a variable
    for i, (w, k) in enumerate(zip(ws, kinds)):
        if k == "m" and all(a.kind in ("v", "n") for a in w) and sum(a.kind == "v" for a in w) >= 2:
            nb = [ws[j] for j in (i - 1, i + 1) if 0 <= j < len(ws)]
            if any(kinds[j] == "t" and all(a.italic for a in ws[j] if a.kind == "t") and any(a.kind == "t" for a in ws[j])
                   for j in (i - 1, i + 1) if 0 <= j < len(ws)) and nb:
                kinds[i] = "t"
                for a in w:
                    if a.kind == "v":
                        a.kind = "t"
    if "x" in kinds:
        w = ws[kinds.index("x")]
        raise Unresolved("italic variable glued to Vietnamese text: " + "".join(a.val for a in w)[:30])
    # runs of formula words; neutral words between two formula words belong to the formula
    nodes = []
    i = 0
    while i < len(ws):
        if kinds[i] != "m":
            nodes.append(("t", ws[i]))
            i += 1
            continue
        j = i
        while j + 1 < len(ws) and not sentence_end(ws, kinds, j) and (kinds[j + 1] == "m" or (kinds[j + 1] == "n" and j + 2 < len(ws) and kinds[j + 2] == "m"
                                                            and not re.fullmatch(r"[.,;:]", "".join(a.val for a in ws[j + 1])))):
            j += 1
        run = [a for w in ws[i:j + 1] for a in w]
        nodes.append(("m", run))
        i = j + 1
    out = []
    for k, (kind, atoms) in enumerate(nodes):
        if kind == "t":
            txt = "".join(a.val for a in sorted(atoms, key=lambda a: a.x0))
            mark = []
            if atoms and all(a.bold for a in atoms if a.kind in ("t", "v")) and any(a.kind in ("t", "v") for a in atoms):
                mark.append("b")
            if any(a.size < 0.82 * S for a in atoms):
                raise Unresolved("small glyph in running text: " + txt[:30])
            out.append({"t": "s", "v": (" " if out else "") + txt, **({"m": mark} if mark else {})})
            continue
        # sentence punctuation after a formula stays text
        atoms = sorted(atoms, key=lambda a: a.x0)
        tail = []
        while atoms and abs(atoms[-1].base - L.base) <= 0.3 * S and (
                (atoms[-1].kind == "n" and atoms[-1].val in ".,;:") or
                (atoms[-1].kind == "delim" and atoms[-1].val in "([" and not atoms[-1].extra.get("tall")
                 and k + 1 < len(nodes) and nodes[k + 1][0] == "t")):
            tail.insert(0, atoms.pop())   # punctuation, or "(" opening a text remark "4 (thỏa)"
        tex = line_math(atoms, L, S)
        out.append({"t": "s", "v": " "} if out else {"t": "s", "v": ""})
        out.append({"t": "m", "tex": tex})
        if tail:
            out.append({"t": "s", "v": "".join(a.val for a in tail)})
    return tidy(out)


def line_math(atoms: list[Atom], L: Line, S: float) -> str:
    """Formula of a run: big delimiters pair as \\left…\\right around their contents."""
    atoms = [a for a in atoms if a.kind != "sp"]
    tall = [a for a in atoms if a.kind == "delim" and a.extra.get("tall") or
            (a.kind == "delim" and a.size > 1.15 * S)]
    if not tall:
        return math_tex(atoms, base=L.base, size=S)
    # pair tall delimiters by nesting order
    stack_, pairs = [], []
    for a in sorted(tall, key=lambda a: a.x0):
        if a.val in OPEN:
            stack_.append(a)
        elif a.val in CLOSE:
            if not stack_ or OPEN[CLOSE.index(a.val)] != stack_[-1].val:
                raise Unresolved("unbalanced big delimiters")
            pairs.append((stack_.pop(), a))
        elif a.val == "|":
            if stack_ and stack_[-1].val == "|":
                pairs.append((stack_.pop(), a))
            else:
                stack_.append(a)
    if stack_:
        raise Unresolved("unbalanced big delimiters")
    for o, c in sorted(pairs, key=lambda p: p[1].x1 - p[0].x0):
        inner = [a for a in atoms if a is not o and a is not c and o.x1 - 0.5 <= a.xc <= c.x0 + 0.5]
        lt = {"{": r"\{", "}": r"\}"}
        tex = r"\left%s %s \right%s" % (lt.get(o.val, o.val), math_tex(inner, base=L.base, size=S) if inner else "",
                                        lt.get(c.val, c.val))
        b = box([o, c] + inner, tex, base=L.base, size=S)
        atoms = [a for a in atoms if a not in inner and a is not o and a is not c] + [b]
    return math_tex(atoms, base=L.base, size=S)


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
    for a, b in zip(out, out[1:]):
        if a["t"] == "m" and b["t"] == "s" and re.match(r"\s+[.,;:)]", b["v"]):
            b["v"] = b["v"].lstrip()
    return [n for n in out if not (n["t"] == "s" and n["v"] == "")]


# ------------------------------------------------------------------------------------------------
# the question's region
# ------------------------------------------------------------------------------------------------
LABEL_RE = re.compile(r"^\s*(?:Câu|Bài)\s*(\d+)\s*[.:]?")
SOLUTION_RE = re.compile(r"^\s*(?:Lời\s*giải|Hướng\s*dẫn(?:\s*giải)?|Giải\s*[:.]|Giải$|HD\s*[:.]|Phương\s*pháp|"
                         r"Chọn\s*(?:đáp\s*án\s*)?[A-D]\b|Đáp\s*án\b|Cách\s*giải)", re.I)
ANSWER_RE = re.compile(r"(?:Chọn\s*(?:đáp\s*án\s*)?|Đáp\s*án\s*(?:đúng\s*(?:là)?\s*)?:?\s*)([A-D])\b")
MARGIN = 0.035


def rough_lines(glyphs: list[Glyph]) -> list[list[Glyph]]:
    gl = sorted(glyphs, key=lambda g: g.base)
    out: list[list[Glyph]] = []
    for g in gl:
        if out and abs(g.base - out[-1][0].base) <= 0.35 * max(g.size, 6):
            out[-1].append(g)
        else:
            out.append([g])
    return [sorted(L, key=lambda g: g.x0) for L in out]


def text_of(L: list[Glyph]) -> str:
    return unicodedata.normalize("NFC", "".join(g.ch for g in L))


def furniture(pages: dict[int, Page]):
    """Header/footer lines: near the top/bottom edge and repeated on another page (page numbers aside)."""
    keys = {}
    for pno, pg in pages.items():
        for L in rough_lines([g for g in pg.glyphs if g.upright]):
            y = L[0].base
            if 0.08 * pg.height < y < 0.92 * pg.height:
                continue
            k = re.sub(r"[\d\s]+", "", text_of(L))
            keys.setdefault((k, round(y / 3)), set()).add(pno)
            L_ids = keys.setdefault(("ids", pno, k, round(y / 3)), [])
            L_ids.extend(L)
    for pno, pg in pages.items():
        pg.furniture = set()
        for st in pg.strokes:   # header/footer rules: long strokes near the top/bottom edge
            if st.kind in ("hline", "rect") and st.h <= 2 and st.w > 0.55 * pg.width and \
                    not 0.12 * pg.height < st.y < 0.88 * pg.height:
                st.used, st.role = True, "furniture"
    # drawings repeated at the same place on several pages near the edges (logos, header boxes, footer rules)
    seen: dict = {}
    for pno, pg in pages.items():
        for st in pg.strokes:
            if not 0.15 * pg.height < st.y < 0.85 * pg.height:
                seen.setdefault((st.kind, round(st.x0), round(st.x1), round(st.top), round(st.bot)), set()).add(pno)
    for pno, pg in pages.items():
        lines = [L for L in rough_lines([g for g in pg.glyphs if g.upright]) if sum(g.ch.isalnum() for g in L) >= 5]
        for st in pg.strokes:
            k = (st.kind, round(st.x0), round(st.x1), round(st.top), round(st.bot))
            # a shading box behind a line of text (a solution's first line) is not a header, even when repeated
            shades = any(st.top - 1 <= L[0].base <= st.bot + 1 and min(g.x0 for g in L) < st.x1 and
                         max(g.x1 for g in L) > st.x0 for L in lines)
            if len(seen.get(k, ())) >= 2 and not shades:
                st.used, st.role = True, "furniture"
        # the body of a continuation page starts under its header
        hdr = [st.bot for st in pg.strokes if st.role == "furniture" and st.y < 0.15 * pg.height] + \
              [g.bot for g in pg.glyphs if id(g) in pg.furniture and g.base < 0.15 * pg.height]
        pg.body_top = max(hdr) + 0.5 if hdr else MARGIN * pg.height
        pg.above_body = [L for L in lines if MARGIN * pg.height <= L[0].base < pg.body_top]
    for key, v in list(keys.items()):
        if key[0] == "ids":
            continue
        if len(v) >= 2:   # repeated on another page (a lone line of digits may be a row of exponents)
            for pno in v:
                pages[pno].furniture |= {id(g) for g in keys[("ids", pno, key[0], key[1])]}


def check_above_body(pg: Page):
    """A continuation page's body starts under its header: a real text line above that point would be lost."""
    fur = getattr(pg, "furniture", set())
    for L in getattr(pg, "above_body", []):
        if not any(id(g) in fur for g in L):
            raise Unresolved(f"text above the body of page {pg.number}: " + text_of(L)[:30])


def body(pg: Page) -> list[Glyph]:
    fur = getattr(pg, "furniture", set())
    return [g for g in pg.glyphs if g.upright and id(g) not in fur and
            MARGIN * pg.height <= g.base <= (1 - MARGIN) * pg.height]


def region(pages: dict[int, Page], first: int, number: int):
    """[(page, top, bottom)] bands of the question: from its 'Câu N' line to the next question's label."""
    bands = []
    started = False
    furniture(pages)
    for pno in sorted(p for p in pages if p >= first):
        lines = rough_lines(body(pages[pno]))
        top = None
        prevs: list = []
        try:
            psize = body_size(body(pages[pno]))
        except Unresolved:
            psize = 10.0

        def end_of(L):
            # the next item's extent (numerators, brackets rising from its line) never cuts into our last line
            # (our last line: a body-size text line clearly above the next item's own line)
            e = line_top(pages[pno], L) - 0.5
            lt = min(g.top for g in L)
            ours = [P for P in prevs if max(g.base for g in P) < lt - 0.2 * psize]
            if ours:
                big = [g for g in ours[-1] if g.size >= 0.82 * max(h.size for h in ours[-1])]
                e = max(e, max(g.base for g in big) + 0.3 * max(g.size for g in big))
            return e
        for L in lines:
            m = LABEL_RE.match(text_of(L))
            if started and not m and is_heading(L):
                if top is None:
                    top = max(MARGIN * pages[pno].height, pages[pno].body_top)
                    check_above_body(pages[pno])
                bands.append((pno, top, end_of(L)))
                return bands
            if m and any(is_bold(g.font) for g in L[:3]):
                n = int(m.group(1))
                if not started and pno == first and n == number:
                    started, top = True, line_top(pages[pno], L) - 1
                    prevs.append(L)
                    continue
                if started:
                    if top is None:
                        top = max(MARGIN * pages[pno].height, pages[pno].body_top)
                        check_above_body(pages[pno])
                    bands.append((pno, top, end_of(L)))
                    return bands
            if started and sum(1 for g in L if g.ch.isalnum() and g.size >= 0.85 * psize) >= 3:
                prevs.append(L)   # a line of text at body size (not a row of scripts rising from the next line)
        if not started:
            raise Unresolved(f"'Câu {number}' not found on page {first}")
        if top is None:
            top = max(MARGIN * pages[pno].height, pages[pno].body_top)
            check_above_body(pages[pno])
        bands.append((pno, top, (1 - MARGIN) * pages[pno].height))
    raise Unresolved("the question's end was not found")


def line_top(pg: Page, L: list[Glyph]) -> float:
    """Top of a line with what sticks up from it: numerators over its fraction bars, accents over its letters."""
    S = max(g.size for g in L)
    lt, lb = min(g.top for g in L), max(g.base for g in L)
    top = lt
    for st in pg.strokes:
        if st.kind == "hline" and lt - 0.5 <= st.y <= lb + 0.3 * S:
            for g in pg.glyphs:
                if st.x0 - 1 <= g.xc <= st.x1 + 1 and st.y - 1.8 * S <= g.base < st.y and g.top < top:
                    top = g.top
    for st in pg.strokes:   # radicals, brackets drawn from the line upwards
        if st.kind != "image" and lt - 1.0 * S <= st.top < top and st.bot > lt + 0.2 * S and st.bot <= lb + 0.6 * S:
            top = st.top
    for g in pg.glyphs:
        if family(g.font) in EXTRA_FAMILIES and lb - 1.6 * S <= g.base < lt + 0.2 * S and \
                any(abs(h.xc - g.xc) < 0.6 * S for h in L):
            top = min(top, g.top)
    body = min((g.size for g in L if g.ch.strip()), default=S)
    for h in L:   # the upper limit over a big ∫ / ∑ of the line
        if h.size >= 1.2 * body and family(h.font) in SYMBOL_FAMILIES and symbol_char(h.ch)[0] in ("∫", "∑", "∏"):
            for g in pg.glyphs:
                if g.size < 0.82 * body and h.x0 - 0.3 * body <= g.xc <= h.x1 + 0.6 * body and \
                        h.top - 1.0 * body <= g.base <= h.top + 0.3 * body:
                    top = min(top, g.top)
    return top


def is_heading(L: list[Glyph]) -> bool:
    """A bold line of several words that is neither a question label nor a solution marker: a section heading
    or the shared passage of the next questions ("Sử dụng thông tin dưới đây…")."""
    letters = [g for g in L if g.ch.isalpha()]
    t = text_of(L).strip()
    if not letters or not all(is_bold(g.font) for g in letters) or SOLUTION_RE.match(t):
        return False
    if len(letters) >= 5 and t == t.upper() and len(t.split()) >= 2:
        return True   # "LUYỆN TẬP", "DẠNG 2: …"
    return len(letters) >= 12 and len(t.split()) >= 4


def body_size(glyphs: list[Glyph]) -> float:
    sz = [round(g.size, 1) for g in glyphs if g.ch.isalpha() and family(g.font) in TEXT_FAMILIES]
    if not sz:
        raise Unresolved("no text in the question")
    return statistics.mode(sz)


def layout_question(pages: dict[int, Page], first: int, number: int) -> Result:
    res = Result()
    try:
        bands = region(pages, first, number)
        all_lines: list[Line] = []
        for pno, top, bot in bands:
            pg = pages[pno]
            gl = [g for g in body(pg) if top <= (g.top + g.bot) / 2 <= bot]
            if not gl:
                continue
            S = body_size(gl)
            # pieces of a big delimiter continue upwards out of the band: the whole delimiter is the question's
            pieces = [g for g in gl if is_piece(g)]
            grown = True
            while grown:
                grown = False
                for g in pg.glyphs:
                    if g in gl or not is_piece(g) or not g.upright:
                        continue
                    if any(abs(g.x0 - h.x0) < 1.5 and -0.6 * S < h.top - g.bot < 0.6 * S and g.top < h.top
                           for h in pieces):
                        gl.append(g)
                        pieces.append(g)
                        top = min(top, g.top - 0.5)
                        grown = True
            # numerators of a fraction on the next question's first line, denominators of the previous one's last
            # line reach into the band: they belong to those lines
            foreign = []
            for st in pg.strokes:
                if st.kind != "hline":
                    continue
                # (only glyphs that straddle the band's edge: everything well inside the band is this question's)
                if bot < st.y <= bot + 1.2 * S:
                    foreign += [g for g in gl if st.x0 - 1 <= g.xc <= st.x1 + 1 and st.y - 1.8 * S <= g.base < st.y
                                and g.base > bot - 0.6 * S]
                if top - 1.2 * S <= st.y < top:
                    foreign += [g for g in gl if st.x0 - 1 <= g.xc <= st.x1 + 1 and st.y < g.top and g.base <= st.y + 2.4 * S
                                and g.base < top + 0.6 * S]
            if foreign:
                gl = [g for g in gl if g not in foreign]
                res.boxes += [(pno, g.x0, g.top, g.x1, g.bot) for g in foreign]
            atoms = [atom_of(g) for g in gl]
            band = (top, bot)
            atoms = build_accents(atoms, S)
            atoms = build_pieces(atoms, S)
            atoms = build_bars(pg, band, atoms, S)
            build_underlines(pg, band, atoms, S)
            atoms = build_overlines(pg, band, atoms, S)
            atoms += slashes(pg, band, S)   # after the radicals: their rising stroke is not a slash
            if any(a.kind == "sqrt" for a in atoms):
                raise Unresolved("radical sign without its overline")
            lines = build_systems(make_lines(atoms, S), S)
            for L in lines:
                L.page = pno
            all_lines += lines
            # ledger: every glyph of the band ends up on a line (in a token or a rebuilt structure)
            placed = {id(x) for L in lines for a in L.atoms for x in a.glyphs}
            lost = [g for g in gl if not g.ch.isspace() and id(g) not in placed]
            if lost:
                raise Unresolved(f"glyph lost while rebuilding: {lost[0].ch!r} on page {pno}")
            loose = [st for st in pg.strokes if not st.used and st.dark and st.kind != "image" and
                     top <= st.y <= bot]

            if loose:
                st = loose[0]
                raise Unresolved(f"{len(loose)} unexplained drawing(s) on page {pno} ({st.kind} "
                                 f"{st.x0:.0f}-{st.x1:.0f}@{st.y:.0f})")
            for a in atoms:
                for g in a.glyphs:
                    g.used = True
                    res.boxes.append((pno, *ink_box(g)))
            for s in pg.strokes:
                if s.used:
                    res.boxes.append((pno, s.x0, s.top, s.x1, s.bot))
            res.pages.append(pno)
            res.bands[pno] = (top, bot)
            res.stats["size"] = S
        if "size" not in res.stats:
            raise Unresolved("no text in the question's region")
        res.content, res.answer_in_solution = structure(all_lines, number, res.stats["size"])
        res.tex = all_tex(res.content)
        for t in res.tex:
            garbled(t)
    except Unresolved as ex:
        res.content, res.reason = None, str(ex)
    return res


GARBLED = re.compile(r"\\d?frac\{[=<>+\-]\}|\\d?frac\{[^{}]*\}\{[=<>]\}|\^\{-\^|[\^_]\{(?:=|\\le|\\ge|<|>)|"
                     r"[\^_]\{[()\[\]]\}|\{\}_\{[()]")


def garbled(tex: str):
    """Structures no real formula has (a fraction over a lone "=", a script that is a lone bracket or starts with a
    relation, a script on a bare minus sign): a rebuild that tore an expression apart."""
    m = GARBLED.search(tex)
    if m:
        raise Unresolved(f"garbled structure {m.group(0)!r} in a rebuilt formula")


def structure(lines: list[Line], number: int, S: float):
    """Lines → stem / options A–D / solution."""
    if not lines:
        raise Unresolved("empty question")
    strip_label(lines[0], number)
    lines = [L for L in lines if any(a.kind != "sp" for a in L.atoms)]
    if not lines:
        raise Unresolved("empty question")
    nodes = [(L, line_nodes(L, S)) for L in lines]
    stem, opts, sol = [], {}, []
    zone, cur = "stem", None
    for L, ns in nodes:
        t = plain_text(ns)
        if zone != "sol" and SOLUTION_RE.match(t) and (zone == "opts" and len(opts) == 4):
            zone = "sol"
        if zone == "sol":
            sol.append((L, ns))
            continue
        segs = split_options(ns)
        if segs is None:
            if zone == "opts":
                if len(opts) == 4:
                    raise Unresolved("unexpected line after the options")
                opts[cur].append((L, ns))
            else:
                stem.append((L, ns))
            continue
        lead, parts = segs
        if lead:
            if zone == "opts":
                opts[cur].append((L, lead))
            else:
                stem.append((L, lead))
        for lab_, content in parts:
            if lab_ in opts or (opts and "ABCD".index(lab_) != len(opts)) or (not opts and lab_ != "A"):
                raise Unresolved(f"option {lab_} out of order")
            opts[lab_] = [(L, content)]
            cur = lab_
        zone = "opts"
    if sorted(opts) != list("ABCD"):
        raise Unresolved("options not exactly A-D")
    # the text's right margin, per page, over the whole question (a solution of short lines never "runs to the margin")
    right: dict[int, float] = {}
    for L, _ in nodes:
        right[L.page] = max(right.get(L.page, 0.0), L.x1)
    content = {"stem": paragraphs(stem, S, right), "options": [{"label": k, "content": paragraphs(opts[k], S, right)}
                                                         for k in "ABCD"]}
    ans = None
    sol_blocks = []
    for L, ns in sol:
        t = plain_text(ns).strip()
        if re.fullmatch(r"(?:Lời\s*giải|Hướng\s*dẫn\s*giải|Hướng\s*dẫn|Giải)\s*[:.]?", t, re.I):
            continue
        m = ANSWER_RE.search(t)
        if m and ans is None:
            ans = m.group(1)
        sol_blocks.append((L, ns))
    content["solution"] = paragraphs(sol_blocks, S, right) or None
    if any(not o["content"] for o in content["options"]):
        raise Unresolved("empty option")
    if not content["stem"]:
        raise Unresolved("empty stem")
    return content, ans


def strip_label(L: Line, number: int):
    """Remove the atoms of "Câu N." / "Câu N:" from the start of the first line."""
    atoms = sorted(L.atoms, key=lambda a: a.x0)
    txt, cut = "", None
    for i, a in enumerate(atoms):
        txt += " " if a.kind == "sp" else a.val
        m = re.fullmatch(r"\s*(?:Câu|Bài)\s*(\d+)\s*([.:])?", txt)
        if m and int(m.group(1)) == number:
            cut = i
            if m.group(2):
                break
        elif cut is not None or len(txt) > 14:
            break
    if cut is None:
        raise Unresolved("question label not at the start of its line")
    L.atoms = atoms[cut + 1:]


def plain_text(ns: list[dict]) -> str:
    return "".join(n.get("v", "") if n["t"] == "s" else "\x00" for n in ns)


def strip_prefix(ns: list[dict], pat: str) -> list[dict]:
    if ns and ns[0]["t"] == "s":
        v = re.sub("^" + pat if not pat.startswith("^") else pat, "", ns[0]["v"], count=1)
        ns = ([dict(ns[0], v=v)] if v.strip() else []) + ns[1:]
    return tidy(ns)


OPT_RE = re.compile(r"(?:^|\s)([A-D])\s*[.:)]\s*")


def split_options(ns: list[dict]):
    """(lead, [(label, nodes)]) when the line holds option labels "A." … (in text, bold or not); else None."""
    hits = []
    for k, n in enumerate(ns):
        if n["t"] != "s":
            continue
        for m in OPT_RE.finditer(n["v"]):
            # a label starts the line or follows a wide gap/another option, never mid-sentence
            before = n["v"][:m.start(1)]
            if before.strip() and not (k == 0 and not before.strip()):
                prev_ok = re.search(r"\s{2,}$", before) is not None
                if not prev_ok and not hits:
                    continue
            hits.append((k, m))
    if not hits:
        return None
    labels = [m.group(1) for _, m in hits]
    if labels != sorted(labels) or len(set(labels)) != len(labels):
        return None
    k0, m0 = hits[0]
    if k0 != 0 or ns[0]["v"][:m0.start(1)].strip():
        return None
    out, lead = [], []
    for i, (k, m) in enumerate(hits):
        k_end, m_end = hits[i + 1] if i + 1 < len(hits) else (len(ns), None)
        seg = []
        first = ns[k]["v"][m.end():]
        if k == k_end and m_end is not None:
            seg.append({"t": "s", "v": ns[k]["v"][m.end():m_end.start(1)]})
        else:
            if first:
                seg.append(dict(ns[k], v=first))
            seg += [dict(x) for x in ns[k + 1:k_end]]
            if m_end is not None:
                seg.append(dict(ns[k_end], v=ns[k_end]["v"][:m_end.start(1)]))
        out.append((m.group(1), tidy(seg)))
    return lead, out


def paragraphs(items: list[tuple[Line, list[dict]]], S: float, margins: dict[int, float] | None = None) -> list[dict]:
    """Lines → paragraphs: a line that runs to the right margin (of its page's question text) continues on the next."""
    if not items:
        return []
    own = max(L.x1 for L, _ in items)
    out = []
    prev = None
    for L, ns in items:
        if not ns:
            continue
        right = max(own, (margins or {}).get(prev.page, 0.0)) if prev is not None else own
        if out and prev is not None and prev.x1 >= right - 1.5 * S and prev.page == L.page:
            out[-1]["c"] = tidy(out[-1]["c"] + [{"t": "s", "v": " "}] + [dict(n) for n in ns])
        else:
            block = {"t": "p", "c": [dict(n) for n in ns]}
            if len(ns) == 1 and ns[0]["t"] == "m":
                block["display"] = True
            out.append(block)
        prev = L
    for b in out:
        if b.get("display") and not (len(b["c"]) == 1 and b["c"][0]["t"] == "m"):
            b.pop("display")
    return out


# ------------------------------------------------------------------------------------------------
# checks
# ------------------------------------------------------------------------------------------------
_NOTEXT: dict = {}


def render_without_text(pdf_path: str, pno: int, dpi: int):
    """The page rendered with every text object removed (also inside form XObjects: MathType equations): what
    remains is drawings and images. Grayscale PIL image, cached per page."""
    key = (str(pdf_path), pno, dpi)
    if key in _NOTEXT:
        return _NOTEXT[key]
    import subprocess
    import tempfile

    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ContentStream, NameObject

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    writer.add_page(reader.pages[pno - 1])
    page = writer.pages[0]
    seen = set()

    def strip(cs: ContentStream) -> ContentStream:
        ops, depth = [], 0
        for operands, op in cs.operations:
            if op == b"BT":
                depth += 1
                continue
            if op == b"ET":
                depth = max(0, depth - 1)
                continue
            if depth == 0:
                ops.append((operands, op))
        cs.operations = ops
        return cs

    def forms(res):
        res = res.get_object() if res is not None else None
        if not res or "/XObject" not in res:
            return
        for _, ref in res["/XObject"].get_object().items():
            xo = ref.get_object()
            if id(xo) in seen or xo.get("/Subtype") != "/Form":
                continue
            seen.add(id(xo))
            cs = strip(ContentStream(xo, reader))
            xo.set_data(cs.get_data())
            forms(xo.get("/Resources"))

    contents = page.get_contents()
    if contents is not None:
        page[NameObject("/Contents")] = writer._add_object(strip(ContentStream(contents, reader)))
    forms(page.get("/Resources"))
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/p.pdf", "wb") as f:
            writer.write(f)
        r = subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-singlefile", f"{tmp}/p.pdf", f"{tmp}/p"],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            raise Unresolved("page could not be rendered without its text")
        im = Image.open(f"{tmp}/p.png").convert("L")
        im.load()
    if len(_NOTEXT) > 8:
        _NOTEXT.clear()
    _NOTEXT[key] = im
    return im


def drawing_check(pdf_path, res: Result, pages: dict[int, Page], dpi: int = 150, limit: int = 6,
                  dark: int = 160) -> str | None:
    """Non-text ink (drawings, images) in the question's bands must lie on explained strokes (fraction bars,
    radicals, slashes, underlines, page furniture). Light ink (watermarks, highlights) is ignored."""
    s = dpi / 72.0
    for pno in res.pages:
        pg = pages[pno]
        top, bot = res.bands[pno]
        im = render_without_text(pdf_path, pno, dpi)
        px = im.load()
        w, h = im.size
        y0, y1 = max(0, int(top * s)), min(h, int(bot * s) + 1)
        mask = set()
        for st in pg.strokes:
            if st.used and st.bot >= top - 2 and st.top <= bot + 2:
                for yy in range(max(y0, int((st.top - 1.2) * s)), min(y1, int((st.bot + 1.2) * s) + 1)):
                    for xx in range(max(0, int((st.x0 - 1.2) * s)), min(w, int((st.x1 + 1.2) * s) + 1)):
                        mask.add((xx, yy))
        stray = 0
        for yy in range(y0, y1):
            for xx in range(w):
                if px[xx, yy] < dark and (xx, yy) not in mask:
                    stray += 1
        if stray > limit:
            return f"{stray} px of drawing/image ink on page {pno} not explained (figure, table or picture)"
    return None

X_HEIGHT = set("acemnorsuvwxzıαεικνοπστυω")
DESCENDERS = set("gjpqyfβγζημξρφχψ,;()[]{}|")


def ink_box(g: Glyph) -> tuple[float, float, float, float]:
    """Where a glyph's ink lies (the font box reaches far above small letters: an accent drawn there must not be
    taken for part of the letter)."""
    ch = unicodedata.normalize("NFD", g.ch)
    base_ch = ch[:1]
    if len(ch) > 1 or not base_ch.isascii() and base_ch.isalpha() and base_ch not in X_HEIGHT:
        top = g.base - 1.0 * g.size
    elif base_ch in X_HEIGHT:
        top = g.base - 0.52 * g.size
    elif base_ch.isascii() and base_ch.isalnum():
        top = g.base - 0.76 * g.size
    else:
        top = g.top
    top = max(top, g.top)
    bot = g.bot if (base_ch in DESCENDERS or not base_ch.isascii() or not base_ch.isalnum()) else g.base + 0.06 * g.size
    return g.x0, top, g.x1, bot
def ink_check(pdf_path, res: Result, pages: dict[int, Page], dpi: int = 150, limit: int = 10,
              dark: int = 150) -> str | None:
    """Render the question's bands and look for dark ink that no used glyph or stroke explains (figures,
    tables, glyphs the text layer does not carry). Light ink (watermarks, highlight backgrounds) is ignored."""
    import subprocess
    import tempfile

    from PIL import Image

    s = dpi / 72.0
    for pno in res.pages:
        pg = pages[pno]
        top, bot = res.bands[pno]
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

        def cover(x0, y0, x1, y1, pad=1.2):
            a0, a1 = max(0, int((x0 - pad) * s)), min(w, int((x1 + pad) * s) + 1)
            b0, b1 = max(0, int((y0 - top - pad) * s)), min(h, int((y1 - top + pad) * s) + 1)
            for yy in range(b0, b1):
                mask[yy * w + a0:yy * w + a1] = b"\x01" * (a1 - a0)

        for b in res.boxes:
            if b[0] == pno:
                cover(b[1], b[2], b[3], b[4])
        for g in pg.glyphs:   # rotated watermark text; neighbours' glyphs reaching into the band
            if not g.upright or (not g.used and (g.bot < top + 3 or g.top > bot - 3)):
                cover(g.x0, g.top, g.x1, g.bot, pad=2)
        stray = 0
        pts = []
        for yy in range(h):
            row = yy * w
            for xx in range(w):
                if not mask[row + xx] and px[xx, yy] < dark:
                    stray += 1
                    if len(pts) < 400:
                        pts.append((xx / s, top + yy / s))
        res.stats.setdefault("stray", {})[pno] = pts
        if stray > limit:
            return f"{stray} px of ink on page {pno} not explained by the text layer (figure/table/drawing)"
    return None
