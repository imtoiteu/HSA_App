"""Orders, payment confirmation (idempotent) and entitlements.

State machine of an order:  pending ──paid──▶ paid          (webhook match or admin reconciliation)
                            pending ──ttl───▶ expired ──paid (late transfer is still honoured)
                            pending ──user──▶ cancelled
                            paid    ──admin─▶ refunded (entitlement revoked)
Confirmation is idempotent: a provider transaction id is stored once (unique constraint) and an
order can only move to `paid` once (conditional UPDATE + unique entitlement per order).
"""
import datetime as dt
import re
import secrets

from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (AuditLog, Entitlement, EntitlementUsage, ExamBlueprint, PaymentOrder, PaymentTransaction,
                      Product, User)
from ..settings_store import get_setting
from .providers import IncomingTxn

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


class CommerceError(Exception):
    def __init__(self, message, status=400, code="invalid"):
        super().__init__(message)
        self.status, self.code = status, code


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def new_code(prefix: str) -> str:
    return (re.sub(r"[^A-Z0-9]", "", prefix.upper())[:6] or "HSA") + "".join(
        secrets.choice(CODE_ALPHABET) for _ in range(8))


def find_code(content: str, prefix: str) -> str | None:
    """Locate an order code in a free-form bank transfer description (spaces/case/punctuation vary)."""
    flat = re.sub(r"[^A-Z0-9]", "", (content or "").upper())
    p = re.sub(r"[^A-Z0-9]", "", prefix.upper())[:6] or "HSA"
    m = re.search(p + "[" + CODE_ALPHABET + "]{8}", flat)
    return m.group(0) if m else None


# ------------------------------------------------------------------------------------------------
# orders
# ------------------------------------------------------------------------------------------------
def create_order(db: Session, user: User, *, product: Product | None = None, blueprint: ExamBlueprint | None = None,
                 now=None) -> PaymentOrder:
    now = now or utcnow()
    pay = get_setting(db, "payment")
    if not pay.get("enabled", True):
        raise CommerceError("Thanh toán đang tạm dừng.", 503, "payments_disabled")
    if product is not None:
        if not product.is_active:
            raise CommerceError("Gói không còn được bán.", 404, "not_found")
        amount = product.price_vnd
        snap = {"kind": "product", "name": product.name, "attempts": product.attempts,
                "duration_days": product.duration_days, "blueprint_ids": list(product.blueprint_ids or [])}
    elif blueprint is not None:
        if blueprint.price_vnd <= 0:
            raise CommerceError("Đề thi này miễn phí.", 400, "free")
        amount = blueprint.price_vnd
        snap = {"kind": "attempt", "name": f"1 lượt thi: {blueprint.name}", "attempts": 1, "duration_days": None,
                "blueprint_ids": [blueprint.id]}
    else:
        raise CommerceError("Thiếu sản phẩm.")
    # reuse an identical pending order instead of piling up new ones
    conds = [PaymentOrder.user_id == user.id, PaymentOrder.status == "pending", PaymentOrder.expires_at > now,
             PaymentOrder.amount_vnd == amount,
             PaymentOrder.product_id == product.id if product else PaymentOrder.product_id.is_(None),
             PaymentOrder.blueprint_id == blueprint.id if blueprint else PaymentOrder.blueprint_id.is_(None)]
    existing = db.scalar(select(PaymentOrder).where(*conds))
    if existing:
        return existing
    for _ in range(5):
        order = PaymentOrder(code=new_code(pay.get("code_prefix", "HSA")), user_id=user.id,
                             product_id=product.id if product else None, blueprint_id=blueprint.id if blueprint else None,
                             product_snapshot=snap, amount_vnd=amount, status="pending",
                             expires_at=now + dt.timedelta(minutes=int(pay.get("order_ttl_minutes", 30))))
        try:
            with db.begin_nested():
                db.add(order)
                db.flush()
            db.commit()
            return order
        except IntegrityError:
            continue
    raise CommerceError("Không tạo được mã đơn hàng, vui lòng thử lại.", 500, "code_collision")


def cancel_order(db: Session, order: PaymentOrder):
    if order.status != "pending":
        raise CommerceError("Chỉ huỷ được đơn đang chờ thanh toán.", 409, "not_pending")
    order.status = "cancelled"
    db.commit()


def expire_orders(db: Session, now=None) -> int:
    now = now or utcnow()
    r = db.execute(update(PaymentOrder).where(PaymentOrder.status == "pending", PaymentOrder.expires_at <= now)
                   .values(status="expired"))
    db.commit()
    return r.rowcount


def _grant_for_order(db: Session, order: PaymentOrder, now) -> Entitlement:
    snap = order.product_snapshot
    ent = Entitlement(user_id=order.user_id, product_id=order.product_id, order_id=order.id,
                      blueprint_ids=list(snap.get("blueprint_ids") or []), attempts_total=snap.get("attempts"),
                      attempts_used=0, valid_from=now,
                      valid_until=now + dt.timedelta(days=snap["duration_days"]) if snap.get("duration_days") else None,
                      status="active", note=snap.get("name"))
    db.add(ent)
    db.flush()
    return ent


def mark_paid(db: Session, order: PaymentOrder, *, provider: str, amount: int, now=None,
              admin_id: int | None = None, note: str | None = None) -> bool:
    """Move an order to paid exactly once and grant its entitlement. Returns True if this call did it."""
    now = now or utcnow()
    r = db.execute(update(PaymentOrder).where(PaymentOrder.id == order.id,
                                              PaymentOrder.status.in_(["pending", "expired"]))
                   .values(status="paid", paid_at=now, paid_amount_vnd=amount, provider=provider,
                           confirmed_by=admin_id, note=note))
    if r.rowcount != 1:
        return False
    db.refresh(order)
    _grant_for_order(db, order, now)
    db.add(AuditLog(actor_user_id=admin_id, action="order_paid", entity="payment_order", entity_id=order.code,
                    data={"provider": provider, "amount": amount}))
    return True


def ingest(db: Session, txn: IncomingTxn, now=None) -> PaymentTransaction:
    """Record one provider transaction and confirm the matching order. Safe to call repeatedly."""
    now = now or utcnow()
    existing = db.scalar(select(PaymentTransaction).where(PaymentTransaction.provider == txn.provider,
                                                          PaymentTransaction.provider_txn_id == txn.provider_txn_id))
    if existing:
        return existing
    pay = get_setting(db, "payment")
    row = PaymentTransaction(provider=txn.provider, provider_txn_id=txn.provider_txn_id, amount_vnd=txn.amount_vnd,
                             content=txn.content[:1000], occurred_at=txn.occurred_at, raw=txn.raw, status="unmatched")
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:  # concurrent duplicate delivery
        db.rollback()
        return db.scalar(select(PaymentTransaction).where(PaymentTransaction.provider == txn.provider,
                                                          PaymentTransaction.provider_txn_id == txn.provider_txn_id))
    if not txn.incoming:
        row.status, row.note = "ignored", "outgoing transfer"
        db.commit()
        return row
    code = find_code(txn.content, pay.get("code_prefix", "HSA"))
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.code == code).with_for_update()) if code else None
    if order is None:
        row.note = "no order code found" if not code else f"unknown order code {code}"
    else:
        row.order_id = order.id
        if order.status == "paid":
            row.status, row.note = "late", "order already paid (possible double payment — review)"
        elif order.status in ("cancelled", "refunded"):
            row.status, row.note = "late", f"order is {order.status} — review manually"
        elif txn.amount_vnd < order.amount_vnd:
            row.status, row.note = "underpaid", f"received {txn.amount_vnd} < {order.amount_vnd}"
        else:
            mark_paid(db, order, provider=txn.provider, amount=txn.amount_vnd, now=now)
            row.status = "matched"
    db.commit()
    return row


def admin_confirm(db: Session, order: PaymentOrder, admin: User, amount: int | None, note: str | None,
                  now=None) -> bool:
    """Manual reconciliation fallback (e.g. transfer seen in the bank app)."""
    now = now or utcnow()
    txn_id = f"order:{order.code}"
    if db.scalar(select(PaymentTransaction).where(PaymentTransaction.provider == "manual",
                                                  PaymentTransaction.provider_txn_id == txn_id)):
        return False
    amount = amount or order.amount_vnd
    db.add(PaymentTransaction(provider="manual", provider_txn_id=txn_id, amount_vnd=amount,
                              content=f"manual confirmation by {admin.email}", occurred_at=now,
                              raw={"note": note}, order_id=order.id, status="matched", note=note))
    done = mark_paid(db, order, provider="manual", amount=amount, now=now, admin_id=admin.id, note=note)
    db.commit()
    return done


def refund(db: Session, order: PaymentOrder, admin: User, note: str | None):
    if order.status != "paid":
        raise CommerceError("Chỉ hoàn tiền đơn đã thanh toán.", 409, "not_paid")
    order.status = "refunded"
    order.note = note
    for ent in db.scalars(select(Entitlement).where(Entitlement.order_id == order.id)):
        ent.status = "revoked"
    db.add(AuditLog(actor_user_id=admin.id, action="order_refunded", entity="payment_order", entity_id=order.code,
                    data={"note": note}))
    db.commit()


# ------------------------------------------------------------------------------------------------
# entitlements
# ------------------------------------------------------------------------------------------------
def _usable(db: Session, user_id: int, blueprint_id: int, now, lock=False):
    stmt = select(Entitlement).where(
        Entitlement.user_id == user_id, Entitlement.status == "active", Entitlement.valid_from <= now,
        or_(Entitlement.valid_until.is_(None), Entitlement.valid_until > now),
        or_(Entitlement.attempts_total.is_(None), Entitlement.attempts_used < Entitlement.attempts_total),
        or_(Entitlement.blueprint_ids == text("'{}'::integer[]"), Entitlement.blueprint_ids.any(blueprint_id)))
    # spend what expires first, then limited-attempt grants before unlimited passes
    stmt = stmt.order_by(Entitlement.valid_until.asc().nulls_last(), Entitlement.attempts_total.asc().nulls_last(),
                         Entitlement.id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalars(stmt).all()


def access_status(db: Session, user: User | None, bp: ExamBlueprint, now=None) -> dict:
    now = now or utcnow()
    if bp.price_vnd <= 0:
        return {"free": True, "allowed": True}
    if user is None:
        return {"free": False, "allowed": False, "price_vnd": bp.price_vnd}
    if user.role == "admin":
        return {"free": False, "allowed": True, "admin": True, "price_vnd": bp.price_vnd}
    ents = _usable(db, user.id, bp.id, now)
    remaining = None if any(e.attempts_total is None for e in ents) else \
        sum(e.attempts_total - e.attempts_used for e in ents)
    return {"free": False, "allowed": bool(ents), "price_vnd": bp.price_vnd, "remaining_attempts": remaining,
            "unlimited": bool(ents) and remaining is None}


def consume(db: Session, user: User, bp: ExamBlueprint, session_id, now=None) -> Entitlement | None:
    """Spend one attempt for a paid blueprint inside the caller's transaction (row-locked)."""
    now = now or utcnow()
    if bp.price_vnd <= 0 or user.role == "admin":
        return None
    ents = _usable(db, user.id, bp.id, now, lock=True)
    if not ents:
        raise CommerceError("Bạn cần mua lượt thi cho đề này.", 402, "payment_required")
    ent = ents[0]
    ent.attempts_used += 1
    db.add(EntitlementUsage(entitlement_id=ent.id, session_id=session_id, blueprint_id=bp.id))
    db.flush()
    return ent


def admin_grant(db: Session, admin: User, user: User, *, blueprint_ids: list[int], attempts: int | None,
                days: int | None, note: str | None, now=None) -> Entitlement:
    now = now or utcnow()
    ent = Entitlement(user_id=user.id, blueprint_ids=blueprint_ids, attempts_total=attempts, attempts_used=0,
                      valid_from=now, valid_until=now + dt.timedelta(days=days) if days else None, status="active",
                      granted_by=admin.id, note=note or "Admin grant")
    db.add(ent)
    db.add(AuditLog(actor_user_id=admin.id, action="entitlement_granted", entity="app_user", entity_id=str(user.id),
                    data={"blueprint_ids": blueprint_ids, "attempts": attempts, "days": days}))
    db.commit()
    return ent


def order_payload(db: Session, order: PaymentOrder, with_qr: bool = True) -> dict:
    from .vietqr import build_payload, qr_svg
    pay = get_setting(db, "payment")
    out = {"code": order.code, "status": order.status, "amount_vnd": order.amount_vnd,
           "name": order.product_snapshot.get("name"), "created_at": order.created_at.isoformat(),
           "expires_at": order.expires_at.isoformat(), "paid_at": order.paid_at.isoformat() if order.paid_at else None,
           "transfer_content": order.code, "blueprint_id": order.blueprint_id, "product_id": order.product_id,
           "bank": None, "qr_svg": None, "qr_payload": None}
    if pay.get("bank_bin") and pay.get("account_number"):
        out["bank"] = {"bank_name": pay.get("bank_name"), "account_number": pay.get("account_number"),
                       "account_name": pay.get("account_name"), "bin": pay.get("bank_bin")}
        if with_qr and order.status == "pending":
            payload = build_payload(pay["bank_bin"], pay["account_number"], order.amount_vnd, order.code)
            out["qr_payload"] = payload
            out["qr_svg"] = qr_svg(payload)
    return out
