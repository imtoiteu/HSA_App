"""Builds a miniature HSA-question-bank tree with the SAME structures as the real upstream
(SQLite schema of scripts/hsa/export_db.py, content-addressed assets, editorial overlays and
bulk-build manifests). Text is synthetic; structures mirror representative real records.
"""
import hashlib
import io
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE document (document_id TEXT PRIMARY KEY, blob_sha256 TEXT, file_kind TEXT, extraction_method TEXT,
  document_role TEXT, exam_system TEXT, exam_name TEXT, exam_type TEXT, year INTEGER, version TEXT,
  metadata TEXT, representative_path TEXT, source_chains TEXT);
CREATE TABLE asset (asset_id TEXT PRIMARY KEY, sha256 TEXT, ext TEXT, bytes INTEGER, role TEXT, storage_path TEXT);
CREATE TABLE formula (formula_id TEXT PRIMARY KEY, source_format TEXT, latex TEXT, latex_confidence REAL,
  issues TEXT, preview_asset_id TEXT, original_payload TEXT);
CREATE TABLE question_group (group_id TEXT PRIMARY KEY, document_id TEXT, kind TEXT, header_md TEXT, content_md TEXT,
  tables TEXT, declared_from INTEGER, declared_to INTEGER);
CREATE TABLE canonical_question (canonical_question_id TEXT PRIMARY KEY, question_type TEXT, stem_md TEXT, tables TEXT,
  correct_answer TEXT, answer_source_type TEXT, answer_confidence REAL, solution_md TEXT, explanation_md TEXT,
  group_id TEXT, subject TEXT, topic TEXT, subtopic TEXT, cognitive_level TEXT, language TEXT, has_image INTEGER,
  has_table INTEGER, has_formula INTEGER, review_status TEXT, review_flags TEXT, representative_occurrence_id TEXT,
  n_occurrences INTEGER, exam_systems TEXT, is_servable INTEGER);
CREATE TABLE canonical_option (canonical_question_id TEXT, label TEXT, content_md TEXT, is_correct INTEGER,
  PRIMARY KEY (canonical_question_id, label));
CREATE TABLE question_occurrence (question_id TEXT PRIMARY KEY, canonical_question_id TEXT, document_id TEXT,
  source_occurrence_id TEXT, source_question_number INTEGER, source_part_index INTEGER, section TEXT, location TEXT,
  stem_md TEXT, options TEXT, correct_answer TEXT, answer_source_type TEXT, answer_confidence REAL,
  answer_evidence TEXT, extraction_method TEXT, extraction_confidence REAL, duplicate_status TEXT,
  review_status TEXT, review_flags TEXT, exam_system TEXT, year INTEGER, subject TEXT);
CREATE TABLE question_formula (canonical_question_id TEXT, formula_id TEXT, PRIMARY KEY (canonical_question_id, formula_id));
CREATE TABLE question_asset (canonical_question_id TEXT, asset_id TEXT, PRIMARY KEY (canonical_question_id, asset_id));
"""


def h16(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def png_bytes(w: int, h: int, seed: int = 0) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    for i in range(0, w, 20):
        d.line([(i, 0), (w - i, h)], fill=(seed % 200, 30, 90), width=3)
    d.rectangle([5, 5, w - 6, h - 6], outline="black", width=4)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class Builder:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.qb = self.root / "question-bank"
        (self.qb / "sqlite").mkdir(parents=True, exist_ok=True)
        (self.root / "editorial").mkdir(parents=True, exist_ok=True)
        self.db_path = self.qb / "sqlite" / "hsa_question_bank.sqlite"
        self.con = sqlite3.connect(self.db_path)
        self.con.executescript(SCHEMA)
        self.docs = {}

    # ---- primitives -------------------------------------------------------------------------
    def doc(self, key, method="docx_native", system="HSA", year=2024):
        did = f"doc_{h16(key)}"
        if did not in self.docs:
            self.con.execute("INSERT INTO document VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (did, h16(key) * 4, "docx", method, "paper_with_solutions", system, f"Đề {key}",
                              "mock_exam", year, None, "{}", f"src/{key}.docx", "[]"))
            self.docs[did] = method
        return did

    def formula(self, latex, conf=0.9, fmt="omml", issues=None):
        fid = f"fm_{h16(latex + fmt + str(conf))}"
        self.con.execute("INSERT OR IGNORE INTO formula VALUES (?,?,?,?,?,?,?)",
                         (fid, fmt, latex, conf, json.dumps(issues or []), None, None))
        return fid

    def image(self, w=400, h=300, seed=0):
        data = png_bytes(w, h, seed)
        sha = hashlib.sha256(data).hexdigest()
        rel = f"assets/{sha[:2]}/{sha}.png"
        p = self.qb / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        aid = f"as_{sha[:16]}"
        self.con.execute("INSERT OR IGNORE INTO asset VALUES (?,?,?,?,?,?)", (aid, sha, "png", len(data), "image", rel))
        return aid

    def group(self, key, header, content="", tables=None, doc_key="d1"):
        gid = f"grp_{h16(key)}"
        self.con.execute("INSERT INTO question_group VALUES (?,?,?,?,?,?,?,?)",
                         (gid, self.doc(doc_key), "range", header, content, json.dumps(tables or []), 1, 3))
        return gid

    def question(self, key, stem, options=None, answer=None, source="SOURCE_PROVIDED_ANSWER", qtype="single_choice",
                 subject="math", solution=None, explanation=None, group=None, tables=None, review="auto_accepted",
                 flags=None, doc_key="d1", method="docx_native", number=1, systems=("HSA",), language="vi"):
        cid = f"cq_{h16(key)}"
        qo = f"qo_{h16(key + 'occ')}"
        did = self.doc(doc_key, method)
        opts = options or {}
        if answer is None:
            source = "UNRESOLVED"
        self.con.execute("INSERT INTO canonical_question VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, qtype, stem, json.dumps(tables or []), json.dumps(answer) if answer else None, source,
                          0.95, solution, explanation, group, subject, None, None, None, language,
                          int("{{img:" in stem), int(bool(tables)), int("{{f:" in stem), review,
                          json.dumps(flags or []), qo, 1, json.dumps(list(systems)),
                          int(review == "auto_accepted")))
        for lab, md in opts.items():
            self.con.execute("INSERT INTO canonical_option VALUES (?,?,?,?)",
                             (cid, lab, md, int(bool(answer and lab in (answer.get("labels") or [])))))
        self.con.execute("INSERT INTO question_occurrence (question_id, canonical_question_id, document_id, "
                         "source_question_number, section, location, review_flags, exam_system, year, extraction_method) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (qo, cid, did, number, "PHẦN 1", "{}", json.dumps(flags or []), systems[0] if systems else None,
                          2024, method))
        return cid

    def overlay(self, name, rows):
        with open(self.root / "editorial" / f"{name}.jsonl", "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def manifest(self, rows, vol="Toan_Vol01"):
        d = self.root / "rendered" / "docx" / "_manifest"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{vol}.json").write_text(json.dumps({"hash": "x", "rows": rows}, ensure_ascii=False))

    def close(self):
        self.con.commit()
        self.con.close()


def build_standard(root: Path) -> dict:
    """The standard fixture corpus used by the tests. Returns {name: cq_id}."""
    b = Builder(root)
    ids = {}
    ABCD = lambda *xs: dict(zip("ABCD", xs))  # noqa: E731
    # plain math MCQs with formulas (enough for small exams)
    for i in range(1, 31):
        f1 = b.formula(f"x^{{{i}}}+{i}")
        ids[f"m{i}"] = b.question(f"m{i}", f"Giá trị của {{{{f:{f1}}}}} khi x = 1 là", ABCD(
            str(i + 1), str(i + 2), str(i + 3), str(i + 4)), {"kind": "labels", "labels": ["A"]},
            solution=f"Thay x = 1 ta được {i + 1}. **Chọn A**", number=i)
    # literature MCQs
    for i in range(1, 21):
        ids[f"v{i}"] = b.question(f"v{i}", f"Câu văn học số {i}: từ nào viết đúng chính tả?",
                                  ABCD("sáng sủa", "xáng sủa", "sáng xủa", "xáng xủa"),
                                  {"kind": "labels", "labels": ["A"]}, subject="literature_language", number=i)
    # science MCQs (chemistry notation) and english
    for i in range(1, 16):
        ids[f"s{i}"] = b.question(f"s{i}", f"Công thức của khí số {i}: C<sub>{i}</sub>H<sub>{2 * i + 2}</sub> là",
                                  ABCD("ankan", "anken", "ankin", "aren"), {"kind": "labels", "labels": ["A"]},
                                  subject="chemistry" if i % 2 else "science", number=i)
    for i in range(1, 11):
        ids[f"e{i}"] = b.question(f"e{i}", f"Choose the correct word ({i}): She ___ to school every day.",
                                  ABCD("goes", "go", "going", "gone"), {"kind": "labels", "labels": ["A"]},
                                  subject="english", language="en", number=i)
    # numeric response
    for i in range(1, 6):
        ids[f"n{i}"] = b.question(f"n{i}", f"Tính 1,5 × {i}.", None, {"kind": "numeric", "text": str(1.5 * i).replace(".", ","),
                                                                      "value": 1.5 * i}, qtype="numeric_response")
    # short response with free-text key → self-check
    ids["short_text"] = b.question("short_text", "Điền từ còn thiếu: “Uống nước nhớ …”", None,
                                   {"kind": "text", "text": "nguồn"}, qtype="short_response",
                                   subject="literature_language")
    # short response that is really an inline MCQ → excluded
    ids["short_inline"] = b.question("short_inline", "Chiều dài là\n\n**A**10 cm.\t**B**20 cm.\t**C**30 cm.\t**D**40 cm.",
                                     None, {"kind": "text", "text": "C"}, qtype="short_response", subject="physics")
    # true/false statements with a counting key
    ids["tf_count"] = b.question("tf_count", "Cho các nhận định sau:\n\na) Một.\n\nb) Hai.\n\nc) Ba.\n\nd) Bốn.\n\nSố nhận định đúng là",
                                 None, {"kind": "numeric", "text": "3", "value": 3.0}, qtype="true_false_statements",
                                 subject="chemistry")
    # error identification (never shuffled)
    ids["err_id"] = b.question("err_id", "France, <u>where</u> is a <u>very beautiful</u> country, <u>has</u> many "
                                         "<u>attractions</u>.", ABCD("where", "very beautiful", "has", "attractions"),
                               {"kind": "labels", "labels": ["A"]}, qtype="error_identification", subject="english",
                               language="en")
    # option referring to other options → not shuffle-safe
    ids["all_above"] = b.question("all_above", "Chọn phát biểu đúng.", ABCD("Một", "Hai", "Ba", "Cả A và B đều đúng"),
                                  {"kind": "labels", "labels": ["D"]})
    # image + table question
    img = b.image(420, 300, 1)
    ids["img_tbl"] = b.question("img_tbl", f"Quan sát hình vẽ:\n\n{{{{img:{img}}}}}\n\nvà bảng {{{{tbl:0}}}} rồi chọn đáp án",
                                ABCD("1", "2", "3", "4"), {"kind": "labels", "labels": ["B"]},
                                tables=[{"rows": [[{"md": "**p**"}, {"md": "V", "vmerge": "restart"}],
                                                  [{"md": "10<sup>5</sup> Pa"}, {"md": "", "vmerge": "continue"}]]}],
                                subject="physics")
    # low-resolution image → NEEDS_VISUAL_REVIEW
    tiny = b.image(200, 120, 2)
    ids["lowres"] = b.question("lowres", f"Hình nhỏ {{{{img:{tiny}}}}}", ABCD("1", "2", "3", "4"),
                               {"kind": "labels", "labels": ["A"]})
    # formula with low confidence → NEEDS_FORMULA_REVIEW
    bad = b.formula(r"\frac{a}{", conf=0.5)
    ids["badformula"] = b.question("badformula", f"Tính {{{{f:{bad}}}}}", ABCD("1", "2", "3", "4"),
                                   {"kind": "labels", "labels": ["A"]})
    # unresolved answer → NEEDS_ANSWER_LINKING
    ids["noanswer"] = b.question("noanswer", "Câu chưa có đáp án.", ABCD("1", "2", "3", "4"), None,
                                 review="needs_review", flags=["missing_answer"])
    # automatically inferred candidate → never trusted
    ids["candidate"] = b.question("candidate", "Câu chỉ có đáp án suy đoán.", ABCD("1", "2", "3", "4"),
                                  {"kind": "labels", "labels": ["C"]}, source="AUTOMATICALLY_INFERRED_CANDIDATE",
                                  review="needs_review", flags=["answer_is_candidate_only"])
    # pdf text layer math → NEEDS_FORMULA_REVIEW
    ids["pdf_math"] = b.question("pdf_math", "Tính sin x khi x = 0", ABCD("0", "1", "2", "3"),
                                 {"kind": "labels", "labels": ["A"]}, method="pdf_text_layer", doc_key="pdf1",
                                 review="needs_review", flags=["pdf_text_layer_fidelity"])
    # rejected
    ids["rejected"] = b.question("rejected", "", None, None, review="rejected", flags=["missing_stem_and_options"])
    # passage group with content (3 members) and a group whose passage is missing
    g1 = b.group("g1", "**Đọc đoạn văn sau và trả lời các câu hỏi từ 5 đến 7**",
                 "Hà Nội là thủ đô của Việt Nam. Thành phố có lịch sử hơn một nghìn năm.", doc_key="d2")
    for k in range(3):
        ids[f"g1_{k}"] = b.question(f"g1_{k}", f"Câu hỏi đọc hiểu {k + 1} về đoạn văn.",
                                    ABCD("Đúng", "Sai", "Không rõ", "Khác"), {"kind": "labels", "labels": ["A"]},
                                    subject="literature_language", group=g1, doc_key="d2", number=5 + k)
    g2 = b.group("g2", "**Sử dụng thông tin dưới đây trả lời câu hỏi từ 81 đến 82**", "", doc_key="d3")
    for k in range(2):
        ids[f"g2_{k}"] = b.question(f"g2_{k}", f"Với số tiền trên, câu {k + 1}?", ABCD("60", "70", "80", "90"),
                                    {"kind": "labels", "labels": ["C"]}, group=g2, doc_key="d3", number=81 + k)
    # editorial overlay: reconstructed stem with {{tex:}} for a PDF-derived question
    ids["overlay"] = b.question("overlay", "Mặt phẳng (P): x + y = 0 (text layer)", ABCD("1", "2", "3", "4"),
                                {"kind": "labels", "labels": ["B"]}, method="pdf_text_layer", doc_key="pdf2",
                                review="needs_review", flags=["pdf_text_layer_fidelity"])
    b.overlay("question_overrides", [{"question_id": ids["overlay"], "status": "RECONSTRUCTED_VALIDATED",
                                      "stem_md": "Mặt phẳng {{tex:(P): x+y=0}} có vectơ pháp tuyến là",
                                      "options": {"A": "{{tex:(1;0)}}", "B": "{{tex:(1;1)}}", "C": "{{tex:(0;1)}}",
                                                  "D": "{{tex:(1;-1)}}"}, "validated_by": "test"}])
    # decorative asset removed by overlay
    deco = b.image(300, 300, 3)
    ids["deco"] = b.question("deco", f"Câu có huy hiệu {{{{img:{deco}}}}}thừa.", ABCD("1", "2", "3", "4"),
                             {"kind": "labels", "labels": ["A"]})
    b.overlay("asset_replacements", [{"asset_id": deco, "kind": "decorative_remove", "validated_by": "test"}])
    # upstream manifest decides the state of one question (authoritative over derivation)
    ids["manifest_flagged"] = b.question("manifest_flagged", "Câu mà biên tập đánh dấu cần xem lại.",
                                         ABCD("1", "2", "3", "4"), {"kind": "labels", "labels": ["A"]})
    b.manifest([{"question_id": ids["manifest_flagged"], "states": ["NEEDS_VISUAL_REVIEW"], "notes": ["crop"]}])
    # an "open" passage whose header names questions 5-6: question 9 attached to it is suspect
    g3 = b.group("g3", "**Dựa vào thông tin được cung cấp sau đây để trả lời từ câu hỏi số 5 đến câu số 6:**",
                 "Năm 1930, kinh tế suy thoái.", doc_key="d4")
    ids["g3_in"] = b.question("g3_in", "Theo đoạn trên, năm nào kinh tế suy thoái?", ABCD("1930", "1931", "1932", "1933"),
                              {"kind": "labels", "labels": ["A"]}, subject="history", group=g3, doc_key="d4", number=5)
    ids["g3_out"] = b.question("g3_out", "Cho dung dịch X vào KOH. Dung dịch X là", ABCD("FeCl2", "AgNO3", "NaCl", "KCl"),
                               {"kind": "labels", "labels": ["A"]}, subject="chemistry", group=g3, doc_key="d4", number=9)
    # the source printed the key as the last paragraph of the stem
    ids["key_in_stem"] = b.question("key_in_stem", "Vòi tưới được gốc rau cao nhất bao nhiêu mét?\n\n4,5", None,
                                    {"kind": "numeric", "text": "4,5", "value": 4.5}, qtype="numeric_response")
    # second-pass subject inference (canonical subject stays null/generic upstream)
    ids["inf_phys"] = b.question("inf_phys", "Một vật dao động điều hoà với chu kì 2 s. Tần số là", ABCD(
        "0,5 Hz", "1 Hz", "2 Hz", "4 Hz"), {"kind": "labels", "labels": ["A"]}, subject=None)
    ids["inf_low"] = b.question("inf_low", "Phát biểu đúng là:", ABCD("x", "y", "z", "t"),
                                {"kind": "labels", "labels": ["A"]}, subject=None)
    ids["inf_none"] = b.question("inf_none", "Câu không đủ bằng chứng để phân loại.", ABCD("1", "2", "3", "4"),
                                 {"kind": "labels", "labels": ["A"]}, subject=None)
    ids["inf_sci"] = b.question("inf_sci", "Chất nào sau đây là axit?", ABCD("HCl", "NaOH", "NaCl", "KOH"),
                                {"kind": "labels", "labels": ["A"]}, subject="science")
    ids["unknown_type"] = b.question("unknown_type", "Nội dung không rõ dạng.", None, {"kind": "text", "text": "x"},
                                     qtype="open_or_unknown")
    b.overlay("subject_inference", [
        {"question_id": ids["inf_phys"], "original_subject": None, "inferred_subject": "physics",
         "confidence": "medium", "evidence": ["content lexicon physics=4 vs math=0"], "method": "subject_infer v1"},
        {"question_id": ids["inf_low"], "original_subject": None, "inferred_subject": "chemistry",
         "confidence": "low", "evidence": ["weak"], "method": "subject_infer v1"},
        {"question_id": ids["inf_none"], "original_subject": None, "inferred_subject": None, "confidence": None,
         "evidence": [], "method": "subject_infer v1"},
        {"question_id": ids["inf_sci"], "original_subject": "science", "inferred_subject": "chemistry",
         "confidence": "high", "evidence": ["path 'Hoá học'"], "method": "subject_infer v1"},
    ])
    # upstream's validated final classification (covers only these in the fixture; the rest use the 2nd pass)
    b.overlay("subject_effective", [
        {"question_id": ids["m4"], "original_subject": "math", "inferred_subject": None, "effective_subject": "chemistry",
         "classification_source": "semantic_correction", "classification_confidence": "high",
         "classification_evidence": ["content model chemistry p=0.97"], "review": None},
        {"question_id": ids["v2"], "original_subject": "literature_language", "inferred_subject": None,
         "effective_subject": "english", "classification_source": "original", "classification_confidence": "medium",
         "classification_evidence": ["model disagrees"], "review": "SUBJECT_CLASSIFICATION_REVIEW"},
        {"question_id": ids["inf_none"], "original_subject": None, "inferred_subject": None, "effective_subject": "history",
         "classification_source": "semantic_assignment", "classification_confidence": "medium",
         "classification_evidence": ["content model history p=0.93"], "review": None},
    ])
    # upstream pipeline state with one scanned PDF deferred for math-aware OCR
    inv = root / "inventory"
    inv.mkdir(exist_ok=True)
    st = sqlite3.connect(inv / "state.sqlite")
    st.executescript("CREATE TABLE blobs (sha256 TEXT PRIMARY KEY, doc_status TEXT, doc_detail TEXT);"
                     "CREATE TABLE occurrences (occurrence_id INTEGER PRIMARY KEY, blob_sha TEXT, member_path TEXT);"
                     "CREATE TABLE source_files (source_file_id INTEGER PRIMARY KEY, rel_path TEXT, sha256 TEXT);")
    st.execute("INSERT INTO blobs VALUES (?,?,?)", ("ab" * 32, "NEEDS_OCR", json.dumps({"pages": 120, "text_layer": "NO_TEXT"})))
    st.execute("INSERT INTO blobs VALUES (?,?,?)", ("cd" * 32, "SEGMENTED", "{}"))
    st.execute("INSERT INTO source_files VALUES (1, ?, ?)", ("Đề scan/Tập 2.pdf", "ab" * 32))
    st.commit()
    st.close()
    b.close()
    return ids
