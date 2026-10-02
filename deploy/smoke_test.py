#!/usr/bin/env python3
"""End-to-end smoke test against a running deployment (stdlib only).

    deploy/smoke_test.py http://127.0.0.1:8620 [--admin-email E --admin-password P]

Registers a throw-away student, runs a practice session (answer, immediate check, submit, review),
starts a free exam, autosaves, reloads, submits, checks history/bookmarks/reports, and — with admin
credentials — the admin overview and question search. Exits non-zero on the first failure.
"""
import argparse
import gzip
import http.client
import http.cookiejar
import time
import json
import sys
import urllib.error
import urllib.request
import uuid


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self):
        return next((c.value for c in self.jar if c.name == "hsa_csrf"), "")

    def call(self, method, path, body=None, expect=200):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("Accept-Encoding", "gzip")  # like browsers
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if method != "GET":
            req.add_header("X-CSRF-Token", self.csrf())
        for attempt in range(3):
            try:
                with self.op.open(req, timeout=60) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    status, text = r.status, raw.decode()
                break
            except urllib.error.HTTPError as e:
                status, text = e.code, e.read().decode()
                break
            except (http.client.IncompleteRead, ConnectionError) as e:
                idempotent = method == "GET" or path.endswith("/submit") or "/check/" in path
                if not idempotent or attempt == 2:  # only idempotent calls are retried
                    raise SystemExit(f"FAIL {method} {path}: {type(e).__name__}")
                time.sleep(0.5)
        if expect is not None and status != expect:
            raise SystemExit(f"FAIL {method} {path}: {status} (expected {expect}) {text[:300]}")
        return json.loads(text) if text and text[0] in "[{" else text


def ok(msg):
    print(f"  ✓ {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--admin-email")
    ap.add_argument("--admin-password")
    a = ap.parse_args()
    s = Client(a.base)
    print("student flow")
    assert s.call("GET", "/api/health")["db"]
    ok("health")
    email = f"smoke_{uuid.uuid4().hex[:8]}@example.com"
    s.call("POST", "/api/auth/register", {"email": email, "password": "smoke-test-pass-1", "display_name": "Smoke"})
    ok(f"registered {email}")
    cat = s.call("GET", "/api/catalog")
    avail = {x["code"]: x["available"] for x in cat["subjects"]}
    assert cat["served_total"] > 0, "no served questions"
    ok(f"catalog: {cat['served_total']} served questions, {len(cat['blueprints'])} exam formats")
    subj = max(avail, key=avail.get)
    sid = s.call("POST", "/api/sessions", {"subjects": [subj], "types": ["single_choice"], "count": 5,
                                            "feedback": "immediate"})["session"]["id"]
    sess = s.call("GET", f"/api/sessions/{sid}")
    it = sess["items"][0]
    assert "answer" not in it
    s.call("PATCH", f"/api/sessions/{sid}/answers",
           {"changes": [{"position": 1, "response": {"labels": [it["question"]["options"][0]["key"]]}}]})
    chk = s.call("POST", f"/api/sessions/{sid}/check/1")
    assert chk["answer"] and chk["outcome"] in ("correct", "incorrect")
    ok(f"practice: answered + checked ({chk['outcome']})")
    res = s.call("POST", f"/api/sessions/{sid}/submit")
    assert res["status"] == "submitted" and all("answer" in i for i in res["items"])
    ok(f"practice submitted: {res['score']}/{res['max_score']}")
    free = [b for b in cat["blueprints"] if b["price_vnd"] == 0 and b["access"]["allowed"]]
    if free:
        bp = min(free, key=lambda b: b["total_questions"])
        r = s.call("POST", "/api/sessions", {"blueprint_id": bp["id"]})
        eid = r["session"]["id"]
        ex = s.call("GET", f"/api/sessions/{eid}")
        ch = [{"position": i["position"], "response": {"labels": [i["question"]["options"][0]["key"]]}}
              for i in ex["items"][:5] if i["question"]["input"]["kind"] == "choice"]
        s.call("PATCH", f"/api/sessions/{eid}/answers", {"changes": ch})
        again = s.call("GET", f"/api/sessions/{eid}")
        assert [i["response"] for i in again["items"][:len(ch)]] == [c["response"] for c in ch]
        ok(f"exam '{bp['name']}': {ex['total']} items, autosave persisted across reload")
        done = s.call("POST", f"/api/sessions/{eid}/submit")
        ok(f"exam submitted: {done['score']}/{done['max_score']} scaled={done['result']['scaled']}")
        s.call("PUT", f"/api/bookmarks/{done['items'][0]['question_ref']}", {})
        assert s.call("GET", "/api/bookmarks")["total"] >= 1
        s.call("POST", "/api/reports", {"question_ref": done["items"][0]["question_ref"], "category": "other",
                                        "message": "smoke test report (ignore)", "session_id": eid})
        ok("bookmark + report")
    paid = [b for b in cat["blueprints"] if b["price_vnd"] > 0]
    if paid:
        r = s.call("POST", "/api/sessions", {"blueprint_id": paid[0]["id"]}, expect=402)
        ok(f"paid exam requires purchase ({paid[0]['price_vnd']} VND)")
    # practice plans: a new account is FREE and limited per subject; PRO is bought through an order
    acc = cat["access"]
    assert acc["plan"] == "FREE", acc
    lim = acc["free_limit"]
    assert all(x["available"] <= min(lim, x["total"]) for x in cat["subjects"]), cat["subjects"]
    ok(f"FREE plan: ≤ {lim} practice questions per subject (" + ", ".join(
        f"{x['code']} {x['available']}/{x['total']}" for x in cat["subjects"] if x["total"]) + ")")
    plans = {p["code"]: p for p in s.call("GET", "/api/plans")["items"]}
    pro = plans.get("PRO")
    if pro:
        r = s.call("POST", "/api/orders", {"plan_code": "PRO", "amount_vnd": 1}, expect=None)
        if r.get("code") == "payment_not_configured" or (isinstance(r.get("detail"), dict)
                                                          and r["detail"].get("code") == "payment_not_configured"):
            ok("PRO order: destination account not configured yet (orders refused)")
        else:
            assert r["amount_vnd"] == pro["price_vnd"] and r["kind"] == "pro", r
            assert r["transfer_content"].replace(" ", "") == r["code"] and r["bank"]["account_number"], r
            assert r["qr_payload"] is None or r["qr_payload"].startswith("000201")
            ok(f"PRO order {r['code']}: {r['amount_vnd']} VND (server price, client amount ignored), "
               f"'{r['transfer_content']}' → {r['bank']['bank_name']} {r['bank']['account_number']}")
            c = s.call("POST", f"/api/orders/{r['code']}/cancel")
            assert c["status"] == "cancelled"
            ok("smoke order cancelled (no payment recorded)")
    assert s.call("GET", "/api/me/plan")["plan"] == "FREE"
    for path in ("/api/admin/dashboard", "/api/admin/orders"):
        s.call("GET", path, expect=403)
    ok("student denied admin APIs")
    hist = s.call("GET", "/api/sessions?status=submitted")
    ok(f"history: {hist['total']} submitted")
    if a.admin_email:
        print("admin flow")
        ad = Client(a.base)
        ad.call("POST", "/api/auth/login", {"email": a.admin_email, "password": a.admin_password})
        ov = ad.call("GET", "/api/admin/overview")
        ok(f"overview: {ov['questions']['total']} questions, {ov['questions']['served']} served, "
           f"{ov['reports']['open']} open reports")
        q = ad.call("GET", "/api/admin/questions?served=true&size=1")["items"][0]
        d = ad.call("GET", f"/api/admin/questions/{q['id']}")
        assert d["current_version"]["content"]["stem"]
        ok(f"question detail {d['external_id']} ({d['editorial_state']}, {d['state_source']})")
        dash = ad.call("GET", "/api/admin/dashboard")
        ok(f"dashboard: {dash['users']['free']} FREE / {dash['users']['pro_active']} PRO users, orders pending "
           f"{dash['payments']['pending']} paid {dash['payments']['paid']}, free limit "
           f"{dash['config']['free_questions_per_subject']}, PRO {dash['config']['pro_price_vnd']} VND / "
           f"{dash['config']['pro_duration_days']} days")
        rec = ad.call("GET", "/api/admin/reconciliation/subjects")
        bad = [x["subject"] for x in rec["items"]
               if (x["student_api"] != x["served"] if rec["practice_allow_self_check"] else x["student_api"] > x["served"])
               or x["effective"] != x["served"] + x["excluded"]]
        assert not bad, f"subject reconciliation mismatch: {bad}"
        ok("subject reconciliation: " + ", ".join(f"{x['subject']} {x['served']}/{x['effective']}"
                                                  for x in rec["items"] if x["effective"]))
        bps = ad.call("GET", "/api/admin/blueprints")["items"]
        ok("blueprint availability: " + ", ".join(f"{b['code']}={'ok' if b['availability']['ok'] else 'SHORT'}"
                                                   for b in bps))
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
