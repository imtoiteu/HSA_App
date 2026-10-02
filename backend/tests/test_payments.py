"""Commercial access: prices, orders, VietQR, webhooks (idempotent), manual reconciliation, entitlements."""
import datetime as dt
import hashlib
import hmac
import json
import uuid

from sqlalchemy import func, select

from app.commerce import service as commerce
from app.db import SessionLocal
from app.models import Entitlement, ExamBlueprint, PaymentOrder, PaymentTransaction

SMALL = {"sections": [{"key": "t", "title": "T", "pools": [{"subjects": ["math"], "types": ["single_choice"],
                                                             "count": 3}]}], "timing": "global", "duration_minutes": 10}


def paid_bp(price=20000):
    db = SessionLocal()
    bp = ExamBlueprint(code=f"paid_{uuid.uuid4().hex[:6]}", name="Đề có phí", config=SMALL, price_vnd=price,
                       access="paid" if price else "free",
                       is_published=True)
    db.add(bp)
    db.commit()
    bid = bp.id
    db.close()
    return bid


def configure_bank(admin, providers=("manual", "sepay", "casso", "generic")):
    r = admin.put("/api/admin/settings/payment", {"value": {
        "enabled": True, "order_ttl_minutes": 30, "code_prefix": "HSA", "bank_bin": "970436",
        "bank_name": "Vietcombank", "account_number": "0123456789", "account_name": "CONG TY HSA",
        "providers": list(providers)}})
    assert r.status_code == 200, r.text


def sepay_call(anon, order_code, amount, txn_id, key="sepay-test-key"):
    body = {"id": txn_id, "gateway": "Vietcombank", "transactionDate": "2026-09-30 10:00:00",
            "accountNumber": "0123456789", "content": f"MBVCB.1 {order_code} thanh toan", "transferType": "in",
            "transferAmount": amount, "referenceCode": "FT1", "description": ""}
    return anon.c.post("/api/payments/webhook/sepay", json=body, headers={"Authorization": f"Apikey {key}"})


def test_paid_exam_requires_purchase_then_webhook_grants_one_attempt(student, admin, anon):
    configure_bank(admin)
    bid = paid_bp()
    r = student.post("/api/sessions", {"blueprint_id": bid})
    assert r.status_code == 402 and r.json()["detail"]["price_vnd"] == 20000
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    assert o["status"] == "pending" and o["amount_vnd"] == 20000 and o["code"].startswith("HSA")
    assert o["qr_payload"].startswith("000201") and "<svg" in o["qr_svg"] and o["bank"]["account_number"]
    # the same pending order is reused
    assert student.post("/api/orders", {"blueprint_id": bid}).json()["code"] == o["code"]
    # wrong API key rejected
    assert sepay_call(anon, o["code"], 20000, 9001, key="wrong").status_code == 401
    r = sepay_call(anon, o["code"], 20000, 9001)
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "matched"
    # duplicate delivery of the same bank transaction is idempotent
    assert sepay_call(anon, o["code"], 20000, 9001).json()["results"][0]["status"] == "matched"
    db = SessionLocal()
    order = db.scalar(select(PaymentOrder).where(PaymentOrder.code == o["code"]))
    assert order.status == "paid" and order.provider == "sepay"
    assert db.scalar(select(func.count()).where(Entitlement.order_id == order.id)) == 1
    assert db.scalar(select(func.count()).where(PaymentTransaction.provider_txn_id == "9001")) == 1
    db.close()
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "paid"
    # one attempt available → one exam, then payment required again
    cat = {b["id"]: b for b in student.get("/api/catalog").json()["blueprints"]}
    r = student.post("/api/sessions", {"blueprint_id": bid})
    assert r.status_code == 200
    sid = r.json()["session"]["id"]
    student.post(f"/api/sessions/{sid}/submit")
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 402


def test_underpaid_and_unknown_transfers_need_reconciliation(student, admin, anon):
    configure_bank(admin)
    bid = paid_bp(30000)
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    assert sepay_call(anon, o["code"], 10000, 9101).json()["results"][0]["status"] == "underpaid"
    assert sepay_call(anon, "KHONGCOMA", 30000, 9102).json()["results"][0]["status"] == "unmatched"
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "pending"
    tx = admin.get("/api/admin/transactions?status=unmatched").json()["items"]
    t = [x for x in tx if x["provider_txn_id"] == "9102"][0]
    assert admin.post(f"/api/admin/transactions/{t['id']}/assign", {"order_code": o["code"]}).status_code == 200
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "paid"
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 200


def test_manual_confirmation_is_idempotent_and_refund_revokes(student, admin):
    configure_bank(admin, providers=("manual",))
    bid = paid_bp(15000)
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    r = admin.post(f"/api/admin/orders/{o['code']}/confirm", {"note": "Đã thấy tiền trong app ngân hàng"})
    assert r.json()["confirmed"] is True
    assert admin.post(f"/api/admin/orders/{o['code']}/confirm", {}).status_code == 409
    assert admin.post(f"/api/admin/orders/{o['code']}/refund", {"note": "hoàn"}).status_code == 200
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 402


def test_disabled_provider_and_generic_hmac(student, admin, anon):
    configure_bank(admin, providers=("manual",))
    assert anon.c.post("/api/payments/webhook/sepay", json={}).status_code == 404
    configure_bank(admin, providers=("manual", "generic"))
    bid = paid_bp(25000)
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    body = json.dumps({"txn_id": "G-1", "amount": 25000, "content": o["code"]}).encode()
    bad = anon.c.post("/api/payments/webhook/generic", content=body, headers={"X-Signature": "00",
                                                                               "Content-Type": "application/json"})
    assert bad.status_code == 401
    sig = hmac.new(b"generic-test-secret", body, hashlib.sha256).hexdigest()
    ok = anon.c.post("/api/payments/webhook/generic", content=body, headers={"X-Signature": sig,
                                                                              "Content-Type": "application/json"})
    assert ok.json()["results"][0]["status"] == "matched"


def test_casso_webhook(student, admin, anon):
    configure_bank(admin, providers=("manual", "casso"))
    bid = paid_bp(20000)
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    payload = {"error": 0, "data": [{"id": 1, "tid": "CAS-77", "description": f"{o['code']} ck", "amount": 20000,
                                     "when": "2026-09-30 11:00:00"}]}
    assert anon.c.post("/api/payments/webhook/casso", json=payload,
                       headers={"Secure-Token": "nope"}).status_code == 401
    r = anon.c.post("/api/payments/webhook/casso", json=payload, headers={"Secure-Token": "casso-test-token"})
    assert r.json()["results"][0]["status"] == "matched"


def test_expired_order_is_still_honoured_when_paid(student, admin, anon):
    configure_bank(admin)
    bid = paid_bp(20000)
    o = student.post("/api/orders", {"blueprint_id": bid}).json()
    db = SessionLocal()  # the expiry time itself is immutable: let the clock pass it instead
    assert commerce.expire_orders(db, now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=31)) >= 1
    db.close()
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "expired"
    assert sepay_call(anon, o["code"], 20000, 9301).json()["results"][0]["status"] == "matched"
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "paid"


def test_bundles_passes_and_admin_grants(student, admin):
    configure_bank(admin)
    bid = paid_bp(20000)
    p = admin.post("/api/admin/products", {"code": f"pack_{uuid.uuid4().hex[:5]}", "name": "Gói 2 lượt",
                                          "price_vnd": 35000, "attempts": 2, "duration_days": 30}).json()
    o = student.post("/api/orders", {"product_id": p["id"]}).json()
    assert o["amount_vnd"] == 35000
    admin.post(f"/api/admin/orders/{o['code']}/confirm", {})
    for _ in range(2):
        r = student.post("/api/sessions", {"blueprint_id": bid})
        assert r.status_code == 200
        student.post(f"/api/sessions/{r.json()['session']['id']}/submit")
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 402
    # admin grants an unlimited pass for this blueprint
    admin.post(f"/api/admin/users/{student.user['id']}/grant", {"blueprint_ids": [bid], "days": 7, "note": "khuyến mãi"})
    for _ in range(3):
        r = student.post("/api/sessions", {"blueprint_id": bid})
        assert r.status_code == 200
        student.post(f"/api/sessions/{r.json()['session']['id']}/submit")


def test_admin_can_make_exam_free(student, admin):
    bid = paid_bp(20000)
    bp = [b for b in admin.get("/api/admin/blueprints").json()["items"] if b["id"] == bid][0]
    body = {k: bp[k] for k in ("code", "name", "description", "kind", "config", "is_published", "sort_order")}
    r = admin.put(f"/api/admin/blueprints/{bid}", dict(body, price_vnd=0))
    assert r.status_code == 200 and r.json()["price_vnd"] == 0 and r.json()["version"] == bp["version"]
    assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 200
