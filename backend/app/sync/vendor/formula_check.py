# Vendored verbatim from HSA-question-bank scripts/hsa/formula_check.py (upstream commit 8628c79).
# Keep in sync with upstream; do not edit here except to re-vendor.
"""Automated formula validation against the source's own rendering.

A MathType OLE object carries a WMF preview in which Word/MathType drew every glyph
of the equation with ExtTextOut records (Times New Roman letters/digits, Symbol-font
Greek and operators). Comparing the multiset of alphanumeric/Greek glyphs drawn in the
preview with the glyphs implied by the extracted LaTeX detects dropped, extra or wrong
symbols, exponents, indices and function names.

Statuses (editorial layer; canonical data is never modified):
  ORIGINAL_STRUCTURED      structured source (OMML) converted without issues, no preview to compare
  EXTRACTED_VALIDATED      structured source (MTEF) whose LaTeX glyphs match the source preview
  RECONSTRUCTED_VALIDATED  retyped after visual comparison with the source (editorial overlay)
  NEEDS_FORMULA_REVIEW     conversion issues, glyph mismatch, or no LaTeX
Structural layout (fraction bars, radical signs, brace heights) is not visible as glyphs;
those parts are covered by the MTEF template parse, not by this check.
"""
import collections
import re
import struct

SYMBOL_FONT = {
    "a": "α", "b": "β", "g": "γ", "d": "δ", "e": "ε", "z": "ζ", "h": "η", "q": "θ", "i": "ι", "k": "κ", "l": "λ",
    "m": "μ", "n": "ν", "x": "ξ", "o": "ο", "p": "π", "r": "ρ", "s": "σ", "t": "τ", "u": "υ", "f": "φ", "j": "φ",
    "c": "χ", "y": "ψ", "w": "ω", "G": "Γ", "D": "Δ", "Q": "Θ", "L": "Λ", "X": "Ξ", "P": "Π", "S": "Σ", "U": "Υ",
    "F": "Φ", "Y": "Ψ", "W": "Ω", "v": "ϖ", "J": "ϑ",
}
GREEK_CMD = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
             "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
             "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ρ", "sigma": "σ", "varsigma": "σ", "tau": "τ",
             "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
             "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
             "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω"}
FUNC_CMD = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max", "min", "sup", "inf",
            "det", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth", "gcd", "deg", "dim", "ker", "arg"}


GREEK = set("αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩϖϑ")
VN_LETTERS = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
                 "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ")


def _keep(ch):
    return len(ch) == 1 and (("0" <= ch <= "9") or ("a" <= ch.lower() <= "z") or ch in GREEK or ch in VN_LETTERS)


CHARSET_CP = {0: "cp1252", 1: "cp1252", 161: "cp1253", 163: "cp1258", 238: "cp1250", 204: "cp1251",
              162: "cp1254", 186: "cp1257", 177: "cp1255", 178: "cp1256", 222: "cp874"}


def wmf_glyphs(data):
    """Counter of alphanumeric/Greek glyphs drawn by text records in a (placeable) WMF."""
    off = 22 if data[:4] == b"\xd7\xcd\xc6\x9a" else 0
    if len(data) < off + 18:
        return None
    hdr_words = struct.unpack_from("<H", data, off + 2)[0]
    i = off + hdr_words * 2
    objs, cur_font, cur_cs = {}, "", 0
    free_slots = []
    next_slot = 0
    out = collections.Counter()
    n_text = 0
    while i + 6 <= len(data):
        size, func = struct.unpack_from("<IH", data, i)
        if size < 3:
            break
        body = data[i + 6:i + size * 2]
        i += size * 2
        if func in (0x02FB, 0x02FC, 0x00F7, 0x02FA, 0x06FF, 0x0142, 0x01F9):  # all object-creating records
            slot = min(free_slots) if free_slots else next_slot
            if free_slots:
                free_slots.remove(slot)
            else:
                next_slot += 1
            if func == 0x02FB and len(body) >= 18:
                objs[slot] = (body[18:50].split(b"\0")[0].decode("latin-1"), body[13])  # (face name, LOGFONT charset)
            else:
                objs[slot] = None
        elif func == 0x012D and len(body) >= 2:
            slot = struct.unpack_from("<H", body)[0]
            if objs.get(slot) is not None:
                cur_font, cur_cs = objs[slot]
        elif func == 0x01F0 and len(body) >= 2:  # DeleteObject
            slot = struct.unpack_from("<H", body)[0]
            objs.pop(slot, None)
            free_slots.append(slot)
        elif func in (0x0A32, 0x0521):
            if func == 0x0A32:
                if len(body) < 8:
                    continue
                _, _, cnt, opt = struct.unpack_from("<hhhH", body)
                p = 8 + (8 if opt & 0x0006 else 0)
                raw = body[p:p + cnt]
            else:
                cnt = struct.unpack_from("<h", body)[0]
                raw = body[2:2 + cnt]
            n_text += 1
            f = cur_font.lower()
            for b in raw:
                ch = chr(b)
                if f.startswith("symbol"):
                    ch = SYMBOL_FONT.get(ch, "")
                elif f.startswith("mt extra") or f.startswith("wingdings"):
                    continue
                else:
                    ch = bytes([b]).decode(CHARSET_CP.get(cur_cs, "cp1252"), "replace")
                if _keep(ch):
                    out[ch] += 1
    return out if n_text else None


def latex_glyphs(latex):
    s = latex
    s = re.sub(r"\\(?:begin|end)\{[a-z*]+\}(?:\{[lcr|]*\})?", " ", s)
    s = re.sub(r"\\(?:left|right)\s*\.", " ", s)
    s = re.sub(r"\\(?:text|mathrm|operatorname|mathbf|mathit|textrm)\{([^{}]*)\}", r" \1 ", s)
    s = re.sub(r"\\mathbb\{(.)\}", r"\1", s)
    out = collections.Counter()

    def cmd(m):
        name = m.group(1)
        if name in GREEK_CMD:
            return " " + GREEK_CMD[name] + " "
        if name in FUNC_CMD:
            return " " + name + " "
        return " "
    s = re.sub(r"\\([A-Za-z]+)", cmd, s)
    s = re.sub(r"\\.", " ", s)
    for ch in s:
        if _keep(ch):
            out[ch] += 1
    return out


def check(latex, wmf_bytes):
    """Return (status_hint, detail). status_hint: 'match' | 'mismatch' | 'no_preview_text'."""
    if not latex:
        return "no_latex", {}
    w = wmf_glyphs(wmf_bytes) if wmf_bytes else None
    if w is None:
        return "no_preview_text", {}
    l = latex_glyphs(latex)
    # MathType draws double-struck letters (ℝ, ℕ, ℤ ...) by overprinting: tolerate their counts
    for ch in re.findall(r"\\mathbb\{(.)\}", latex):
        w.pop(ch, None)
        l.pop(ch, None)
    if w == l:
        return "match", {}
    missing = dict(w - l)
    extra = dict(l - w)
    return "mismatch", {"in_source_not_latex": missing, "in_latex_not_source": extra}


def formula_status(f, wmf_bytes, texmath_ok=True, override=None):
    """Editorial status for one formula record."""
    if override and override.get("status") == "RECONSTRUCTED_VALIDATED":
        return "RECONSTRUCTED_VALIDATED", {"override": True}
    if not f.get("latex") or not texmath_ok:
        return "NEEDS_FORMULA_REVIEW", {"reason": "no LaTeX" if not f.get("latex") else "LaTeX not convertible to OMML"}
    if f.get("latex_confidence", 0) < 0.9 or f.get("issues"):
        return "NEEDS_FORMULA_REVIEW", {"reason": "converter issues", "issues": f.get("issues")}
    if f["source_format"] == "omml":
        return "ORIGINAL_STRUCTURED", {}
    hint, detail = check(f["latex"], wmf_bytes)
    if hint == "match":
        return "EXTRACTED_VALIDATED", {}
    if hint == "no_preview_text":
        return "ORIGINAL_STRUCTURED", {"reason": "preview has no text records to compare"}
    return "NEEDS_FORMULA_REVIEW", {"reason": "glyph mismatch vs source preview", **detail}
