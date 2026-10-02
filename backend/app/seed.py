"""Idempotent reference data. Existing rows are never overwritten (admins may have edited them)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exam.blueprint import parse_config
from .models import ExamBlueprint, Plan, Product, Subject, SubjectAlias
from .sync.importer import ensure_bank

SUBJECTS = [
    # code, name, short, color, order, upstream values
    ("math", "Toán học & Xử lý số liệu", "Toán", "#2563eb", 10, ["math"]),
    ("literature", "Ngữ văn – Ngôn ngữ", "Văn", "#db2777", 20, ["literature_language"]),
    ("english", "Tiếng Anh", "Anh", "#0891b2", 30, ["english"]),
    ("science", "Khoa học tổng hợp", "Khoa học", "#16a34a", 40, ["science"]),
    ("physics", "Vật lý", "Lý", "#7c3aed", 50, ["physics"]),
    ("chemistry", "Hóa học", "Hóa", "#ea580c", 60, ["chemistry"]),
    ("biology", "Sinh học", "Sinh", "#65a30d", 70, ["biology"]),
    ("history", "Lịch sử", "Sử", "#b45309", 80, ["history"]),
    ("geography", "Địa lý", "Địa", "#0d9488", 90, ["geography"]),
    ("logic", "Tư duy logic", "Logic", "#4f46e5", 100, ["logic_reasoning"]),
    ("general", "Tổng hợp (chưa phân loại)", "Tổng hợp", "#64748b", 200, [""]),
]

SCIENCE = ["science", "physics", "chemistry", "biology", "history", "geography"]

P1 = {"key": "toan", "title": "Phần 1: Toán học và Xử lý số liệu", "duration_minutes": 75,
      "description": "50 câu · 75 phút",
      "pools": [{"subjects": ["math"], "types": ["single_choice"], "count": 40},
                {"subjects": ["math"], "types": ["numeric_response"], "count": 10}]}
P2 = {"key": "van", "title": "Phần 2: Văn học – Ngôn ngữ", "duration_minutes": 60, "description": "50 câu · 60 phút",
      "pools": [{"subjects": ["literature"], "types": ["single_choice", "error_identification"], "count": 50}]}
P3_SCI = {"key": "khoahoc", "title": "Phần 3: Khoa học", "duration_minutes": 60, "description": "50 câu · 60 phút",
          "pools": [{"subjects": SCIENCE, "types": ["single_choice"], "count": 50}]}
P3_ENG = {"key": "tienganh", "title": "Phần 3: Tiếng Anh", "duration_minutes": 60, "description": "50 câu · 60 phút",
          "pools": [{"subjects": ["english"], "types": ["single_choice", "error_identification"], "count": 50}]}

BLUEPRINTS = [
    ("hsa_full_khoahoc", "Đề thi thử HSA đầy đủ (Khoa học)",
     "Mô phỏng bài thi HSA: 3 phần thi liên tiếp, tính giờ riêng từng phần, 150 câu · 195 phút, thang 150 điểm.",
     {"sections": [P1, P2, P3_SCI], "timing": "per_section", "scoring": {"scale_to": 150}}, 20000, 10),
    ("hsa_full_tienganh", "Đề thi thử HSA đầy đủ (Tiếng Anh)",
     "Mô phỏng bài thi HSA với phần 3 là Tiếng Anh: 150 câu · 195 phút, thang 150 điểm.",
     {"sections": [P1, P2, P3_ENG], "timing": "per_section", "scoring": {"scale_to": 150}}, 20000, 20),
    ("hsa_p1_toan", "Phần 1 – Toán học và Xử lý số liệu", "Luyện riêng phần Toán: 50 câu · 75 phút.",
     {"sections": [dict(P1, duration_minutes=None)], "timing": "global", "duration_minutes": 75,
      "scoring": {"scale_to": 50}}, 0, 30),
    ("hsa_p2_van", "Phần 2 – Văn học – Ngôn ngữ", "Luyện riêng phần Văn học – Ngôn ngữ: 50 câu · 60 phút.",
     {"sections": [dict(P2, duration_minutes=None)], "timing": "global", "duration_minutes": 60,
      "scoring": {"scale_to": 50}}, 0, 40),
    ("hsa_p3_khoahoc", "Phần 3 – Khoa học", "Luyện riêng phần Khoa học: 50 câu · 60 phút.",
     {"sections": [dict(P3_SCI, duration_minutes=None)], "timing": "global", "duration_minutes": 60,
      "scoring": {"scale_to": 50}}, 0, 50),
    ("hsa_p3_tienganh", "Phần 3 – Tiếng Anh", "Luyện riêng phần Tiếng Anh: 50 câu · 60 phút.",
     {"sections": [dict(P3_ENG, duration_minutes=None)], "timing": "global", "duration_minutes": 60,
      "scoring": {"scale_to": 50}}, 0, 60),
    ("mini_tonghop", "Đề mini tổng hợp 30 câu", "Kiểm tra nhanh: 10 câu Toán, 10 câu Văn, 10 câu Khoa học · 45 phút.",
     {"sections": [{"key": "tonghop", "title": "Tổng hợp", "pools": [
         {"subjects": ["math"], "types": ["single_choice"], "count": 10},
         {"subjects": ["literature"], "types": ["single_choice"], "count": 10},
         {"subjects": SCIENCE, "types": ["single_choice"], "count": 10}], "order": "shuffled"}],
      "timing": "global", "duration_minutes": 45, "scoring": {"scale_to": 10}}, 0, 70),
]

PRODUCTS = [
    ("pack5", "Gói 5 lượt thi thử", "5 lượt làm bất kỳ đề thi thử có phí, dùng trong 90 ngày.", 80000, 5, 90, False),
    ("pass30", "Gói 30 ngày không giới hạn", "Làm không giới hạn mọi đề thi thử có phí trong 30 ngày.", 199000, None,
     30, False),
]


# Practice plans. Price, duration, benefits and availability are edited in Admin → Gói & giá; the seed
# only creates missing rows and never overwrites what an admin changed.
PLANS = [
    ("FREE", "Miễn phí", "Luyện tập một phần ngân hàng câu hỏi của mỗi môn.", 0, None, 10,
     ["Luyện tập giới hạn số câu mỗi môn", "Làm các đề thi thử miễn phí", "Lưu câu hỏi, xem lịch sử và lời giải"]),
    ("PRO", "Gói Pro", "Luyện tập toàn bộ ngân hàng câu hỏi đã kiểm duyệt của mọi môn.", 300000, 365, 20,
     ["Toàn bộ ngân hàng câu hỏi đủ điều kiện của mọi môn", "Luyện tập không giới hạn theo môn và dạng câu",
      "Ôn lại câu sai, câu chưa làm trên toàn bộ ngân hàng"]),
]


def seed(db: Session) -> dict:
    out = {"subjects": 0, "aliases": 0, "blueprints": 0, "products": 0, "plans": 0}
    for code, name, desc, price, days, order, benefits in PLANS:
        if db.scalar(select(Plan).where(Plan.code == code)) is None:
            db.add(Plan(code=code, name=name, description=desc, price_vnd=price, duration_days=days, is_active=True,
                        features={"benefits": benefits}, sort_order=order))
            out["plans"] += 1
    bank = ensure_bank(db)
    for code, name, short, color, order, upstream in SUBJECTS:
        if db.get(Subject, code) is None:
            db.add(Subject(code=code, name=name, short_name=short, color=color, sort_order=order))
            out["subjects"] += 1
    db.flush()
    for code, *_, upstream in SUBJECTS:
        for val in upstream:
            if db.get(SubjectAlias, (bank.id, val)) is None:
                db.add(SubjectAlias(bank_id=bank.id, source_value=val, subject_code=code))
                out["aliases"] += 1
    for code, name, desc, cfg, price, order in BLUEPRINTS:
        if db.scalar(select(ExamBlueprint).where(ExamBlueprint.code == code)) is None:
            parse_config(cfg)  # validate
            db.add(ExamBlueprint(code=code, name=name, description=desc, kind="random", config=cfg, price_vnd=price,
                                 access="paid" if price > 0 else "free", is_published=True, sort_order=order))
            out["blueprints"] += 1
    for code, name, desc, price, attempts, days, active in PRODUCTS:
        if db.scalar(select(Product).where(Product.code == code)) is None:
            db.add(Product(code=code, name=name, description=desc, price_vnd=price, attempts=attempts,
                           duration_days=days, is_active=active))
            out["products"] += 1
    db.commit()
    return out
