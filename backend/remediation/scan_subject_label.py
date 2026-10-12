"""Promoted questions whose subject was accepted on the upstream label alone and fail the current subject rule."""
from sqlalchemy import select, text
from app.db import SessionLocal
from app.models import Question
from app.review import remediate as M
from app.review import service as S
db = SessionLocal()
ids = db.scalars(text("SELECT DISTINCT q.id FROM question q JOIN review_event e ON e.question_id = q.id "
                      "WHERE q.review_gate = 'ready' AND e.action = 'confirm' AND e.note LIKE '%phân loại cuối của ngân hàng%'"))
for qid in ids:
    q = db.get(Question, qid)
    dims = S.get_review(db, q).dims or {}
    if (dims.get("subject") or {}).get("m") == "claude_review":
        continue
    v = M.check_subject(q, q.current_version.content)
    if not v.ok:
        print(q.external_id, q.subject_code, v.reason[:90], M.plain_text(q.current_version.content.get("stem") or [])[:110].replace("\n", " "))
