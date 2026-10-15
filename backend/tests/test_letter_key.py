"""A lone A–D key on a question without options is a mis-split question (a worked solution parsed as a question,
an essay/drag-and-drop item carrying another question's key letter): flagged and never served."""
from app.content import answers as A
from app.review import content as C
from app.sync.policy import DEFAULT_POLICY, PolicyInput, evaluate


def test_letter_key_without_options_is_detected():
    assert A.letter_key_without_options([], {"kind": "text", "text": "D"})
    assert A.letter_key_without_options([], {"kind": "text", "text": " b. "})
    assert not A.letter_key_without_options([], {"kind": "text", "text": "Đáp án D sai vì…"})
    assert not A.letter_key_without_options([], {"kind": "text", "text": "Hà Nội"})
    assert not A.letter_key_without_options([], {"kind": "numeric", "text": "2", "value": 2})
    assert not A.letter_key_without_options([{"label": "A"}, {"label": "B"}], {"kind": "text", "text": "A"})


def test_flag_blocks_serving():
    content = {"type": "short_response", "stem": [{"t": "p", "c": [{"t": "s", "v": "Ta có y' = 0. Chọn D"}]}],
               "options": [], "input": {"kind": "text"}}
    flags = C.derived_flags(content, {"kind": "text", "text": "D"})
    assert "letter_answer_without_options" in flags and "letter_answer_without_options" in C.CONTENT_FLAGS
    ok, reasons = evaluate(PolicyInput("READY_TO_SERVE", "short_response", "auto", sorted(flags), False, None),
                           DEFAULT_POLICY)
    assert not ok and "letter_answer_without_options" in reasons
    ok, _ = evaluate(PolicyInput("READY_TO_SERVE", "short_response", "auto", [], False, None), DEFAULT_POLICY)
    assert ok
