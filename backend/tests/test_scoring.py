"""Deterministic scoring rules."""
import pytest

from app.exam.scoring import score_item, score_session

CFG = {"correct": 1.0, "incorrect": 0.0, "unanswered": 0.0, "multi_choice": "all_or_nothing",
       "tf_sequence": "all_or_nothing", "numeric_tolerance": 1e-6, "scale_to": None}
CHOICE = {"kind": "choice", "labels": ["B"]}


def test_single_choice():
    assert score_item(CHOICE, "auto", {"labels": ["B"]}, 1, CFG).outcome == "correct"
    assert score_item(CHOICE, "auto", {"labels": ["A"]}, 1, CFG).outcome == "incorrect"
    r = score_item(CHOICE, "auto", {"labels": []}, 1, CFG)
    assert (r.outcome, r.points, r.max_points) == ("unanswered", 0, 1)
    assert score_item(CHOICE, "auto", None, 1, CFG).outcome == "unanswered"


def test_negative_marking_and_points():
    cfg = dict(CFG, incorrect=-0.25)
    assert score_item(CHOICE, "auto", {"labels": ["A"]}, 2, cfg).points == -0.5
    assert score_item(CHOICE, "auto", {"labels": ["B"]}, 2, cfg).points == 2


def test_multiple_choice_rules():
    ans = {"kind": "choice", "labels": ["A", "C"]}
    assert score_item(ans, "auto", {"labels": ["C", "A"]}, 1, CFG).outcome == "correct"
    assert score_item(ans, "auto", {"labels": ["A"]}, 1, CFG).outcome == "incorrect"
    r = score_item(ans, "auto", {"labels": ["A"]}, 1, dict(CFG, multi_choice="partial"))
    assert (r.outcome, r.points) == ("partial", 0.5)
    assert score_item(ans, "auto", {"labels": ["A", "B"]}, 1, dict(CFG, multi_choice="partial")).outcome == "incorrect"


@pytest.mark.parametrize("given,ok", [("1,5", True), ("1.5", True), ("3/2", True), (" 1,50 ", True),
                                      ("1,6", False), ("abc", False)])
def test_numeric(given, ok):
    ans = {"kind": "numeric", "value": 1.5, "text": "1,5"}
    assert score_item(ans, "auto", {"value": given}, 1, CFG).outcome == ("correct" if ok else "incorrect")


def test_tf_sequence_rules():
    ans = {"kind": "tf_sequence", "values": [True, False, True, True]}
    assert score_item(ans, "auto", {"values": [True, False, True, True]}, 1, CFG).outcome == "correct"
    assert score_item(ans, "auto", {"values": [True, False, True, False]}, 1, CFG).outcome == "incorrect"
    thpt = dict(CFG, tf_sequence="thpt2025")
    assert score_item(ans, "auto", {"values": [True, False, True, False]}, 1, thpt).points == 0.5
    assert score_item(ans, "auto", {"values": [True, None, None, None]}, 1, thpt).points == 0.1
    per = dict(CFG, tf_sequence="per_statement")
    assert score_item(ans, "auto", {"values": [True, True, True, True]}, 1, per).points == 0.75


def test_text_and_boolean():
    ans = {"kind": "text", "text": "nguồn", "accepted": ["nguồn"]}
    assert score_item(ans, "auto", {"text": " Nguồn. "}, 1, CFG).outcome == "correct"
    assert score_item({"kind": "boolean", "value": False}, "auto", {"value": False}, 1, CFG).outcome == "correct"


def test_unknown_answer_is_never_scored():
    r = score_item(None, "auto", {"labels": ["A"]}, 1, CFG)
    assert (r.outcome, r.points, r.max_points) == ("ungraded", 0, 0)
    r = score_item({"kind": "text", "text": "x"}, "self_check", {"text": "x"}, 1, CFG)
    assert (r.outcome, r.max_points) == ("ungraded", 0)


def test_session_totals_sections_and_scaling():
    items = [
        {"position": 1, "section_index": 0, "answer": CHOICE, "scoring_mode": "auto", "response": {"labels": ["B"]}, "points": 1},
        {"position": 2, "section_index": 0, "answer": CHOICE, "scoring_mode": "auto", "response": {"labels": ["C"]}, "points": 1},
        {"position": 3, "section_index": 1, "answer": CHOICE, "scoring_mode": "auto", "response": None, "points": 1},
        {"position": 4, "section_index": 1, "answer": None, "scoring_mode": "self_check", "response": {"text": "x"}, "points": 1},
    ]
    r = score_session(items, dict(CFG, scale_to=150), [{"key": "a", "title": "A"}, {"key": "b", "title": "B"}])
    assert (r["score"], r["max_score"], r["scaled"]) == (1.0, 3.0, 50.0)
    assert r["counts"] == {"correct": 1, "incorrect": 1, "partial": 0, "unanswered": 1, "ungraded": 1}
    assert r["sections"][0]["score"] == 1 and r["sections"][1]["max_score"] == 1
    # determinism
    assert score_session(items, dict(CFG, scale_to=150), [{"key": "a"}, {"key": "b"}])["score"] == r["score"]
