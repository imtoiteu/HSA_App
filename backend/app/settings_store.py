"""Admin-editable settings stored in app_setting (JSON). Secrets never live here (env only)."""
import copy

from sqlalchemy.orm import Session

from .models import AppSetting

DEFAULTS: dict = {
    "serving_policy": None,  # filled from sync.policy.DEFAULT_POLICY on read
    "payment": {
        "enabled": True,
        "order_ttl_minutes": 30,
        "code_prefix": "HSA",
        # receiving account for VietQR transfers (public information shown to payers, not a secret)
        "bank_bin": "",            # NAPAS BIN, e.g. 970436 (Vietcombank)
        "bank_name": "",
        "account_number": "",
        "account_name": "",
        "providers": ["manual"],   # enabled confirmation channels: manual | sepay | casso | generic
    },
    "practice": {
        "free": True,              # practice modes are free; exams follow each blueprint's price
        "max_questions": 100,
        "default_questions": 20,
    },
    "site": {
        "name": "Luyện thi HSA",
        "support_contact": "",
        "announcement": "",
    },
}


def get_setting(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    if key == "serving_policy":
        from .sync.policy import merged_policy
        return merged_policy(row.value if row else None)
    base = copy.deepcopy(DEFAULTS.get(key) or {})
    if row and isinstance(row.value, dict):
        base.update(row.value)
    return base


def set_setting(db: Session, key: str, value: dict, user_id: int | None = None) -> dict:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=user_id)
        db.add(row)
    else:
        row.value = value
        row.updated_by = user_id
    db.flush()
    return get_setting(db, key)
