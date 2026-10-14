"""Multi-part questions ("Nhiều ý / Câu ghép"): a shared stem with child items, each with its own answer type,
key, solution and points; authored in the Review Studio, served and graded item by item."""
import pytest
from sqlalchemy import select

from app.content import parts as P
from app.db import SessionLocal
from app.exam.scoring import fraction_correct, is_blank, score_item
from app.exam.service import SessionError, validate_response
from app.models import Question

from .test_review import plain, qid_of


def mp_content():
    return {"type": "multi_part", "stem": plain("Cho hàm số y = x² − 4x + 3."), "options": [],
            "parts": [
                {"key": "p1", "label": "a", "prompt": plain("Đồ thị cắt trục tung tại điểm có tung độ"),
                 "answer_type": "numeric", "points": 1.0, "solution": plain("y(0) = 3.")},
                {"key": "p2", "label": "b", "prompt": plain("Hàm số đồng biến trên"), "answer_type": "choice",
                 "multi": False, "points": 2.0, "solution": None,
                 "options": [{"label": "A", "content": plain("(2; +∞)")},
                             {"label": "B", "content": plain("(−∞; 2)")}]},
                {"key": "p3", "label": "c", "prompt": plain("Đạo hàm"), "answer_type": "formula", "points": 1.0},
                {"key": "p4", "label": "d", "prompt": plain("Đỉnh là (2; −1)"), "answer_type": "boolean",
                 "points": 1.0},
            ]}


def mp_answer():
    return {"kind": "parts", "parts": {"p1": {"kind": "numeric", "value": 3.0, "text": "3", "unit": None},
                                        "p2": {"kind": "choice", "labels": ["A"]},
                                        "p3": {"kind": "math_expression", "tex": "2x-4", "accepted": ["2x-4"]},
                                        "p4": {"kind": "boolean", "value": True}}}


# ---------------------------------------------------------------------------------------------- unit
def test_part_answers_are_normalised_and_checked():
    c = mp_content()
    a = P.normalize_answer(c, {"kind": "parts", "parts": {"p1": {"text": "3"}, "p2": {"labels": ["a"]},
                                                          "p3": {"tex": "$2x-4$"}, "p4": {"value": True}}})
    assert a == mp_answer()
    with pytest.raises(P.PartError):
        P.normalize_answer(c, {"kind": "parts", "parts": {"p2": {"labels": ["C"]}}})
    with pytest.raises(P.PartError):   # the parent has no single answer
        P.normalize_answer(c, {"kind": "choice", "labels": ["A"]})
    assert P.missing_answers(c, {"kind": "parts", "parts": {"p1": a["parts"]["p1"]}}) == ["b", "c", "d"]
    assert P.problems(c, a) == []
    assert P.input_spec(c)["parts"][1] == {"key": "p2", "kind": "choice", "multi": False}


def test_items_are_scored_by_their_own_kind_and_weighted():
    c, a = mp_content(), mp_answer()
    resp = {"parts": {"p1": {"value": "3"}, "p2": {"labels": ["B"]}, "p3": {"text": "2(x-2)"},
                      "p4": {"value": True}}}
    rs = {r["key"]: r["outcome"] for r in P.results(c, a, resp, {}, fraction_correct)}
    assert rs == {"p1": "correct", "p2": "incorrect", "p3": "correct", "p4": "correct"}
    # weights 1 + 2 + 1 + 1: 3 of 5
    s = score_item(a, "auto", resp, 1.0, {}, c)
    assert s.outcome == "partial" and abs(s.points - 0.6) < 1e-9
    full = {"parts": {"p1": {"value": "3"}, "p2": {"labels": ["A"]}, "p3": {"text": "2x-4"},
                      "p4": {"value": True}}}
    assert score_item(a, "auto", full, 1.0, {}, c).outcome == "correct"
    assert is_blank({"parts": {"p1": {"value": ""}, "p2": {"labels": []}}})
    assert score_item(a, "auto", {"parts": {}}, 1.0, {}, c).outcome == "unanswered"
    # formula answers compare up to spacing and equivalent spellings
    assert P.norm_tex(r"\dfrac{1}{2}") == P.norm_tex(r"\frac 1 2".replace(" 1 2", "{1}{2}"))
    assert P.norm_tex(r"\left( x \right)^{2}") == P.norm_tex("(x)^2")


def test_responses_are_validated_item_by_item():
    c = mp_content()
    inp = P.input_spec(c)
    ok = validate_response(inp, {"parts": {"p2": {"labels": ["A"]}, "p3": {"text": "2x-4"}, "zz": {}}},
                           [], c)
    assert ok == {"parts": {"p2": {"labels": ["A"]}, "p3": {"text": "2x-4"}}}
    with pytest.raises(SessionError):
        validate_response(inp, {"parts": {"p2": {"labels": ["Z"]}}}, [], c)
    with pytest.raises(SessionError):
        validate_response(inp, {"labels": ["A"]}, [], c)


# ---------------------------------------------------------------------------------------------- end to end
def test_author_approve_serve_and_grade_a_multi_part_question(admin, student, synced):
    qid = qid_of(synced["ids"]["m25"])
    c = mp_content()
    body = {"type": "multi_part", "stem": c["stem"], "parts": c["parts"],
            "answer": {"kind": "parts", "parts": {"p1": {"text": "3"}, "p2": {"labels": ["A"]},
                                                  "p3": {"tex": "2x-4"}}},
            "note": "câu ghép"}
    r = admin.put(f"/api/review/questions/{qid}", body)
    assert r.status_code == 200, r.text
    d = admin.get(f"/api/review/questions/{qid}").json()
    w = d["working"]
    assert w["content"]["type"] == "multi_part" and w["content"]["options"] == []
    assert [p["label"] for p in w["content"]["parts"]] == ["a", "b", "c", "d"]
    assert w["content"]["input"]["kind"] == "parts" and w["answer"]["kind"] == "parts"
    assert d["preview"]["parts"][1]["options"][0]["key"] == "A" and "solution" not in d["preview"]["parts"][0]
    # item d has no key yet: the question cannot be approved
    for dim in ("content", "answer", "formula", "visual", "subject", "source"):
        admin.post(f"/api/review/questions/{qid}/confirm", {"dim": dim, "state": "ok"})
    assert admin.post(f"/api/review/questions/{qid}/approve", {}).status_code != 200
    r = admin.put(f"/api/review/questions/{qid}", {"answer": {"kind": "parts", "parts": {
        "p1": {"text": "3"}, "p2": {"labels": ["A"]}, "p3": {"tex": "2x-4"}, "p4": {"value": True}}}})
    assert r.status_code == 200, r.text
    for dim in ("content", "answer", "formula", "visual", "subject", "source"):
        admin.post(f"/api/review/questions/{qid}/confirm", {"dim": dim, "state": "ok"})
    r = admin.post(f"/api/review/questions/{qid}/approve", {})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    q = db.get(Question, qid)
    assert q.question_type == "multi_part" and q.answer_kind == "parts" and q.scoring_mode == "auto"
    assert q.is_served, q.policy_reasons
    db.close()

    # a practice session limited to the type serves it; the student answers item by item
    r = student.post("/api/sessions", {"count": 1, "types": ["multi_part"], "subjects": ["math"],
                                       "feedback": "end"})
    assert r.status_code == 200, r.text
    sid = r.json()["session"]["id"]
    s = student.get(f"/api/sessions/{sid}").json()
    it = s["items"][0]
    assert it["question"]["type"] == "multi_part" and len(it["question"]["parts"]) == 4
    assert "answer" not in it and "part_results" not in it
    resp = {"parts": {"p1": {"value": "3"}, "p2": {"labels": ["B"]}, "p3": {"text": "2x - 4"},
                      "p4": {"value": True}}}
    r = student.patch(f"/api/sessions/{sid}/answers", {"changes": [{"position": it["position"], "response": resp}]})
    assert r.status_code == 200, r.text
    r = student.post(f"/api/sessions/{sid}/submit")
    assert r.status_code == 200, r.text
    s = student.get(f"/api/sessions/{sid}").json()
    it = s["items"][0]
    assert it["outcome"] == "partial"
    pr = {p["key"]: p for p in it["part_results"]}
    assert pr["p2"]["outcome"] == "incorrect" and pr["p1"]["outcome"] == "correct"
    assert pr["p1"]["solution"][0]["c"][0]["v"] == "y(0) = 3." and pr["p2"]["answer"]["labels"] == ["A"]

    # back to a single-choice question: the items and their keys are dropped, nothing else breaks
    r = admin.put(f"/api/review/questions/{qid}", {"type": "single_choice", "options": [
        {"label": "A", "content": plain("1")}, {"label": "B", "content": plain("2")}],
        "answer": {"kind": "choice", "labels": ["A"]}})
    assert r.status_code == 200, r.text
    w = admin.get(f"/api/review/questions/{qid}").json()["working"]
    assert "parts" not in w["content"] and w["answer"] == {"kind": "choice", "labels": ["A"]}


def test_existing_questions_keep_their_hashes():
    from app.review import content as C
    c = {"type": "single_choice", "stem": plain("x"), "options": [{"label": "A", "content": plain("1")}]}
    before = C.part_hashes(c, {"kind": "choice", "labels": ["A"]}, "math", {})
    assert before["stem"] == C.h(c["stem"]) and before["options"] == C.h(c["options"])
