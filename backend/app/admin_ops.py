"""Admin operations shared by the API and the CLI."""
import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppSetting, AuditLog, Question, QuestionCorrection, User


def audit(db: Session, actor: User | None, action: str, entity: str, entity_id, data: dict | None = None):
    db.add(AuditLog(actor_user_id=actor.id if actor else None, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id is not None else None, data=data or {}))


def export_corrections(db: Session, out_path: str, mark: bool = True) -> int:
    """Write proposed corrections in the upstream proposal format (docs/EDITORIAL_WORKFLOW.md of
    HSA-question-bank): question_id, field, new_value, evidence, editor, date, note."""
    rows = db.execute(select(QuestionCorrection, User.email).outerjoin(User, User.id == QuestionCorrection.created_by)
                      .where(QuestionCorrection.status == "proposed").order_by(QuestionCorrection.id)).all()
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for c, email in rows:
            f.write(json.dumps({"question_id": c.external_id, "field": c.field, "new_value": c.new_value,
                                "old_value": c.old_value, "evidence": c.evidence, "editor": email or "hsa-app",
                                "date": c.created_at.date().isoformat(), "note": c.note,
                                "app_correction_id": c.id, "app_report_id": c.report_id}, ensure_ascii=False) + "\n")
    if mark:
        now = dt.datetime.now(dt.timezone.utc)
        for c, _ in rows:
            c.status, c.exported_at = "exported", now
        db.commit()
    return len(rows)
