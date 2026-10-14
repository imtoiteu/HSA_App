"""'Biểu thức / Công thức toán' answers: safe parsing, algebraic equivalence, editor normalisation, grading."""
import pytest

from app.content import mathexpr as M
from app.content.answers import input_spec
from app.exam.scoring import score_item
from app.exam.service import validate_response
from app.review.content import ContentError, normalize_answer


@pytest.mark.parametrize("a,b", [
    (r"\frac{a\sqrt{6}}{6}", r"\frac{a}{\sqrt{6}}"),
    (r"a\sqrt{6}/6", r"\sqrt{6}\cdot a/6"),
    (r"\dfrac{a^3\sqrt3}{3}", r"\frac{\sqrt{3}a^3}{3}"),
    (r"x^2+2x+1", r"(x+1)^2"),
    (r"3\sqrt{2}", r"\sqrt{18}"),
    (r"\sin^2x+\cos^2x", "1"),
    (r"\log_2 8", "3"),
    (r"\frac{1}{2}", "0,5"),
    (r"y=2x+1", "1+2x"),
    (r"e^{2}", r"\mathrm{e}^2"),
    (r"\left(x-1\right)\left(x+1\right)", r"x^{2}-1"),
])
def test_equivalent_forms(a, b):
    assert M.equivalent(a, b)


@pytest.mark.parametrize("a,b", [
    (r"a\sqrt{6}/6", r"\frac{a\sqrt{3}}{3}"),
    (r"|x-1|", "x-1"),
    (r"2x", "2x+0.001"),
    (r"x^2", "x^3"),
])
def test_different_forms(a, b):
    assert not M.equivalent(a, b)


def test_parser_is_safe_and_bounded():
    for bad in ["__import__('os')", "x+", r"\input{/etc/passwd}", "a" * 500, r"\frac{1}{"]:
        assert M.check(bad) is not None
    assert M.check("a+b", ["a"]) == "biến không khai báo: b"
    assert not M.equivalent("2^{100000}", "1")   # huge exponents are refused, never computed


def test_editor_key_and_tolerance():
    a = normalize_answer("short_response", {"kind": "math_expression", "tex": r"\dfrac{a\sqrt{6}}{6}",
                                            "variables": "a", "accepted": [r"\frac{a}{\sqrt6}"]}, [])
    assert a["kind"] == "math_expression" and a["tex"] == r"\frac{a\sqrt{6}}{6}" and a["variables"] == ["a"]
    assert input_spec("short_response", [], a) == {"kind": "math"}
    with pytest.raises(ContentError):
        normalize_answer("short_response", {"kind": "math_expression", "tex": "a+b", "variables": ["a"]}, [])
    pi = normalize_answer("short_response", {"kind": "math_expression", "tex": r"\pi", "tolerance": 0.01}, [])
    assert score_item(pi, "auto", {"text": "3.14"}, 1.0, {}).outcome == "correct"
    assert score_item(pi, "auto", {"text": "3.1"}, 1.0, {}).outcome == "incorrect"


def test_student_answers_are_graded_by_equivalence():
    a = {"kind": "math_expression", "tex": r"\frac{a\sqrt{6}}{6}", "accepted": [r"\frac{a\sqrt{6}}{6}"],
         "variables": ["a"]}
    r = validate_response({"kind": "math"}, {"text": r"\frac{a}{\sqrt{6}}"}, [])
    assert score_item(a, "auto", r, 1.0, {}).outcome == "correct"
    assert score_item(a, "auto", {"text": r"\sqrt{6}\cdot a/6"}, 1.0, {}).outcome == "correct"
    assert score_item(a, "auto", {"text": r"\frac{a\sqrt{3}}{3}"}, 1.0, {}).outcome == "incorrect"
    assert score_item(a, "auto", {"text": "không biết"}, 1.0, {}).outcome == "incorrect"
