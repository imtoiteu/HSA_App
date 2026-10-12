"""Promoted questions whose option structure is suspect (arrangement questions parsed as A–D + glued real options)."""
import re, sys
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Question, QuestionVersion
from app.content.render import plain_text
LIST = re.compile(r"(?:^|\s)A[.)]\s.+?(?:\s)B[.)]\s.+?(?:\s)C[.)]\s", re.S)
db = SessionLocal()
out = []
for q in db.scalars(select(Question).where(Question.review_gate == "ready")):
    up = db.get(QuestionVersion, q.upstream_version_id)
    cur = q.current_version
    def bad(v):
        opts = v.content.get("options") or []
        return len(opts) != 4 or any(LIST.search(plain_text(o["content"])) for o in opts)
    if bad(up) or bad(cur):
        out.append(q.external_id)
print(len(out)); print(",".join(out))
