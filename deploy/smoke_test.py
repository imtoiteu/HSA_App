#!/usr/bin/env python3
"""End-to-end smoke test against a running deployment (stdlib only).

    deploy/smoke_test.py http://127.0.0.1:8620 [--admin-email E --admin-password P]

Registers a throw-away student, runs a practice session (answer, immediate check, submit, review),
starts a free exam, autosaves, reloads, submits, checks history/bookmarks/reports, and — with admin
credentials — the admin overview and question search. Exits non-zero on the first failure.
"""
import argparse
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
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if method != "GET":
            req.add_header("X-CSRF-Token", self.csrf())
        for attempt in range(3):
            try:
                with self.op.open(req, timeout=60) as r:
                    status, text = r.status, r.read().decode()
                break
            except urllib.error.HTTPError as e:
                status, text = e.code, e.read().decode()
                break
            except (http.client.IncompleteRead, ConnectionError) as e:
                idempotent = method == "GET" or path.endswith("/submit") or "/check/" in path
                if not idempotent or attempt == 2:  # only idempotent calls are retried
                    raise SystemExit(f"FAIL {method} {path}: {type(e).__name__}")
                time.sleep(0.5)
        if status != expect:
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
        bps = ad.call("GET", "/api/admin/blueprints")["items"]
        ok("blueprint availability: " + ", ".join(f"{b['code']}={'ok' if b['availability']['ok'] else 'SHORT'}"
                                                   for b in bps))
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
