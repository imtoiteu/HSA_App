import json
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..commerce import service as commerce
from ..commerce.providers import ADAPTERS, ProviderAuthError
from ..config import get_settings
from ..db import get_db
from ..models import ExamBlueprint, PaymentOrder, Product, User
from ..settings_store import get_setting
from .common import err

router = APIRouter(prefix="/api", tags=["payments"])
log = logging.getLogger(__name__)


@router.get("/products")
def products(db: Session = Depends(get_db)):
    rows = db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.sort_order, Product.id))
    return {"items": [{"id": p.id, "code": p.code, "name": p.name, "description": p.description,
                       "price_vnd": p.price_vnd, "attempts": p.attempts, "duration_days": p.duration_days,
                       "blueprint_ids": p.blueprint_ids} for p in rows]}


class OrderIn(BaseModel):
    product_id: int | None = None
    blueprint_id: int | None = None


@router.post("/orders")
def create_order(body: OrderIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(auth.current_user)):
    auth.rate_limit(db, f"order:{user.id}", 20, 3600)
    product = db.get(Product, body.product_id) if body.product_id else None
    bp = db.get(ExamBlueprint, body.blueprint_id) if body.blueprint_id and not product else None
    if not product and not (bp and bp.is_published):
        raise err(404, "not_found", "Không tìm thấy sản phẩm.")
    try:
        order = commerce.create_order(db, user, product=product, blueprint=bp)
    except commerce.CommerceError as e:
        raise err(e.status, e.code, str(e))
    return commerce.order_payload(db, order)


def _mine(db: Session, code: str, user: User) -> PaymentOrder:
    o = db.scalar(select(PaymentOrder).where(PaymentOrder.code == code.upper()))
    if o is None or (o.user_id != user.id and user.role != "admin"):
        raise err(404, "not_found", "Không tìm thấy đơn hàng.")
    return o


@router.get("/orders")
def my_orders(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    commerce.expire_orders(db)
    rows = db.scalars(select(PaymentOrder).where(PaymentOrder.user_id == user.id)
                      .order_by(PaymentOrder.created_at.desc()).limit(50))
    return {"items": [commerce.order_payload(db, o, with_qr=False) for o in rows]}


@router.get("/orders/{code}")
def get_order(code: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    o = _mine(db, code, user)
    if o.status == "pending" and o.expires_at <= commerce.utcnow():
        commerce.expire_orders(db)
        db.refresh(o)
    return commerce.order_payload(db, o)


@router.post("/orders/{code}/cancel")
def cancel(code: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    o = _mine(db, code, user)
    try:
        commerce.cancel_order(db, o)
    except commerce.CommerceError as e:
        raise err(e.status, e.code, str(e))
    return commerce.order_payload(db, o, with_qr=False)


@router.get("/entitlements")
def my_entitlements(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    from ..models import Entitlement
    rows = db.scalars(select(Entitlement).where(Entitlement.user_id == user.id).order_by(Entitlement.id.desc()))
    return {"items": [{"id": e.id, "note": e.note, "blueprint_ids": e.blueprint_ids, "attempts_total": e.attempts_total,
                       "attempts_used": e.attempts_used, "valid_until": e.valid_until.isoformat() if e.valid_until else None,
                       "status": e.status, "created_at": e.created_at.isoformat()} for e in rows]}


@router.post("/payments/webhook/{provider}")
async def webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    """Provider → app notification. Authenticated per provider; idempotent per provider txn id."""
    adapter = ADAPTERS.get(provider)
    enabled = get_setting(db, "payment").get("providers") or []
    if adapter is None or provider not in enabled:
        raise err(404, "not_found", "unknown provider")
    body = await request.body()
    if len(body) > 256_000:
        raise err(413, "too_large", "payload too large")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise err(400, "bad_json", "invalid JSON")
    s = get_settings()
    secret = {"sepay": s.sepay_api_key, "casso": s.casso_secure_token, "generic": s.generic_webhook_secret}[provider]
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        txns = adapter(headers, body, payload, secret)
    except ProviderAuthError as ex:
        log.warning("webhook %s rejected: %s", provider, ex)
        raise err(401, "unauthorized", "unauthorized")
    except (KeyError, ValueError, TypeError) as ex:
        raise err(400, "bad_payload", f"invalid payload: {ex}")
    results = []
    for t in txns:
        row = commerce.ingest(db, t)
        results.append({"txn": t.provider_txn_id, "status": row.status})
    log.info("webhook %s processed %s", provider, results)
    return {"success": True, "results": results}
