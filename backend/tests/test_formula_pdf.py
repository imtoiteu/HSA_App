"""Formula reconstruction from PDF layout (review/formula_pdf): synthetic pages, no PDF needed."""
import json
import pytest

from app.review import formula_pdf as F

S = 12.0  # body size


def g(ch, font, x, base, size=S, w=None, code=-1):
    w = w if w is not None else 0.5 * size
    return F.Glyph(ch, code, font, size, x, x + w, base - 0.75 * size, base + 0.25 * size, base)


def word(text, font, x, base, size=S):
    out = []
    for ch in text:
        if ch == " ":
            x += 0.33 * size
            continue
        out.append(g(ch, font, x, base, size))
        x += 0.5 * size
    return out, x


def page(glyphs, rules=(), shapes=()):
    pg = F.Page(1, 600, 840, list(glyphs), [F.Rule(*r) for r in rules], list(shapes))
    pg.body_top, pg.body_bot = 30, 800
    return {1: pg}


def question(stem_glyphs, rules=(), label_n=1, base=100, opts=None):
    gl, x = word(f"Câu {label_n}.", "VNBX12", 40, base)
    gl += stem_glyphs
    ob = base + 20
    for k, lab in enumerate("ABCD"):
        gl += [g(lab, "VNBX12", 50 + 120 * k, ob), g(".", "VNR12", 56 + 120 * k, ob)]
        txt = (opts or {}).get(lab, str(k + 1))
        gl += word(txt, "CMR12", 66 + 120 * k, ob)[0]
    gl += word(f"Câu {label_n + 1}.", "VNBX12", 40, base + 60)[0]  # the next question ends the region
    return page(gl, rules)


def test_superscript_and_text():
    stem, x = word("Cho", "VNR12", 90, 100)
    stem += [g("x", "CMMI12", x + 5, 100), g("2", "CMR8", x + 11, 96, size=8)]
    res = F.layout_question(question(stem), 1, 1)
    assert res.reason == "" and res.content
    nodes = res.content["stem"][0]["c"]
    assert nodes[0] == {"t": "s", "v": "Cho "}
    assert nodes[1] == {"t": "m", "tex": "x^2"}
    assert [o["content"][0]["c"] for o in res.content["options"]][0] == [{"t": "m", "tex": "1"}]


def test_fraction_from_bar():
    stem, x = word("Tính", "VNR12", 90, 100)
    # 1/2 : numerator above the bar at y=97, denominator below
    stem += [g("1", "CMR12", x + 6, 92), g("2", "CMR12", x + 6, 106)]
    res = F.layout_question(question(stem, rules=[(x + 5, x + 13, 97)]), 1, 1)
    assert res.reason == ""
    assert {"t": "m", "tex": r"\dfrac{1}{2}"} in res.content["stem"][0]["c"]


def test_radical_hangs_from_its_overline():
    stem, x = word("Có", "VNR12", 90, 100)
    sq = g("√", "CMSY10", x + 5, 91, code=0x70)  # the sign's baseline is the overline's height
    stem += [sq, g("3", "CMR12", sq.x1, 100)]
    res = F.layout_question(question(stem, rules=[(sq.x1, sq.x1 + 6, 91)]), 1, 1)
    assert res.reason == ""
    assert {"t": "m", "tex": r"\sqrt{3}"} in res.content["stem"][0]["c"]


def test_unknown_stroke_is_refused():
    stem, x = word("Bảng", "VNR12", 90, 100)
    res = F.layout_question(question(stem, rules=[(100, 400, 150)]), 1, 1)
    assert res.content is None and "unexplained stroke" in res.reason


def test_unknown_symbol_is_refused():
    stem, x = word("Ký", "VNR12", 90, 100)
    stem.append(g("(cid:61)", "CMSY10", x + 5, 100, code=0x7F))
    res = F.layout_question(question(stem), 1, 1)
    assert res.content is None and "unknown symbol" in res.reason


def test_missing_option_is_refused():
    pages = question(word("Đề", "VNR12", 90, 100)[0])
    pages[1].glyphs = [x for x in pages[1].glyphs if not (x.ch == "D" and x.font == "VNBX12")]
    res = F.layout_question(pages, 1, 1)
    assert res.content is None


def test_every_glyph_used_once():
    stem, x = word("Cho", "VNR12", 90, 100)
    stem += [g("y", "CMMI12", x + 5, 100), g("=", "CMR12", x + 12, 100), g("x", "CMMI12", x + 20, 100),
             g("3", "CMR8", x + 26, 96, size=8)]
    res = F.layout_question(question(stem), 1, 1)
    assert res.reason == ""
    assert {"t": "m", "tex": "y=x^3"} in res.content["stem"][0]["c"]
    assert all(v == 1 for v in F._LEDGER.values())


def test_wrong_question_number_is_refused():
    res = F.layout_question(question(word("Đề", "VNR12", 90, 100)[0], label_n=7), 1, 9)
    assert res.content is None and "Câu 9." in res.reason


def test_same_question_score():
    a = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Cho hàm số y = x2 đồng biến"}]}], "options": []}
    b = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Cho hàm số "}, {"t": "m", "tex": "y=x^2"},
                                   {"t": "s", "v": " đồng biến"}]}], "options": []}
    assert F.same_question(a, b) == 1.0
    c = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Tính thể tích khối nón"}]}], "options": []}
    assert F.same_question(a, c) < 0.5


@pytest.mark.parametrize("t,expected", [("x", "x"), (r"\min", r"{\min}"), ("ab", "{ab}")])
def test_script_grouping(t, expected):
    assert F.grp(t) == expected


def test_each_vector_arrow_covers_its_own_letter():
    stem, x = word("Cho", "VNR12", 90, 100)
    x += 5
    # i⃗ and k⃗ side by side: each arrow is drawn from its own tail '#' to its head '»'
    for letter in "ik":
        stem += [g("#", "TeX-vect10", x, 92, w=2), g("»", "TeX-vect10", x + 2, 92, w=3),
                 g(letter, "CMMI12", x, 100, w=5)]
        x += 9
    res = F.layout_question(question(stem), 1, 1)
    assert res.reason == ""
    assert {"t": "m", "tex": r"\overrightarrow{i}\overrightarrow{k}"} in res.content["stem"][0]["c"]


def test_denominator_ignores_scripts_of_another_row():
    stem, x = word("Tính", "VNR12", 90, 100)
    # 1/2 whose bar sits at y=97; a superscript '3' of the NEXT line lies under the bar's span, far below
    stem += [g("1", "CMR12", x + 6, 92), g("2", "CMR12", x + 6, 106), g("3", "CMR8", x + 12, 112, size=8)]
    res = F.layout_question(question(stem, rules=[(x + 5, x + 13, 97)]), 1, 1)
    assert res.content is None or {"t": "m", "tex": r"\dfrac{1}{2}"} in res.content["stem"][0]["c"]
    if res.content is not None:
        assert "2_3" not in json.dumps(res.content) and "2^3" not in json.dumps(res.content)


def test_slash_over_relation_is_its_negation():
    stem, x = word("Có", "VNR12", 90, 100)
    stem += [g("x", "CMMI12", x + 5, 100), g("∈", "CMSY10", x + 12, 100, w=8, code=0x32),
             g("/", "CMMI12", x + 14, 100, w=5), g("A", "CMMI12", x + 22, 100)]
    res = F.layout_question(question(stem), 1, 1)
    assert res.reason == ""
    assert {"t": "m", "tex": r"x\notin A"} in res.content["stem"][0]["c"]


def test_script_fraction_ignores_a_script_of_the_line_above():
    stem, x = word("Có", "VNR12", 90, 100)
    x += 5
    # line 1: x₅ (subscript at script size); line 2 right below: y_{1/5}, a script-size fraction whose numerator '1'
    # sits just under that subscript '5'
    stem += [g("x", "CMMI12", x, 100), g("5", "CMR8", x + 6, 103, size=8)]
    stem += word("và", "VNR12", 90, 112)[0]
    stem += [g("y", "CMMI12", x, 112), g("1", "CMR5", x + 6.5, 110, size=5), g("5", "CMR5", x + 6.5, 115.5, size=5)]
    res = F.layout_question(question(stem, rules=[(x + 6, x + 9.5, 111.3)]), 1, 1)
    assert res.reason == ""
    tex = [n["tex"] for b in res.content["stem"] for n in b["c"] if n["t"] == "m"]
    assert "x_5" in tex and r"y_{\frac{1}{5}}" in tex


def drawn_page(glyphs, boxes):
    """A page whose rendered image (72 dpi: one pixel per point) has ink exactly in `boxes`."""
    from PIL import Image, ImageDraw
    pages = page(glyphs, shapes=[F.Shape("LTCurve", *b) for b in boxes])
    im = Image.new("L", (600, 840), 255)
    d = ImageDraw.Draw(im)
    for x0, x1, top, bot in boxes:
        d.rectangle([x0, top, x1, bot], outline=0)
    for gl in glyphs:
        d.rectangle([gl.x0 + 1, gl.top + 2, gl.x1 - 1, gl.bot - 2], fill=0)
    pages[1]._img = (im, 1.0)
    return pages


def figure_question(fig_top, fig_bot, extra=()):
    gl, x = word("Câu 1.", "VNBX12", 40, 100)
    gl += word("Cho đồ thị như hình", "VNR12", 90, 100)[0]
    ob = fig_bot + 30
    for k, lab in enumerate("ABCD"):
        gl += [g(lab, "VNBX12", 50 + 120 * k, ob), g(".", "VNR12", 56 + 120 * k, ob)]
        gl += word(str(k + 1), "CMR12", 66 + 120 * k, ob)[0]
    gl += word("Câu 2.", "VNBX12", 40, ob + 60)[0]
    return gl + list(extra)


def test_figure_mode_cuts_the_drawing_into_the_stem():
    gl = figure_question(120, 220)
    pages = drawn_page(gl, [(200, 320, 120, 220)])
    assert F.layout_question(pages, 1, 1).content is None  # without figure mode a drawing is unexplained
    pages = drawn_page(gl, [(200, 320, 120, 220)])
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.reason == "" and len(res.figures) == 1
    x0, y0, x1, y1 = res.figures[0]["box"]
    assert x0 <= 201 and x1 >= 319 and y0 <= 121 and y1 >= 219
    assert [b["t"] for b in res.content["stem"]] == ["p", "img"]
    assert all(b["t"] == "p" for o in res.content["options"] for b in o["content"])


def test_figure_box_leaves_out_the_end_of_solution_mark():
    qed = g("(cid:3)", "MSAM10", 330, 225, code=0x03)  # the □ ending the solution, next to the figure's corner
    lab = g("x", "CMMI12", 300, 214)                  # a label inside the figure
    pg = drawn_page([lab, qed], [(200, 320, 120, 220)])[1]
    figs = F.find_figures(pg, [[lab], [qed]], 110, 240, S)
    assert len(figs) == 1 and figs[0]["labels"] == [lab]
    assert figs[0]["box"][2] < qed.x0 and figs[0]["box"][3] < qed.bot


def test_a_table_rule_near_the_top_is_no_page_header():
    pg = F.Page(1, 600, 840, [], [F.Rule(40, 560, 26), F.Rule(90, 500, 65), F.Rule(90, 500, 85)], [])
    F.frame(pg)
    assert pg.body_top == 26  # the book's header rule (0.87 of the page), not the table's rules (0.68)


def test_figure_box_stops_short_of_the_line_below():
    gl = figure_question(120, 220)
    opt_top = min(x.top for x in gl if x.ch == "A" and x.font == "VNBX12")
    pages = drawn_page(gl, [(200, 320, 120, opt_top + 2)])  # the drawing's axis reaches into the options line
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.reason == "" and res.figures[0]["box"][3] < opt_top


def test_text_words_are_no_figure_labels():
    words = word("trên khoảng", "VNR12", 210, 222)[0]  # the end of a sentence inside the drawing's box
    gl = figure_question(120, 220, extra=words)
    pages = drawn_page(gl, [(200, 320, 120, 220)])
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.content is None and "text inside a figure" in res.reason


def bracket_system(rows, x=150, top_base=228.0):
    """A '[' assembled from cmex pieces (top, extensions, bottom) like pdfTeX sets it, rows of math after it."""
    n = len(rows)
    bases = [top_base] + [top_base + 21 + 7 * k for k in range(2 * n - 4)] + [top_base + 35 + 7 * (2 * n - 6)]
    codes = [0x32] + [0x36] * (len(bases) - 2) + [0x34]
    gl = [g("", "CMEX10", x, b, code=c, w=8) for b, c in zip(bases, codes)]
    for k, row in enumerate(rows):  # rows 21 pt apart, the first one level with the bracket's top serif + 1 ex
        xx = x + 12
        for ch in row:
            gl.append(g(ch, "CMMI12" if ch.isalpha() else "CMR12", xx, top_base + 11 + 21 * k))
            xx += 7
    return gl


def test_three_row_bracket_keeps_its_last_row():
    stem, x = word("Ta có", "VNR12", 90, 249)
    sysg = bracket_system(["x<1", "x=2", "x>4"], x=x + 10, top_base=228)
    gl, _ = word("Câu 1.", "VNBX12", 40, 100)
    gl += word("Giải", "VNR12", 90, 100)[0] + stem + sysg
    ob = 320
    for k, lab in enumerate("ABCD"):
        gl += [g(lab, "VNBX12", 50 + 120 * k, ob), g(".", "VNR12", 56 + 120 * k, ob)]
        gl += word(str(k + 1), "CMR12", 66 + 120 * k, ob)[0]
    gl += word("Câu 2.", "VNBX12", 40, ob + 60)[0]
    res = F.layout_question(page(gl), 1, 1)
    assert res.reason == "", res.reason
    tex = [n["tex"] for b in res.content["stem"] for n in b["c"] if n["t"] == "m"]
    assert r"\left[\begin{aligned} x<1 \\ x=2 \\ x>4 \end{aligned}\right." in tex


def test_rotated_label_beside_a_drawing_is_in_its_box():
    five = F.Glyph("5", -1, "VNR10", 10, 182, 192, 160, 172, 170, upright=False)  # "5 cm" along the side
    stamp = F.Glyph("T", -1, "Times-Roman", 10, 330, 339, 150, 160, 158, upright=False)  # the page's watermark
    pg = drawn_page([], [(200, 320, 120, 220)])[1]
    pg.glyphs += [five, stamp]
    figs = F.find_figures(pg, [], 110, 240, S)
    assert figs[0]["box"][0] <= five.x0 and figs[0]["box"][2] < stamp.x0


def test_figures_side_by_side_are_refused():
    gl = figure_question(120, 220)
    pages = drawn_page(gl, [(100, 220, 120, 220), (300, 420, 125, 215)])
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.content is None and "side by side" in res.reason


def test_text_running_up_to_a_drawing_is_no_label():
    # "… nghiệm t ∈ (2; e)." set around the drawing: its last formula reaches into the drawing's box
    words, x = word("Ta có nghiệm", "VNR12", 110, 160)
    tail = [g("t", "CMMI12", 184, 160), g("∈", "CMSY10", 190, 160, code=0x32), g("(", "CMR12", 196, 160),
            g("2", "CMR12", 202, 160), g(")", "CMR12", 208, 160)]
    gl = figure_question(120, 220, extra=words + tail)
    pages = drawn_page(gl, [(200, 320, 120, 220)])
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.content is None and "cuts through a line of text" in res.reason


def test_radical_of_the_fraction_below_is_no_part_of_a_denominator():
    # B. 1/6 set right above D. √3/2 (options in two columns, rows 28 pt apart): D's radical sign hangs from its
    # overline just under B's fraction bar
    stem, x = word("Tính", "VNR12", 90, 100)
    gl = stem + [g("1", "CMR12", 272, 145), g("6", "CMR12", 272, 161.6),
                 g("√", "CMSY10", 262, 163.6, code=0x70, w=10), g("3", "CMR12", 272, 173.5), g("2", "CMR12", 272, 189.8)]
    rules = [(262, 288, 150.4), (272, 278, 163.3), (262, 288, 178.6)]
    pages = question(gl, rules=rules, base=100)
    pages[1].glyphs = [x for x in pages[1].glyphs if not (x.base == 120)]  # options moved below the fractions
    for k, lab in enumerate("ABCD"):
        pages[1].glyphs += [g(lab, "VNBX12", 50 + 120 * k, 215), g(".", "VNR12", 56 + 120 * k, 215),
                            g(str(k + 1), "CMR12", 66 + 120 * k, 215)]
    pages[1].glyphs = [x for x in pages[1].glyphs if not (x.font == "VNBX12" and x.base == 160)]
    pages[1].glyphs += word("Câu 2.", "VNBX12", 40, 260)[0]
    res = F.layout_question(pages, 1, 1)
    assert res.reason == "", res.reason
    tex = [n["tex"] for b in res.content["stem"] for n in b["c"] if n["t"] == "m"]
    assert r"\dfrac{1}{6}" in " ".join(tex) and r"\dfrac{\sqrt{3}}{2}" in " ".join(tex)


def test_point_name_beside_the_end_of_a_text_line_is_a_label():
    # "… cạnh bằng" ends 1.3 em left of the drawing; the point "B" above the square is on the same baseline
    words, _ = word("có cạnh bằng", "VNR12", 110, 112)
    b = g("B", "CMMI12", 255, 112)
    gl = figure_question(120, 220, extra=words + [b])
    pages = drawn_page(gl, [(200, 320, 120, 220)])
    res = F.layout_question(pages, 1, 1, figures=True)
    assert res.reason == "" and res.figures[0]["box"][1] <= b.top
    assert "B" not in json.dumps(res.content["stem"][0], ensure_ascii=False)
