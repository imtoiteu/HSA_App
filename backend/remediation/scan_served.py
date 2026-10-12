"""Served questions with the H S A watermark artifact or a solution that paraphrases another option than the key."""
from sqlalchemy import select
from sqlalchemy.orm import lazyload
from app.db import SessionLocal
from app.models import Question, QuestionVersion
from app.review import remediate as M
db = SessionLocal()
wm, contra = [], []
rows = db.execute(select(Question.external_id, Question.review_gate, QuestionVersion.content, QuestionVersion.answer)
                  .join(QuestionVersion, QuestionVersion.id == Question.current_version_id)
                  .where(Question.is_served.is_(True)).execution_options(yield_per=2000))
for ext, gate, c, a in rows:
    if M.strip_hsa_watermark(c)[1]:
        wm.append((ext, gate))
    r = M.solution_contradicts(a, c)
    if r:
        contra.append((ext, gate, r))
print("watermark", len(wm), "of which review-promoted", sum(1 for _, g in wm if g == "ready"))
print("contradiction", len(contra), "of which review-promoted", sum(1 for _, g, _ in contra if g == "ready"))
print("WM_PROMOTED", ",".join(e for e, g in wm if g == "ready"))
print("CONTRA_PROMOTED", ",".join(e for e, g, _ in contra if g == "ready"))
for e, g, r in contra[:12]:
    print(" ", e, g, r)
