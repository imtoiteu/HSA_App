"""Promoted questions that no longer pass the current rules (passage / answer evidence / structure)."""
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Question, SourceCache
from app.review import remediate as M
from app.review.sources import default_store
db = SessionLocal(); store = default_store(); up = store.up
out = []
for q in db.scalars(select(Question).where(Question.review_gate == "ready")):
    v = q.current_version
    sc = db.get(SourceCache, (q.provenance or {}).get("document_id"))
    for chk in (lambda: M.check_structure(v.content), lambda: M.check_complete(v.content), lambda: M.check_source_text(v.content, store.page_texts(sc.blob_sha256)),
                lambda: M.check_answer(q, v.answer, v.content, up)):
        r = chk()
        if not r.ok:
            out.append((q.external_id, r.reason)); break
import collections
print(collections.Counter(r.split(":")[0][:50] for _, r in out))
print(",".join(e for e, r in out if "layout" not in r and q is not None))
for e, r in out: print(" ", e, r[:110])
