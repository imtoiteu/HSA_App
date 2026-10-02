"""Practice plans (FREE limit / PRO), plan configuration, PRO orders, mock-exam payments, admin security."""
import datetime as dt
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.commerce import access as plans
from app.commerce import service as commerce
from app.db import SessionLocal
from app.models import (ExamBlueprint, PaymentOrder, PaymentOrderEvent, PlanSubscription, Question, QuestionBank,
                        User)

from .test_payments import configure_bank, sepay_call

SUBJ = "geography"  # the fixture upstream bank serves only a few geography questions


@pytest.fixture(scope="module")
def big_bank(synced):
    """An extra bank with 125 single questions + 2 passages of 4 in one subject (> the default limit)."""
    from app.sync.importer import recompute_policy
    from app.sync.jsonl_import import import_jsonl
    tmp = Path(os.environ["HSA_MEDIA_ROOT"]).parent / f"bank_{uuid.uuid4().hex[:6]}.jsonl"
    opts = [{"label": x, "md": f"Phương án {x}"} for x in "ABCD"]
    with open(tmp, "w", encoding="utf-8") as f:
        for i in range(125):
            f.write(json.dumps({"external_id": f"geo-{i}", "type": "single_choice", "subject": SUBJ,
                                "status": "READY_TO_SERVE", "stem_md": f"Câu địa lí số {i}: chọn đáp án đúng.",
                                "options": opts, "answer": {"kind": "labels", "labels": ["A"]}}, ensure_ascii=False)
                    + "\n")
        for g in range(2):
            for k in range(4):
                f.write(json.dumps({"external_id": f"geo-g{g}-{k}", "type": "single_choice", "subject": SUBJ,
                                    "status": "READY_TO_SERVE", "stem_md": f"Theo đoạn {g}, câu {k}?", "options": opts,
                                    "answer": {"kind": "labels", "labels": ["B"]},
                                    "group": {"key": f"geo-grp-{g}", "header_md": "Đọc đoạn sau",
                                              "passage_md": f"Đoạn văn {g} về khí hậu Việt Nam."}},
                                   ensure_ascii=False) + "\n")
    db = SessionLocal()
    code = f"geo_{uuid.uuid4().hex[:5]}"
    import_jsonl(db, code, str(tmp), Path(os.environ["HSA_MEDIA_ROOT"]))
    db.commit()
    plans.clear_cache()
    yield code
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == code))
    bank.is_active = False  # other test modules see the bank as before
    db.flush()
    recompute_policy(db)
    db.commit()
    db.close()
    plans.clear_cache()


def subject_row(api, code=SUBJ):
    return [s for s in api.get("/api/catalog").json()["subjects"] if s["code"] == code][0]


def practise(api, **body):
    r = api.post("/api/sessions", dict({"count": 100, "feedback": "end"}, **body))
    return r


def item_ids(api, sid):
    return {it["question_ref"] for it in api.get(f"/api/sessions/{sid}").json()["items"]}


def set_practice(admin, **v):
    r = admin.put("/api/admin/settings/practice", {"value": v})
    assert r.status_code == 200, r.text
    return r


def user_id(api):
    return api.user["id"]


@pytest.fixture()
def limit_100(admin):
    set_practice(admin, free_questions_per_subject=100)
    yield
    set_practice(admin, free_questions_per_subject=100)


# ------------------------------------------------------------------------------------------------ FREE
def test_free_plan_gets_100_questions_per_subject_by_default(student, big_bank, limit_100):
    db = SessionLocal()
    assert plans.free_limit(db) == 100
    pool = plans.free_pool(db, SUBJ)
    plans.clear_cache()
    assert plans.free_pool(db, SUBJ) == pool  # deterministic, not per request
    served = db.scalar(select(func.count()).where(Question.subject_code == SUBJ, Question.is_served.is_(True)))
    db.close()
    row = subject_row(student)
    assert row["total"] == served >= 133 and row["available"] == 100 == len(pool)
    assert student.get("/api/catalog").json()["access"]["plan"] == "FREE"
    # practising the whole allowance only ever returns pool questions
    s1 = practise(student, subjects=[SUBJ])
    assert s1.status_code == 200, s1.text
    got = item_ids(student, s1.json()["session"]["id"])
    assert len(got) <= 100 and got <= set(pool)


def test_free_pool_keeps_passages_whole(big_bank, limit_100):
    db = SessionLocal()
    pool = set(plans.free_pool(db, SUBJ))
    groups = db.execute(select(Question.group_key, Question.id).join(QuestionBank).where(
        QuestionBank.code == big_bank, Question.group_key.is_not(None))).all()
    db.close()
    by: dict = {}
    for g, qid in groups:
        by.setdefault(g, set()).add(qid)
    for members in by.values():
        assert members <= pool or not (members & pool)  # all or nothing


def test_free_limit_cannot_be_bypassed_through_the_api(student, big_bank, admin):
    set_practice(admin, free_questions_per_subject=3)
    try:
        db = SessionLocal()
        allowed = set()
        for code in plans.practice_subjects(db):
            allowed |= set(plans.free_pool(db, code))
        pool = set(plans.free_pool(db, SUBJ))
        db.close()
        assert len(pool) == 3 and subject_row(student)["available"] == 3
        # every variant of the request stays inside the free pools
        for body in ({"subjects": [SUBJ], "count": 200}, {"subjects": [], "count": 200},
                     {"subjects": [SUBJ, SUBJ], "types": ["single_choice"]}, {"source": "all", "count": 1}):
            r = practise(student, **body)
            assert r.status_code == 200, (body, r.text)
            assert item_ids(student, r.json()["session"]["id"]) <= allowed
        # the account cannot raise its own limit
        assert student.put("/api/admin/settings/practice", {"value": {"free_questions_per_subject": 99999}}) \
            .status_code == 403
        # once every free question was seen, "unseen" explains the plan instead of a generic error
        r = practise(student, subjects=[SUBJ], source="unseen")
        assert r.status_code == 402 and r.json()["detail"]["code"] == "free_limit"
        assert "Pro" in r.json()["detail"]["message"]
    finally:
        set_practice(admin, free_questions_per_subject=100)


def test_subject_with_fewer_questions_than_the_limit(student, limit_100):
    for row in student.get("/api/catalog").json()["subjects"]:
        if 0 < row["total"] < 100:
            assert row["available"] == row["total"]
            r = practise(student, subjects=[row["code"]], count=100)
            assert r.status_code == 200
            assert len(item_ids(student, r.json()["session"]["id"])) <= row["total"]
            break
    else:
        pytest.skip("no small subject in the fixture")


# ------------------------------------------------------------------------------------------------ PRO
def test_pro_gets_the_whole_pool_and_expiry_returns_to_free(student, admin, big_bank, limit_100):
    free_session = practise(student, subjects=[SUBJ]).json()["session"]["id"]
    r = admin.post(f"/api/admin/users/{user_id(student)}/pro", {"days": 30, "note": "tặng thử nghiệm"})
    assert r.status_code == 200, r.text
    row = subject_row(student)
    assert row["available"] == row["total"] > 100
    me = student.get("/api/me/plan").json()
    assert me["plan"] == "PRO" and me["days_left"] >= 29
    s = practise(student, subjects=[SUBJ], count=100).json()["session"]["id"]
    db = SessionLocal()
    pool = set(plans.free_pool(db, SUBJ))
    # with 133 served and 100 drawn at random, at least some must come from outside the free pool
    assert item_ids(student, s) - pool
    # expiry (time passes): back to FREE, nothing of the history is lost
    sub = db.scalar(select(PlanSubscription).where(PlanSubscription.user_id == user_id(student)))
    sub.starts_at = commerce.utcnow() - dt.timedelta(days=40)
    sub.expires_at = commerce.utcnow() - dt.timedelta(days=10)
    db.commit()
    db.close()
    assert subject_row(student)["available"] == 100
    me = student.get("/api/me/plan").json()
    assert me["plan"] == "FREE" and me["expired_at"] and me["subscriptions"][0]["state"] == "expired"
    ids = {x["id"] for x in student.get("/api/sessions?size=50").json()["items"]}
    assert {free_session, s} <= ids
    assert student.get(f"/api/sessions/{s}").status_code == 200


def test_renewal_stacks_after_the_current_period(student, admin):
    for _ in range(2):
        assert admin.post(f"/api/admin/users/{user_id(student)}/pro", {"days": 10, "note": "gia hạn"}).status_code == 200
    subs = sorted(student.get("/api/me/plan").json()["subscriptions"], key=lambda x: x["starts_at"])
    assert subs[1]["starts_at"] == subs[0]["expires_at"]
    assert subs[1]["state"] == "scheduled" and student.get("/api/me/plan").json()["days_left"] >= 19


# ------------------------------------------------------------------------------------------------ config
def test_admin_validates_and_audits_the_free_limit(admin, student, limit_100):
    for bad in (0, -5, "100", 10 ** 9, True):
        assert admin.put("/api/admin/settings/practice", {"value": {"free_questions_per_subject": bad}}).status_code == 422
    assert admin.put("/api/admin/settings/practice", {"value": {"unknown_key": 1}}).status_code == 422
    set_practice(admin, free_questions_per_subject=50)
    assert student.get("/api/catalog").json()["access"]["free_limit"] == 50
    v = admin.get("/api/admin/settings/practice").json()
    assert v["value"]["max_questions"] == 100  # partial update keeps the other keys
    log = admin.get("/api/admin/audit?action=setting_updated").json()["items"][0]
    assert log["data"]["changes"]["free_questions_per_subject"]["after"] == 50


def test_pro_price_change_applies_to_new_orders_only(student, admin, anon):
    configure_bank(admin)
    pro = [p for p in admin.get("/api/admin/plans").json()["items"] if p["code"] == "PRO"][0]
    body = {k: pro[k] for k in ("name", "description", "price_vnd", "duration_days", "is_active", "benefits")}
    assert pro["price_vnd"] == 300000 and pro["duration_days"]
    # the client cannot choose the amount
    o1 = student.post("/api/orders", {"plan_code": "PRO", "amount_vnd": 1000, "price_vnd": 1}).json()
    assert o1["amount_vnd"] == 300000 and o1["kind"] == "pro"
    try:
        r = admin.put("/api/admin/plans/PRO", dict(body, price_vnd=250000, duration_days=90))
        assert r.status_code == 200 and r.json()["price_vnd"] == 250000
        assert admin.put("/api/admin/plans/PRO", dict(body, price_vnd=0)).status_code == 422
        o2 = student.post("/api/orders", {"plan_code": "PRO"}).json()
        assert o2["amount_vnd"] == 250000 and o2["duration_days"] == 90 and o2["code"] != o1["code"]
        old = student.get(f"/api/orders/{o1['code']}").json()
        assert old["amount_vnd"] == 300000 and old["duration_days"] == pro["duration_days"]
        # the old order is paid at its own price and grants its own duration
        assert sepay_call(anon, o1["code"], 300000, 7101).json()["results"][0]["status"] == "matched"
        me = student.get("/api/me/plan").json()
        assert me["plan"] == "PRO" and me["subscriptions"][0]["name"].endswith(f"{pro['duration_days']} ngày")
    finally:
        admin.put("/api/admin/plans/PRO", body)


# ------------------------------------------------------------------------------------------------ payment
def test_pro_order_reference_confirmation_and_idempotency(student, admin, anon):
    configure_bank(admin)
    o = student.post("/api/orders", {"plan_code": "PRO"}).json()
    assert o["status"] == "pending" and o["transfer_content"] == f"HSA {o['code'][3:]}"
    assert o["qr_payload"].startswith("000201") and o["bank"]["account_number"] == "0123456789"
    other = student.post("/api/orders", {"blueprint_id": None, "plan_code": "PRO"}).json()
    assert other["code"] == o["code"]  # an identical pending order is reused, not duplicated
    # students cannot confirm, grant or reconfigure
    uid = user_id(student)
    for method, url, body in (("post", f"/api/admin/orders/{o['code']}/confirm", {}),
                              ("post", f"/api/admin/users/{uid}/pro", {"days": 365, "note": "tự cấp"}),
                              ("put", "/api/admin/plans/PRO", {"name": "x", "price_vnd": 1}),
                              ("put", "/api/admin/settings/payment", {"value": {"account_number": "999999"}}),
                              ("get", "/api/admin/dashboard", None), ("get", "/api/admin/orders", None),
                              ("get", "/api/admin/reconciliation/subjects", None)):
        r = getattr(student, method)(url, body) if body is not None else getattr(student, method)(url)
        assert r.status_code == 403, (url, r.status_code)
        r = getattr(anon, method)(url, body) if body is not None else getattr(anon, method)(url)
        assert r.status_code in (401, 403), (url, r.status_code)
    assert student.get("/api/me/plan").json()["plan"] == "FREE"
    # manual confirmation by an admin, twice: one subscription
    r1 = admin.post(f"/api/admin/orders/{o['code']}/confirm", {"note": "MB app: FT123"}).json()
    r2 = admin.post(f"/api/admin/orders/{o['code']}/confirm", {"note": "again"})
    assert r1["confirmed"] is True and r1["order"]["status"] == "paid"
    assert r2.status_code == 409 or r2.json()["confirmed"] is False
    # a duplicate bank notification for the same order does not grant again
    assert sepay_call(anon, o["code"], 300000, 7201).json()["results"][0]["status"] == "late"
    db = SessionLocal()
    oid = db.scalar(select(PaymentOrder.id).where(PaymentOrder.code == o["code"]))
    assert db.scalar(select(func.count()).where(PlanSubscription.order_id == oid)) == 1
    evs = db.execute(select(PaymentOrderEvent.from_status, PaymentOrderEvent.to_status, PaymentOrderEvent.source)
                     .where(PaymentOrderEvent.order_id == oid).order_by(PaymentOrderEvent.id)).all()
    db.close()
    assert evs[0] == (None, "pending", "user") and evs[-1] == ("pending", "paid", "admin")
    me = student.get("/api/me/plan").json()
    assert me["plan"] == "PRO"
    d = admin.get(f"/api/admin/orders/{o['code']}").json()
    assert d["subscription"] and d["order"]["confirmed_by_email"] and d["order"]["bank_snapshot"]["account_number"]
    assert [e["to"] for e in d["events"]] == ["pending", "paid"]
    # refund revokes the PRO period it granted
    assert admin.post(f"/api/admin/orders/{o['code']}/refund", {"note": "khách huỷ"}).status_code == 200
    assert student.get("/api/me/plan").json()["plan"] == "FREE"


def test_destination_is_frozen_per_order(student, admin):
    configure_bank(admin)
    o = student.post("/api/orders", {"plan_code": "PRO"}).json()
    try:
        assert admin.put("/api/admin/settings/payment", {"value": {"account_number": "5550001112"}}).status_code == 200
        assert student.get(f"/api/orders/{o['code']}").json()["bank"]["account_number"] == "0123456789"
        student.post(f"/api/orders/{o['code']}/cancel")
        new = student.post("/api/orders", {"plan_code": "PRO"}).json()
        assert new["bank"]["account_number"] == "5550001112"
        # the database itself refuses to change what a customer was asked to pay
        db = SessionLocal()
        row = db.scalar(select(PaymentOrder).where(PaymentOrder.code == new["code"]))
        row.amount_vnd = 1
        with pytest.raises(Exception):
            db.commit()
        db.rollback()
        db.close()
    finally:
        configure_bank(admin)


def test_expired_and_failed_orders(student, admin):
    configure_bank(admin)
    o = student.post("/api/orders", {"plan_code": "PRO"}).json()
    db = SessionLocal()
    commerce.expire_orders(db, now=commerce.utcnow() + dt.timedelta(days=1))
    db.close()
    assert student.get(f"/api/orders/{o['code']}").json()["status"] == "expired"
    # confirming a non-pending order needs a reason
    assert admin.post(f"/api/admin/orders/{o['code']}/confirm", {}).status_code == 422
    o2 = student.post("/api/orders", {"plan_code": "PRO"}).json()
    assert admin.post(f"/api/admin/orders/{o2['code']}/fail", {"note": "giao dịch bị hoàn"}).status_code == 200
    assert student.get(f"/api/orders/{o2['code']}").json()["status"] == "failed"
    assert admin.post(f"/api/admin/orders/{o2['code']}/fail", {"note": "lần hai"}).status_code == 409
    lst = admin.get("/api/admin/orders?status=failed&kind=pro").json()
    assert any(x["code"] == o2["code"] for x in lst["items"])
    ref = admin.get(f"/api/admin/orders?reference={o2['transfer_content'].lower()}").json()
    assert [x["code"] for x in ref["items"]] == [o2["code"]]
    # an expired order paid late is honoured after an admin checks it
    r = admin.post(f"/api/admin/orders/{o['code']}/confirm", {"note": "chuyển khoản muộn, đã kiểm tra MB"})
    assert r.json()["confirmed"] is True and student.get("/api/me/plan").json()["plan"] == "PRO"


def _paid_exam(**kw):
    db = SessionLocal()
    cfg = {"sections": [{"key": "t", "title": "T", "pools": [{"subjects": ["math"], "types": ["single_choice"],
                                                               "count": 2}]}], "timing": "none"}
    bp = ExamBlueprint(code=f"pe_{uuid.uuid4().hex[:6]}", name="Đề có phí", config=cfg, is_published=True,
                       access="paid", **kw)
    db.add(bp)
    db.commit()
    bid = bp.id
    db.close()
    return bid


def test_mock_exam_default_and_promo_price_unlock(student, admin, anon):
    configure_bank(admin)
    bid = _paid_exam(price_vnd=0, promo_price_vnd=None, attempts_per_purchase=2)
    cat = {b["id"]: b for b in student.get("/api/catalog").json()["blueprints"]}
    assert cat[bid]["paid"] and cat[bid]["price_vnd"] == 20000  # default mock-exam price
    assert admin.put("/api/admin/settings/mock_exams", {"value": {"default_price_vnd": 25000}}).status_code == 200
    try:
        o = student.post("/api/orders", {"blueprint_id": bid}).json()
        assert o["amount_vnd"] == 25000 and o["kind"] == "exam"
        bid2 = _paid_exam(price_vnd=30000, promo_price_vnd=19000)
        o2 = student.post("/api/orders", {"blueprint_id": bid2}).json()
        assert o2["amount_vnd"] == 19000 and o2["list_price_vnd"] == 30000
        # PRO does not unlock paid exams unless configured
        admin.post(f"/api/admin/users/{user_id(student)}/pro", {"days": 5, "note": "pro"})
        assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 402
        assert sepay_call(anon, o["code"], 25000, 7301).json()["results"][0]["status"] == "matched"
        acc = {b["id"]: b for b in student.get("/api/catalog").json()["blueprints"]}[bid]["access"]
        assert acc["allowed"] and acc["remaining_attempts"] == 2
        assert student.post("/api/sessions", {"blueprint_id": bid}).status_code == 200
        # optional policy: PRO includes paid exams
        assert admin.put("/api/admin/settings/mock_exams", {"value": {"pro_includes_paid_exams": True}}).status_code == 200
        assert student.post("/api/sessions", {"blueprint_id": bid2}).status_code == 200
        # paid exams switched off: no new orders
        admin.put("/api/admin/settings/mock_exams", {"value": {"paid_enabled": False, "pro_includes_paid_exams": False}})
        assert student.post("/api/orders", {"blueprint_id": bid2}).status_code == 503
    finally:
        admin.put("/api/admin/settings/mock_exams", {"value": {"default_price_vnd": 20000, "paid_enabled": True,
                                                              "pro_includes_paid_exams": False}})


def test_checkout_refused_without_configured_destination(student, admin):
    configure_bank(admin)
    try:
        admin.put("/api/admin/settings/payment", {"value": {"account_number": "", "bank_bin": ""}})
        r = student.post("/api/orders", {"plan_code": "PRO"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "payment_not_configured"
        assert admin.put("/api/admin/settings/payment", {"value": {"bank_bin": "97042"}}).status_code == 422
    finally:
        configure_bank(admin)


# ------------------------------------------------------------------------------------------------ admin
def test_admin_dashboard_reconciliation_and_queues(admin, student):
    d = admin.get("/api/admin/dashboard").json()
    assert d["bank"]["total"] >= d["bank"]["served"] and "pro_active" in d["users"] and "revenue_pro_vnd" in d["payments"]
    rec = admin.get("/api/admin/reconciliation/subjects").json()
    db = SessionLocal()
    active = db.scalar(select(func.count()).select_from(Question).join(QuestionBank).where(QuestionBank.is_active.is_(True)))
    db.close()
    assert rec["totals"]["imported"] == rec["totals"]["effective"] == active
    for r in rec["items"]:
        assert r["effective"] == r["served"] + r["excluded"]
        # practice serves every served question unless self-check items are excluded by the policy
        assert r["student_api"] == r["served"] if rec["practice_allow_self_check"] else r["student_api"] <= r["served"]
        assert r["free_accessible"] == min(r["served"], rec["free_limit"]) or r["free_accessible"] <= rec["free_limit"]
        # drill-down to question ids matches the counts
        if r["subject"] != "_none":
            q = admin.get(f"/api/admin/questions?upstream_subject={r['subject']}&active_bank=true&size=1").json()
            assert q["total"] == r["upstream_total"]
        q = admin.get(f"/api/admin/questions?subject={r['subject']}&served=true&active_bank=true&size=1").json()
        assert q["total"] == r["served"]
    queues = {x["key"]: x for x in admin.get("/api/admin/qa-queues").json()["items"]}
    assert {"subject_review", "formula", "visual", "answer", "review", "render", "passage_missing",
            "unsupported"} <= set(queues)
    users = admin.get("/api/admin/users?plan=free").json()
    assert all(u["plan"] in ("FREE", "EXPIRED") for u in users["items"])
    detail = admin.get(f"/api/admin/users/{user_id(student)}").json()
    assert detail["plan"]["plan"] == "FREE" and "usage" in detail


def test_admin_revokes_pro_with_reason(student, admin):
    sub = admin.post(f"/api/admin/users/{user_id(student)}/pro", {"days": 30, "note": "hỗ trợ"}).json()
    assert admin.post(f"/api/admin/subscriptions/{sub['id']}/revoke", {"note": ""}).status_code == 422
    r = admin.post(f"/api/admin/subscriptions/{sub['id']}/revoke", {"note": "cấp nhầm tài khoản"})
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    assert admin.post(f"/api/admin/subscriptions/{sub['id']}/revoke", {"note": "lần hai"}).status_code == 409
    me = student.get("/api/me/plan").json()
    assert me["plan"] == "FREE" and me["subscriptions"][0]["revoke_reason"] == "cấp nhầm tài khoản"
    db = SessionLocal()
    assert db.get(User, user_id(student)) is not None
    db.close()
