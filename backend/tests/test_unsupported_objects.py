"""'Đối tượng không hiển thị được' placeholders resolved from the original Word document: watermarks removed,
simple arrows as Unicode, pictures inserted, anything unmatched left for a reviewer."""
import io
import zipfile

from PIL import Image

from app.review import unsupported_objects as U

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" ' \
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
       'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" ' \
       'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" ' \
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def png(color, size=(40, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def picture(rid, behind):
    return (f'<w:r><w:drawing><wp:anchor behindDoc="{1 if behind else 0}"><wp:extent cx="3600000" cy="3600000"/>'
            f'<wp:wrapNone/><wp:docPr id="1" name="p"/><a:graphic><a:graphicData><pic:pic><pic:blipFill>'
            f'<a:blip r:embed="{rid}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>')


ARROW = ('<w:r><w:drawing><wp:inline><wp:extent cx="360000" cy="120000"/><a:graphic><a:graphicData><wps:wsp>'
         '<wps:spPr><a:prstGeom prst="rightArrow"/></wps:spPr></wps:wsp></a:graphicData></a:graphic></wp:inline>'
         '</w:drawing></w:r>')


def para(*runs):
    return "<w:p>" + "".join(runs) + "</w:p>"


def text(t):
    return f"<w:r><w:t>{t}</w:t></w:r>"


def make_doc():
    body = [
        para(picture("rWM", True), text("Câu 1. Cho tam giác ABC vuông tại A.")),
        para(picture("rWM", True), text("Câu 2. Hai vật dao động điều hoà.")),
        para(picture("rWM", True), text("Câu 3. Tính giá trị của biểu thức.")),
        para(text("Quan sát đồ thị sau đây"), picture("rFIG", False), text("rồi trả lời câu hỏi.")),
        para(text("Phản ứng xảy ra"), ARROW, text("kết tủa trắng xuất hiện.")),
    ]
    doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W_NS}><w:body>{"".join(body)}<w:sectPr/></w:body></w:document>'
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rWM" Target="media/wm.png"/><Relationship Id="rFIG" Target="media/fig.png"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/wm.png", png((235, 235, 235)))     # light: the publisher's watermark
        z.writestr("word/media/fig.png", png((0, 0, 0)))          # dark: a real figure
    normalized = {"blocks": [
        {"type": "p", "i": 0, "runs": [{"unsupported": "drawing_shape"}, {"t": "Câu 1. Cho tam giác ABC vuông tại A."}]},
        {"type": "p", "i": 1, "runs": [{"unsupported": "drawing_shape"}, {"t": "Câu 2. Hai vật dao động điều hoà."}]},
        {"type": "p", "i": 2, "runs": [{"unsupported": "drawing_shape"}, {"t": "Câu 3. Tính giá trị của biểu thức."}]},
        {"type": "p", "i": 3, "runs": [{"t": "Quan sát đồ thị sau đây"}, {"unsupported": "drawing_shape"},
                                        {"t": "rồi trả lời câu hỏi."}]},
        {"type": "p", "i": 4, "runs": [{"t": "Phản ứng xảy ra"}, {"unsupported": "drawing_shape"},
                                        {"t": "kết tủa trắng xuất hiện."}]},
    ]}
    return buf.getvalue(), normalized


def ph():
    return {"t": "warn", "v": U.PLACEHOLDER}


def test_drawings_are_read_and_decided():
    docx, normalized = make_doc()
    ds, unaligned = U.read_document(docx, normalized)
    assert unaligned == 0 and len(ds) == 5
    decisions = [U.decide(d) for d in ds]
    assert [x.action for x in decisions] == ["remove", "remove", "remove", "image", "text"]
    assert decisions[4].text == "→" and ds[0].media_uses == 3


def test_placeholders_in_a_question_are_replaced():
    docx, normalized = make_doc()
    ds, _ = U.read_document(docx, normalized)
    content = {"stem": [{"t": "p", "c": [ph(), {"t": "s", "v": "Cho tam giác ABC vuông tại A."}]},
                        {"t": "p", "c": [{"t": "s", "v": "Quan sát đồ thị sau đây"}, ph(),
                                         {"t": "s", "v": "rồi trả lời câu hỏi."}]}],
               "options": [{"label": "A", "content": [{"t": "p", "c": [{"t": "s", "v": "Phản ứng xảy ra"}, ph(),
                                                                         {"t": "s", "v": "kết tủa trắng xuất hiện."}]}]}],
               "solution": None}
    stored = []
    new, actions, problems, images = U.resolve(content, ds, U.decide,
                                               lambda data: (stored.append(data) or "ab/" + "a" * 64 + ".png", 40, 20))
    assert problems == [] and images == 1 and not U.has_placeholder(new)
    assert new["stem"][0]["c"] == [{"t": "s", "v": "Cho tam giác ABC vuông tại A."}]
    assert new["stem"][1]["c"][1]["t"] == "img" and stored and stored[0] == ds[3].media
    assert new["options"][0]["content"][0]["c"][1] == {"t": "s", "v": "→"}


def test_an_unmatched_placeholder_is_left_for_a_reviewer():
    docx, normalized = make_doc()
    ds, _ = U.read_document(docx, normalized)
    content = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Một đoạn văn hoàn toàn khác"}, ph(),
                                         {"t": "s", "v": "không có trong tài liệu"}]}]}
    new, actions, problems, images = U.resolve(content, ds, U.decide, lambda data: ("x", 1, 1))
    assert problems and U.has_placeholder(new)


def test_a_question_is_resolved_partly_when_one_placeholder_has_no_match():
    """The matched placeholders are replaced, the unmatched one stays (the draft still has a placeholder: it is
    never served), instead of leaving every placeholder in place."""
    docx, normalized = make_doc()
    ds, _ = U.read_document(docx, normalized)
    content = {"stem": [{"t": "p", "c": [{"t": "s", "v": "Quan sát đồ thị sau đây"}, ph(),
                                         {"t": "s", "v": "rồi trả lời câu hỏi."}]},
                        {"t": "p", "c": [{"t": "s", "v": "Một đoạn văn hoàn toàn khác"}, ph(),
                                         {"t": "s", "v": "không có trong tài liệu"}]}]}
    new, actions, problems, images = U.resolve(content, ds, U.decide, lambda data: ("ab/" + "a" * 64 + ".png", 4, 2))
    assert U.outcome(actions, problems) == "partial"
    assert new["stem"][0]["c"][1]["t"] == "img" and U.has_placeholder(new) and images == 1
    assert U.outcome([], problems) == "skip" and U.outcome(actions, []) == "full"
