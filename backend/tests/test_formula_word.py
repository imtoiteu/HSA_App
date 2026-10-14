"""Formula reconstruction from Word/MathType PDF layout (review/formula_word): synthetic pages, no PDF needed."""
from app.review import formula_word as W

S = 12.0
TNR, TNRI, TNRB, SYM, MTX = "TimesNewRoman", "TimesNewRoman,Italic", "TimesNewRoman,Bold", "Symbol", "MTExtra"


def g(ch, font, x, base, size=S, w=None):
    w = w if w is not None else 0.5 * size
    return W.Glyph(ch, font, size, x, x + w, base - 0.75 * size, base + 0.25 * size, base)


def text(s, font, x, base, size=S):
    out = []
    for ch in s:
        if ch == " ":
            out.append(g(" ", font, x, base, size, w=0.25 * size))
            x += 0.25 * size
            continue
        out.append(g(ch, font, x, base, size))
        x += 0.5 * size
    return out, x


def hline(x0, x1, y):
    return W.Stroke("hline", x0, x1, y, y)


def question(stem, strokes=(), options=None, solution=(), number=1, base=100):
    gl, _ = text(f"Câu {number}.", TNRB, 40, base)
    gl += stem
    ob = base + 30
    for k, lab in enumerate("ABCD"):
        gl += text(lab + ".", TNRB, 50 + 120 * k, ob)[0]
        gl += (options or {}).get(lab) or text(str(k + 1), TNR, 66 + 120 * k, ob)[0]
    gl += list(solution)
    gl += text(f"Câu {number + 1}.", TNRB, 40, base + 120)[0]
    pg = W.Page(1, 600, 840, gl, list(strokes))
    return W.layout_question({1: pg}, 1, number)


def math_nodes(res, part="stem"):
    return [n["tex"] for b in res.content[part] for n in b["c"] if n["t"] == "m"]


def test_symbol_font_operators_and_italic_variables():
    stem, x = text("Cho ", TNR, 90, 100)
    stem += [g("x", TNRI, x, 100), g("", SYM, x + 7, 100, w=7), g("0", TNR, x + 15, 100)]
    res = question(stem)
    assert res.reason == ""
    assert math_nodes(res) == [r"x\ge0"]


def test_superscript_by_size_and_baseline():
    stem, x = text("Cho ", TNR, 90, 100)
    stem += [g("x", TNRI, x, 100), g("2", TNR, x + 6.5, 95, size=7)]
    res = question(stem)
    assert res.reason == "" and math_nodes(res) == ["x^2"]


def test_fraction_bar_with_numerator_and_denominator():
    stem, x = text("Tính ", TNR, 90, 100)
    x += 4
    stem += [g("1", TNR, x + 1, 92.4), g("2", TNR, x + 1, 109)]   # MathType: numerator ~0.38 em above the bar
    res = question(stem, strokes=[hline(x, x + 8, 97)])
    assert res.reason == "" and math_nodes(res) == [r"\dfrac{1}{2}"]


def test_underline_under_a_label_is_formatting_not_a_fraction():
    stem, x = text("Đề bài", TNR, 90, 100)
    # option B is underlined (the answer); the next line's text sits right under the stroke
    res = question(stem, strokes=[hline(170, 176, 131.5)])
    assert res.reason == ""
    assert [o["label"] for o in res.content["options"]] == list("ABCD")


def test_prescripts_of_a_nuclide():
    stem, x = text("Phản ứng ", TNR, 90, 100)
    stem += [g("1", TNR, x, 96, size=7), g("0", TNR, x, 103, size=7), g("n", TNR, x + 4.5, 100),
             g("", SYM, x + 12, 100), g("p", TNRI, x + 20, 100)]
    res = question(stem)
    assert res.reason == ""
    assert math_nodes(res) == [r"{}_0^1\mathrm{n}+p"]


def test_italic_vietnamese_word_is_text():
    stem, x = text("Đề", TNR, 90, 100)
    sol, _ = text("Hướng dẫn giải", "TimesNewRoman,BoldItalic", 50, 160)
    sol2, _ = text("Chọn B", TNRB, 50, 175)
    res = question(stem, solution=sol + sol2)
    assert res.reason == ""
    assert res.answer_in_solution == "B"
    assert all(n["t"] == "s" for b in res.content["solution"] for n in b["c"])


def test_mt_extra_arrow_over_a_letter():
    stem, x = text("Véc tơ ", TNR, 90, 100)
    stem += [g("", MTX, x, 90, w=5), g("u", TNRI, x, 100, w=5)]
    res = question(stem)
    assert res.reason == "" and math_nodes(res) == [r"\overrightarrow{u}"]


def test_drawn_slash_between_two_numbers():
    stem, x = text("Tỉ số ", TNR, 90, 100)
    stem += [g("2", TNR, x, 100), g("9", TNR, x + 10, 100)]
    slash = W.Stroke("line", x + 6.5, x + 9.5, 91, 102, rising=True)
    res = question(stem, strokes=[slash])
    assert res.reason == "" and res.content["stem"][0]["c"] == [{"t": "s", "v": "Tỉ số 2/9"}]


def test_unknown_symbol_is_refused():
    stem, x = text("Ký ", TNR, 90, 100)
    stem.append(g("", SYM, x, 100))
    res = question(stem)
    assert res.content is None and "unknown symbol" in res.reason


def test_unexplained_dark_stroke_is_refused():
    stem, _ = text("Bảng", TNR, 90, 100)
    res = question(stem, strokes=[W.Stroke("line", 300, 301, 95, 125)])
    assert res.content is None and "unexplained drawing" in res.reason


def test_symbol_code_given_as_latin1():
    # Euclid Symbol / SymbolMT report 8-bit codes as Latin-1 characters: '³' is the code of ≥, not a digit
    assert W.atom_of(g("³", "SymbolMT", 0, 100)).val == r"\ge"
    assert W.atom_of(g("", "Symbol", 0, 100)).val == r"\pi"


def test_a_row_of_exponents_near_the_page_top_is_not_a_page_number():
    stem, x = text("Cho ", TNR, 90, 60)
    stem += [g("x", TNRI, x, 60), g("2", TNR, x + 6.5, 55, size=7)]
    res = question(stem, base=60)
    assert res.reason == "" and math_nodes(res) == ["x^2"]
