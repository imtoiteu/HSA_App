"""hsa-md rendering and answer normalisation (pure functions)."""
from app.content import answers as A
from app.content import render as R


def test_marks_formulas_and_escapes():
    res = R.Resolver(formula=lambda fid: [{"t": "m", "tex": "x^2"}])
    out = R.blocks("**Câu** *nghiêng* <u>gạch</u> m<sup>3</sup> C<sub>4</sub>H<sub>10</sub> {{f:fm_abc}} \\* sao", res)
    assert len(out) == 1 and out[0]["t"] == "p"
    c = out[0]["c"]
    assert {"t": "s", "v": "Câu", "m": ["b"]} in c
    assert {"t": "s", "v": "nghiêng", "m": ["i"]} in c
    assert {"t": "s", "v": "gạch", "m": ["u"]} in c
    assert {"t": "s", "v": "3", "m": ["sup"]} in c
    assert {"t": "s", "v": "10", "m": ["sub"]} in c
    assert {"t": "m", "tex": "x^2"} in c
    assert any(n["t"] == "s" and "* sao" in n["v"] for n in c)


def test_paragraphs_breaks_and_display_math():
    res = R.Resolver()
    out = R.blocks("Dòng 1\nDòng 2\n\n{{tex:\\frac{1}{2}}}", res)
    assert out[0]["c"][1] == {"t": "br"}
    assert out[1] == {"t": "p", "c": [{"t": "m", "tex": "\\frac{1}{2}"}], "display": True}


def test_unresolved_references_are_flagged_not_hidden():
    res = R.Resolver()
    out = R.blocks("Hình {{img:as_0011223344556677}} và {{unsupported:ole}}", res)
    kinds = [k for k, _ in res.issues]
    assert "missing_visual" in kinds and "visual" in kinds
    assert any(n["t"] == "warn" for n in out[0]["c"])


def test_table_colspan_and_vertical_merge():
    res = R.Resolver()
    t = {"rows": [[{"md": "a", "colspan": 2}, {"md": "b", "vmerge": "restart"}],
                  [{"md": "c"}, {"md": "d"}, {"md": "", "vmerge": "continue"}]]}
    out = R.blocks("{{tbl:0}}", res, [t])
    rows = out[0]["rows"]
    assert rows[0][0]["colspan"] == 2 and rows[0][1]["rowspan"] == 2
    assert len(rows[1]) == 2  # the continued cell is merged away


def test_group_header_range_neutralised():
    assert R.neutralize_group_header("Trả lời các câu hỏi từ 66 - 70") == "Trả lời các câu hỏi {{range}}"
    assert R.neutralize_group_header("Read and answer questions 36 to 40.") == "Read and answer {{range}}."
    out = R.blocks("Đọc và trả lời câu {{range}}", R.Resolver())
    assert {"t": "range"} in out[0]["c"]


def test_plain_text():
    assert R.plain_text(R.blocks("A **b** {{tex:x}}", R.Resolver())) == "A b x"


# ---------------------------------------------------------------------------------------------- answers
def test_unresolved_and_candidates_never_produce_answers():
    raw = {"kind": "labels", "labels": ["C"]}
    assert A.normalize_answer("single_choice", raw, "UNRESOLVED", list("ABCD")) == (None, None)
    assert A.normalize_answer("single_choice", raw, "AUTOMATICALLY_INFERRED_CANDIDATE", list("ABCD")) == (None, None)
    assert A.normalize_answer("single_choice", raw, "SOURCE_PROVIDED_ANSWER", list("ABCD"))[0] == \
        {"kind": "choice", "labels": ["C"]}


def test_answer_must_exist_among_options():
    assert A.normalize_answer("single_choice", {"kind": "labels", "labels": ["E"]}, "SOURCE_PROVIDED_ANSWER",
                              list("ABCD")) == (None, "answer_not_in_options")


def test_text_key_on_choice_question_maps_to_label_or_option_text():
    opts = {"A": "Hà Nội", "B": "Huế"}
    assert A.normalize_answer("single_choice", {"kind": "text", "text": "B."}, "SOURCE_PROVIDED_ANSWER", ["A", "B"],
                              opts)[0] == {"kind": "choice", "labels": ["B"]}
    assert A.normalize_answer("single_choice", {"kind": "text", "text": "hà nội"}, "SOURCE_PROVIDED_ANSWER",
                              ["A", "B"], opts)[0] == {"kind": "choice", "labels": ["A"]}


def test_numbers_vietnamese_formats():
    assert A.parse_number("1,5") == 1.5
    assert A.parse_number("-2") == -2
    assert A.parse_number("−0,25") == -0.25
    assert A.parse_number("3/4") == 0.75
    assert A.parse_number("1.000.000") == 1_000_000
    assert A.parse_number("abc") is None


def test_input_and_scoring_modes():
    ans, _ = A.normalize_answer("short_response", {"kind": "text", "text": "nguồn"}, "SOURCE_PROVIDED_ANSWER", [])
    inp = A.input_spec("short_response", [], ans)
    assert inp == {"kind": "text"} and A.scoring_mode(inp, ans, False) == "self_check"
    assert A.scoring_mode(inp, ans, False, text_auto=True) == "auto"
    ans, _ = A.normalize_answer("short_response", {"kind": "text", "text": "12,5"}, "SOURCE_PROVIDED_ANSWER", [])
    assert ans["kind"] == "numeric" and ans["value"] == 12.5
    assert A.input_spec("constructed_response", [], None) == {"kind": "none"}
    assert A.scoring_mode({"kind": "none"}, None, True) == "self_check"
    assert A.scoring_mode({"kind": "none"}, None, False) == "none"
    assert A.input_spec("true_false_statements", [], None, "a) x\n\nb) y\n\nc) z") == {"kind": "tf_sequence", "n": 3}


def test_shuffle_safety():
    assert A.shuffle_safe("single_choice", {"A": "1", "B": "2", "C": "3", "D": "4"})
    assert not A.shuffle_safe("single_choice", {"A": "1", "B": "2", "C": "3", "D": "Cả A và B đều đúng"})
    assert not A.shuffle_safe("single_choice", {"A": "x", "B": "Tất cả các đáp án trên"})
    assert not A.shuffle_safe("error_identification", {"A": "where", "B": "has"})
