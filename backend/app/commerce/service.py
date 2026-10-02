"""Orders, payment confirmation (idempotent), PRO subscriptions and mock-exam entitlements.

Two independent things are sold:
  * the PRO practice plan (kind "pro"): one paid order = one validity period (plan_subscription);
  * mock exams (kind "exam" for one blueprint, "product" for attempt bundles/passes): entitlements.

State machine of an order (every transition is recorded in payment_order_event):
    pending ──paid──▶ paid          webhook match or admin reconciliation
    pending ──ttl───▶ expired ──paid  (a late transfer is still honoured)
    pending ──user──▶ cancelled
    pending/expired ──admin──▶ cancelled | failed
    paid    ──admin─▶ refunded      (the subscription / entitlement it granted is revoked)
Amount, transfer reference and destination account are fixed when the order is created (DB trigger).
Confirmation is idempotent: the order row is locked and moves to `paid` once; a provider transaction
id is stored once (unique constraint); subscriptions and entitlements are unique per order.
"""
import datetime as dt
import re
import secrets

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (AuditLog, Entitlement, EntitlementUsage, ExamBlueprint, PaymentOrder, PaymentOrderEvent,
                      PaymentTransaction, Plan, PlanSubscription, Product, User)
from ..settings_store import get_setting
from .providers import IncomingTxn

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CONFIRMABLE_BY_WEBHOOK = ("pending", "expired")
CONFIRMABLE_BY_ADMIN = ("pending", "expired", "cancelled", "failed")


class CommerceError(Exception):
    def __init__(self, message, status=400, code="invalid", **extra):
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _prefix(pay: dict) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pay.get("code_prefix") or "HSA").upper())[:6] or "HSA"


def new_code(prefix: str) -> str:
    return (re.sub(r"[^A-Z0-9]", "", prefix.upper())[:6] or "HSA") + "".join(
        secrets.choice(CODE_ALPHABET) for _ in range(8))


def find_code(content: str, prefix: str) -> str | None:
    """Locate an order code in a free-form bank transfer description (spaces/case/punctuation vary)."""
    flat = re.sub(r"[^A-Z0-9]", "", (content or "").upper())
    p = re.sub(r"[^A-Z0-9]", "", prefix.upper())[:6] or "HSA"
    m = re.search(p + "[" + CODE_ALPHABET + "]{8}", flat)
    return m.group(0) if m else None


def event(db: Session, order: PaymentOrder, from_status: str | None, to_status: str | None, source: str,
          actor_id: int | None = None, note: str | None = None, data: dict | None = None):
    db.add(PaymentOrderEvent(order_id=order.id, from_status=from_status, to_status=to_status, source=source,
                             actor_user_id=actor_id, note=note, data=data or {}))


# ------------------------------------------------------------------------------------------------
# prices (always computed server-side; the client never sends an amount)
# ------------------------------------------------------------------------------------------------
def exam_price(db: Session, bp: ExamBlueprint) -> dict:
    """{"paid", "price_vnd" (what a purchase costs now), "list_price_vnd", "promo"}."""
    if bp.access != "paid":
        return {"paid": False, "price_vnd": 0, "list_price_vnd": 0, "promo": False}
    ms = get_setting(db, "mock_exams")
    list_price = bp.price_vnd or int(ms.get("default_price_vnd") or 0)
    price = bp.promo_price_vnd or list_price
    return {"paid": True, "price_vnd": price, "list_price_vnd": list_price,
            "promo": bool(bp.promo_price_vnd and bp.promo_price_vnd != list_price)}


def get_plan(db: Session, code: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.code == code.upper()))


def bank_destination(pay: dict) -> dict | None:
    if not (pay.get("bank_bin") and pay.get("account_number")):
        return None
    return {"bank_name": pay.get("bank_name") or "", "bank_bin": pay["bank_bin"],
            "account_number": pay["account_number"], "account_name": pay.get("account_name") or ""}


# ------------------------------------------------------------------------------------------------
# orders
# ------------------------------------------------------------------------------------------------
def create_order(db: Session, user: User, *, plan: Plan | None = None, product: Product | None = None,
                 blueprint: ExamBlueprint | None = None, now=None) -> PaymentOrder:
    now = now or utcnow()
    pay = get_setting(db, "payment")
    if not pay.get("enabled", True):
        raise CommerceError("Thanh toán đang tạm dừng.", 503, "payments_disabled")
    dest = bank_destination(pay)
    if dest is None:
        raise CommerceError("Tài khoản nhận thanh toán chưa được cấu hình. Vui lòng liên hệ quản trị viên.", 503,
                            "payment_not_configured")
    if plan is not None:
        if not plan.is_active or plan.code == "FREE" or plan.price_vnd <= 0 or not plan.duration_days:
            raise CommerceError("Gói này hiện không được bán.", 404, "not_found")
        kind, amount, list_price = "pro", plan.price_vnd, plan.price_vnd
        snap = {"kind": "pro", "name": f"{plan.name} – {plan.duration_days} ngày", "plan_code": plan.code,
                "duration_days": plan.duration_days, "price_vnd": plan.price_vnd}
    elif product is not None:
        if not product.is_active:
            raise CommerceError("Gói không còn được bán.", 404, "not_found")
        kind, amount, list_price = "product", product.price_vnd, product.price_vnd
        snap = {"kind": "product", "name": product.name, "attempts": product.attempts,
                "duration_days": product.duration_days, "blueprint_ids": list(product.blueprint_ids or [])}
    elif blueprint is not None:
        pr = exam_price(db, blueprint)
        if not pr["paid"] or pr["price_vnd"] <= 0:
            raise CommerceError("Đề thi này miễn phí.", 400, "free")
        if not get_setting(db, "mock_exams").get("paid_enabled", True):
            raise CommerceError("Hiện chưa mở bán đề thi có phí.", 503, "paid_exams_disabled")
        kind, amount, list_price = "exam", pr["price_vnd"], pr["list_price_vnd"]
        n = blueprint.attempts_per_purchase or 1
        snap = {"kind": "attempt", "name": f"{n} lượt thi: {blueprint.name}", "attempts": n, "duration_days": None,
                "blueprint_ids": [blueprint.id]}
    else:
        raise CommerceError("Thiếu sản phẩm.")
    # reuse an identical pending order (same product, price and destination) instead of piling up new ones
    conds = [PaymentOrder.user_id == user.id, PaymentOrder.status == "pending", PaymentOrder.expires_at > now,
             PaymentOrder.kind == kind, PaymentOrder.amount_vnd == amount,
             PaymentOrder.plan_code == plan.code if plan else PaymentOrder.plan_code.is_(None),
             PaymentOrder.product_id == product.id if product else PaymentOrder.product_id.is_(None),
             PaymentOrder.blueprint_id == blueprint.id if blueprint else PaymentOrder.blueprint_id.is_(None)]
    existing = db.scalar(select(PaymentOrder).where(*conds))
    if existing and (existing.bank_snapshot or {}) == dest:
        return existing
    prefix = _prefix(pay)
    ttl = int(pay.get("order_ttl_minutes") or 30)
    for _ in range(5):
        code = new_code(prefix)
        order = PaymentOrder(code=code, user_id=user.id, kind=kind, plan_code=plan.code if plan else None,
                             product_id=product.id if product else None,
                             blueprint_id=blueprint.id if blueprint else None, product_snapshot=snap,
                             amount_vnd=amount, list_price_vnd=list_price,
                             transfer_content=f"{prefix} {code[len(prefix):]}", bank_snapshot=dest,
                             status="pending", expires_at=now + dt.timedelta(minutes=ttl))
        try:
            with db.begin_nested():
                db.add(order)
                db.flush()
            event(db, order, None, "pending", "user", user.id, data={"amount_vnd": amount, "kind": kind})
            db.commit()
            return order
        except IntegrityError:
            continue
    raise CommerceError("Không tạo được mã đơn hàng, vui lòng thử lại.", 500, "code_collision")


def _lock(db: Session, order: PaymentOrder) -> PaymentOrder:
    return db.scalar(select(PaymentOrder).where(PaymentOrder.id == order.id).with_for_update()
                     .execution_options(populate_existing=True))


def cancel_order(db: Session, order: PaymentOrder, actor: User | None = None, note: str | None = None,
                 source: str = "user"):
    o = _lock(db, order)
    allowed = ("pending",) if source == "user" else ("pending", "expired")
    if o.status not in allowed:
        db.rollback()
        raise CommerceError("Chỉ huỷ được đơn đang chờ thanh toán.", 409, "not_pending")
    prev, o.status = o.status, "cancelled"
    event(db, o, prev, "cancelled", source, actor.id if actor else None, note)
    if source == "admin":
        db.add(AuditLog(actor_user_id=actor.id, action="order_cancelled", entity="payment_order", entity_id=o.code,
                        data={"from": prev, "note": note}))
    db.commit()


def fail_order(db: Session, order: PaymentOrder, admin: User, note: str):
    """Admin: the transfer was rejected/returned or the customer will not pay (no entitlement)."""
    o = _lock(db, order)
    if o.status not in ("pending", "expired"):
        db.rollback()
        raise CommerceError(f"Đơn đang ở trạng thái {o.status}.", 409, "not_pending")
    prev, o.status = o.status, "failed"
    o.note = note
    event(db, o, prev, "failed", "admin", admin.id, note)
    db.add(AuditLog(actor_user_id=admin.id, action="order_failed", entity="payment_order", entity_id=o.code,
                    data={"from": prev, "note": note}))
    db.commit()


def add_note(db: Session, order: PaymentOrder, admin: User, note: str):
    event(db, order, None, None, "admin", admin.id, note)
    db.add(AuditLog(actor_user_id=admin.id, action="order_note", entity="payment_order", entity_id=order.code,
                    data={"note": note}))
    db.commit()


def expire_orders(db: Session, now=None) -> int:
    now = now or utcnow()
    rows = db.scalars(select(PaymentOrder).where(PaymentOrder.status == "pending", PaymentOrder.expires_at <= now)
                      .with_for_update(skip_locked=True)).all()
    for o in rows:
        o.status = "expired"
        event(db, o, "pending", "expired", "system", note="hết thời hạn thanh toán")
    db.commit()
    return len(rows)


# ------------------------------------------------------------------------------------------------
# PRO subscriptions
# ------------------------------------------------------------------------------------------------
def pro_until(db: Session, user_id: int, now=None) -> dt.datetime | None:
    """End of the user's current or queued PRO validity (None when not PRO now and nothing queued)."""
    now = now or utcnow()
    return db.scalar(select(func.max(PlanSubscription.expires_at)).where(
        PlanSubscription.user_id == user_id, PlanSubscription.status == "active", PlanSubscription.expires_at > now))


def active_subscription(db: Session, user_id: int, now=None) -> PlanSubscription | None:
    now = now or utcnow()
    return db.scalar(select(PlanSubscription).where(
        PlanSubscription.user_id == user_id, PlanSubscription.status == "active", PlanSubscription.starts_at <= now,
        PlanSubscription.expires_at > now).order_by(PlanSubscription.expires_at.desc()).limit(1))


def is_pro(db: Session, user: User | None, now=None) -> bool:
    return bool(user) and active_subscription(db, user.id, now) is not None


def _new_period(db: Session, user_id: int, days: int, now) -> tuple[dt.datetime, dt.datetime]:
    """A new period starts now, or when the user's current PRO ends (renewal never loses paid days)."""
    db.execute(select(User.id).where(User.id == user_id).with_for_update())  # serialise grants per user
    start = max(now, pro_until(db, user_id, now) or now)
    return start, start + dt.timedelta(days=days)


def grant_pro_for_order(db: Session, order: PaymentOrder, now) -> PlanSubscription:
    snap = order.product_snapshot
    start, end = _new_period(db, order.user_id, int(snap["duration_days"]), now)
    sub = PlanSubscription(user_id=order.user_id, plan_code=order.plan_code or "PRO", plan_snapshot=snap,
                           starts_at=start, expires_at=end, status="active", source="order", order_id=order.id)
    db.add(sub)
    db.flush()
    return sub


def admin_grant_pro(db: Session, admin: User, user: User, days: int, note: str, now=None) -> PlanSubscription:
    now = now or utcnow()
    plan = get_plan(db, "PRO")
    start, end = _new_period(db, user.id, days, now)
    sub = PlanSubscription(user_id=user.id, plan_code="PRO", plan_snapshot={
        "kind": "pro", "name": f"{plan.name if plan else 'Pro'} – cấp bởi quản trị ({days} ngày)", "duration_days": days,
        "price_vnd": 0}, starts_at=start, expires_at=end, status="active", source="admin_grant",
        granted_by=admin.id, note=note)
    db.add(sub)
    db.flush()
    db.add(AuditLog(actor_user_id=admin.id, action="pro_granted", entity="app_user", entity_id=str(user.id),
                    data={"subscription_id": sub.id, "days": days, "starts_at": start.isoformat(),
                          "expires_at": end.isoformat(), "note": note}))
    db.commit()
    return sub


def revoke_subscription(db: Session, admin: User, sub: PlanSubscription, reason: str, now=None):
    now = now or utcnow()
    if sub.status != "active":
        raise CommerceError("Quyền này đã bị thu hồi.", 409, "already_revoked")
    sub.status, sub.revoked_by, sub.revoked_at, sub.revoke_reason = "revoked", admin.id, now, reason
    db.add(AuditLog(actor_user_id=admin.id, action="pro_revoked", entity="plan_subscription", entity_id=str(sub.id),
                    data={"user_id": sub.user_id, "reason": reason, "expires_at": sub.expires_at.isoformat()}))
    db.commit()


# ------------------------------------------------------------------------------------------------
# confirmation
# ------------------------------------------------------------------------------------------------
def _grant_for_order(db: Session, order: PaymentOrder, now):
    if order.kind == "pro":
        return grant_pro_for_order(db, order, now)
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
              admin_id: int | None = None, note: str | None = None, source: str = "webhook",
              allowed: tuple = CONFIRMABLE_BY_WEBHOOK) -> bool:
    """Move an order to paid exactly once and grant what it sells. Returns True if this call did it."""
    now = now or utcnow()
    o = _lock(db, order)
    if o is None or o.status not in allowed:
        return False
    prev = o.status
    o.status, o.paid_at, o.paid_amount_vnd, o.provider, o.confirmed_by = "paid", now, amount, provider, admin_id
    if note:
        o.note = note
    granted = _grant_for_order(db, o, now)
    event(db, o, prev, "paid", source, admin_id, note,
          {"provider": provider, "amount_vnd": amount, "granted": type(granted).__name__, "granted_id": granted.id})
    db.add(AuditLog(actor_user_id=admin_id, action="order_paid", entity="payment_order", entity_id=o.code,
                    data={"provider": provider, "amount": amount, "from": prev}))
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
    code = find_code(txn.content, _prefix(pay))
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.code == code).with_for_update()) if code else None
    if order is None:
        row.note = "no order code found" if not code else f"unknown order code {code}"
    else:
        row.order_id = order.id
        if order.status == "paid":
            row.status, row.note = "late", "order already paid (possible double payment — review)"
        elif order.status in ("cancelled", "refunded", "failed"):
            row.status, row.note = "late", f"order is {order.status} — review manually"
        elif txn.amount_vnd < order.amount_vnd:
            row.status, row.note = "underpaid", f"received {txn.amount_vnd} < {order.amount_vnd}"
        else:
            mark_paid(db, order, provider=txn.provider, amount=txn.amount_vnd, now=now,
                      note=f"{txn.provider} transaction {txn.provider_txn_id}")
            row.status = "matched"
    db.commit()
    return row


def admin_confirm(db: Session, order: PaymentOrder, admin: User, amount: int | None, note: str | None,
                  now=None) -> bool:
    """Manual reconciliation (e.g. transfer seen in the bank app). Idempotent per order."""
    now = now or utcnow()
    txn_id = f"order:{order.code}"
    if db.scalar(select(PaymentTransaction).where(PaymentTransaction.provider == "manual",
                                                  PaymentTransaction.provider_txn_id == txn_id)):
        return False
    amount = amount or order.amount_vnd
    done = mark_paid(db, order, provider="manual", amount=amount, now=now, admin_id=admin.id, note=note,
                     source="admin", allowed=CONFIRMABLE_BY_ADMIN)
    if not done:
        db.rollback()
        return False
    db.add(PaymentTransaction(provider="manual", provider_txn_id=txn_id, amount_vnd=amount,
                              content=f"manual confirmation by {admin.email}", occurred_at=now,
                              raw={"note": note}, order_id=order.id, status="matched", note=note))
    try:
        db.commit()
    except IntegrityError:  # a concurrent confirmation won
        db.rollback()
        return False
    return True


def refund(db: Session, order: PaymentOrder, admin: User, note: str | None, now=None):
    now = now or utcnow()
    o = _lock(db, order)
    if o.status != "paid":
        db.rollback()
        raise CommerceError("Chỉ hoàn tiền đơn đã thanh toán.", 409, "not_paid")
    o.status = "refunded"
    o.note = note
    for ent in db.scalars(select(Entitlement).where(Entitlement.order_id == o.id)):
        ent.status = "revoked"
    for sub in db.scalars(select(PlanSubscription).where(PlanSubscription.order_id == o.id,
                                                          PlanSubscription.status == "active")):
        sub.status, sub.revoked_by, sub.revoked_at, sub.revoke_reason = "revoked", admin.id, now, f"refund: {note or ''}"
    event(db, o, "paid", "refunded", "admin", admin.id, note)
    db.add(AuditLog(actor_user_id=admin.id, action="order_refunded", entity="payment_order", entity_id=o.code,
                    data={"note": note}))
    db.commit()


# ------------------------------------------------------------------------------------------------
# mock-exam entitlements
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


def _pro_covers_exams(db: Session, user: User, now) -> bool:
    return bool(get_setting(db, "mock_exams").get("pro_includes_paid_exams")) and is_pro(db, user, now)


def access_status(db: Session, user: User | None, bp: ExamBlueprint, now=None) -> dict:
    now = now or utcnow()
    pr = exam_price(db, bp)
    if not pr["paid"]:
        return {"free": True, "allowed": True}
    base = {"free": False, "price_vnd": pr["price_vnd"], "list_price_vnd": pr["list_price_vnd"], "promo": pr["promo"],
            "attempts_per_purchase": bp.attempts_per_purchase,
            "on_sale": bool(get_setting(db, "mock_exams").get("paid_enabled", True))}
    if user is None:
        return dict(base, allowed=False)
    if user.role == "admin":
        return dict(base, allowed=True, admin=True)
    if _pro_covers_exams(db, user, now):
        return dict(base, allowed=True, unlimited=True, via_pro=True, remaining_attempts=None)
    ents = _usable(db, user.id, bp.id, now)
    remaining = None if any(e.attempts_total is None for e in ents) else \
        sum(e.attempts_total - e.attempts_used for e in ents)
    return dict(base, allowed=bool(ents), remaining_attempts=remaining, unlimited=bool(ents) and remaining is None)


def consume(db: Session, user: User, bp: ExamBlueprint, session_id, now=None) -> Entitlement | None:
    """Spend one attempt for a paid blueprint inside the caller's transaction (row-locked)."""
    now = now or utcnow()
    if not exam_price(db, bp)["paid"] or user.role == "admin" or _pro_covers_exams(db, user, now):
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


# ------------------------------------------------------------------------------------------------
# payloads
# ------------------------------------------------------------------------------------------------
def order_payload(db: Session, order: PaymentOrder, with_qr: bool = True) -> dict:
    from .vietqr import build_payload, qr_svg
    pay = get_setting(db, "payment")
    out = {"code": order.code, "kind": order.kind, "status": order.status, "amount_vnd": order.amount_vnd,
           "list_price_vnd": order.list_price_vnd, "name": order.product_snapshot.get("name"),
           "created_at": order.created_at.isoformat(), "expires_at": order.expires_at.isoformat(),
           "paid_at": order.paid_at.isoformat() if order.paid_at else None,
           "transfer_content": order.transfer_content or order.code, "blueprint_id": order.blueprint_id,
           "product_id": order.product_id, "plan_code": order.plan_code,
           "duration_days": order.product_snapshot.get("duration_days"),
           "instructions": pay.get("instructions") or "", "bank": None, "qr_svg": None, "qr_payload": None}
    dest = order.bank_snapshot  # the account the customer was asked to pay, never today's settings
    if dest and dest.get("account_number"):
        out["bank"] = {"bank_name": dest.get("bank_name"), "account_number": dest["account_number"],
                       "account_name": dest.get("account_name") or None, "bin": dest.get("bank_bin")}
        if with_qr and order.status == "pending" and pay.get("qr_enabled", True) and dest.get("bank_bin"):
            payload = build_payload(dest["bank_bin"], dest["account_number"], order.amount_vnd,
                                    order.transfer_content or order.code)
            out["qr_payload"] = payload
            out["qr_svg"] = qr_svg(payload)
    if order.kind == "pro" and order.status == "paid":
        sub = db.scalar(select(PlanSubscription).where(PlanSubscription.order_id == order.id))
        if sub:
            out["subscription"] = {"starts_at": sub.starts_at.isoformat(), "expires_at": sub.expires_at.isoformat(),
                                   "status": sub.status}
    return out


def subscription_payload(sub: PlanSubscription, now=None) -> dict:
    now = now or utcnow()
    state = sub.status if sub.status != "active" else (
        "expired" if sub.expires_at <= now else "scheduled" if sub.starts_at > now else "active")
    return {"id": sub.id, "plan_code": sub.plan_code, "name": (sub.plan_snapshot or {}).get("name"),
            "starts_at": sub.starts_at.isoformat(), "expires_at": sub.expires_at.isoformat(), "state": state,
            "source": sub.source, "order_id": sub.order_id, "note": sub.note, "revoke_reason": sub.revoke_reason,
            "revoked_at": sub.revoked_at.isoformat() if sub.revoked_at else None,
            "created_at": sub.created_at.isoformat() if sub.created_at else None}
