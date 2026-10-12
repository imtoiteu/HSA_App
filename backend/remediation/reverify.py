"""Re-check promoted questions against their source with the current rules (read-only)."""
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Question, QuestionReview, SourceCache
from app.review import remediate as M
from app.review.sources import default_store

db = SessionLocal()
store = default_store()
bad = 0
for q in db.scalars(select(Question).where(Question.review_gate == "ready")):
    v = q.current_version
    sc = db.get(SourceCache, (q.provenance or {}).get("document_id"))
    sv = M.check_source_text(v.content, store.page_texts(sc.blob_sha256))
    if not sv.ok:
        bad += 1
        print("FAIL", q.external_id, sv.reason)
print("rechecked; failing:", bad)
