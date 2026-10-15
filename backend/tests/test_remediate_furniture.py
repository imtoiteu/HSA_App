"""Exam/page furniture glued into solutions (served questions flagged solution_contains_next_question)."""
from app.review.remediate import strip_furniture


def p(*texts):
    return {"t": "p", "c": [{"t": "s", "v": t} for t in texts]}


def test_page_header_split_over_paragraphs_is_dropped_and_the_solution_kept():
    c = {"stem": [p("Đề")], "options": [], "solution": [p("Vậy có 48 cách."), p("ĐỀ THI THAM KHẢO"),
                                                       p("KỲ THI", " ", "ĐÁNH GIÁ", " ", "NĂNG LỰC")],
         "explanation": [p("Đếm số cách đến từng nút của lưới ô vuông.")]}
    new, changed = strip_furniture(c)
    assert changed and new["solution"] == [p("Vậy có 48 cách.")] and new["explanation"] == c["explanation"]


def test_end_of_part_footer_is_dropped():
    c = {"stem": [p("Đề")], "options": [], "solution": [p("Đáp án C."), p("HẾT ĐỀ THI PHẦN 2"), p("Đề thi tham khảo"),
                                                       p("Kỳ thi đánh giá năng lực học sinh trung học phổ thông")]}
    new, changed = strip_furniture(c)
    assert changed and new["solution"] == [p("Đáp án C.")]


def test_next_test_title_and_its_preamble_are_cut():
    c = {"stem": [p("Đề")], "options": [], "solution": [p("m = 22,74 (g)"), p("KIỂM TRA HỌC KÌ I_ĐỀ SỐ 2"),
                                                       p("Cho biết nguyên tử khối của các nguyên tố"), p("H = 1; C = 12")]}
    new, changed = strip_furniture(c)
    assert changed and new["solution"] == [p("m = 22,74 (g)")]
    c2 = {"stem": [p("Đề")], "options": [], "solution": [p("Ta có…"), p("KIỂM TRA MỘT TIẾT CHUYÊN ĐỀ III")]}
    assert strip_furniture(c2)[0]["solution"] == [p("Ta có…")]


def test_text_that_only_mentions_an_exam_is_kept():
    c = {"stem": [p("ĐỀ THI THAM KHẢO")], "options": [],
         "solution": [p("Theo đề thi tham khảo năm 2023, ta có kết quả trên."), p("Kỳ thi đánh giá năng lực là kỳ thi…")]}
    new, changed = strip_furniture(c)
    assert not changed and new == c


def test_furniture_lines_inside_a_paragraph_are_dropped():
    br = {"t": "br"}
    c = {"stem": [p("Đề")], "options": [], "solution": [{"t": "p", "c": [
        {"t": "s", "v": "không thể sử dụng tối đa."}, br, {"t": "s", "v": "ĐỀ THI THAM KHẢO"}, br,
        {"t": "s", "v": "KỲ THI ĐÁNH GIÁ NĂNG LỰC"}, br, {"t": "s", "v": "Dựa vào bản đồ đã cho."}]}]}
    new, changed = strip_furniture(c)
    assert changed and new["solution"] == [{"t": "p", "c": [{"t": "s", "v": "không thể sử dụng tối đa."}, br,
                                                            {"t": "s", "v": "Dựa vào bản đồ đã cho."}]}]
