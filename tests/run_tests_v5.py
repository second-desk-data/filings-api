#!/usr/bin/env python3
"""Step 5 checks: self-serve free key issuance (POST /v1/keys), the
public changelog, and the anti-farming hostile battery.

Boots the real server against the real filings + catalog stores with
a temporary issued-keys store and verifies:

  * POST /v1/keys returns a working free key immediately (201, shown
    once, sd_free_ prefix, tier/limit/scope in the body)
  * the raw key is NOWHERE in the on-disk store; its SHA-256 hash is
  * one active key per email: second request -> 409 with a next step
  * every documented error body: invalid_email / missing_field /
    unexpected_field / bad_json (400), body_too_large (413)
  * FARMING: same email behind rotating IPs -> 429 after 5 attempts/h
  * FARMING: many emails from one IP -> 429 after 10 attempts/h
  * an issued key is a full free-tier citizen (catalog, filings, board
    post + delete) and still capped at 50 requests/day (51st -> 429)
  * GET /changelog is public and carries dated v1 / v2 / agent
    commerce entries, newest first

Run: python3 tests/run_tests_v5.py
"""

import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8893
BASE = f"http://127.0.0.1:{PORT}"

PASSED = 0
FAILED = []


def check(name, cond, extra=""):
    global PASSED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED.append(name)
        print(f"FAIL  {name}  {extra}")


def request(method, path, key=None, body=None, headers=None, raw_body=None):
    data = raw_body
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if key:
        hdrs["X-API-Key"] = key
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), payload
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(path, key=None, headers=None):
    s, _h, raw = request("GET", path, key=key, headers=headers)
    return s, json.loads(raw)


def post_keys(body=None, headers=None, raw_body=None):
    s, h, raw = request("POST", "/v1/keys", body=body, headers=headers,
                        raw_body=raw_body)
    try:
        return s, h, json.loads(raw)
    except ValueError:
        return s, h, {}


def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="filings-v5-"))
    issued_path = tmp / "issued_keys.json"
    board_path = tmp / "board.json"
    keys_path = tmp / "keys.json"
    keys_path.write_text(json.dumps({"keys": [
        {"key": "v5-suspended-key", "tier": "city", "daily_limit": 5000,
         "scope": {"type": "all"}, "status": "suspended"},
        {"key": "v5-revoked-key", "tier": "state", "daily_limit": 10000,
         "scope": {"type": "all"}, "status": "revoked"},
    ]}), encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "server.py"),
         "--port", str(PORT), "--host", "127.0.0.1",
         "--db", str(ROOT / "data" / "filings.db"),
         "--keys", str(keys_path),
         "--catalog-db", str(ROOT / "data" / "catalog.db"),
         "--board-path", str(board_path),
         "--issued-keys", str(issued_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 240
        while True:
            try:
                s, _ = get_json("/health")
                if s == 200:
                    break
            except Exception:
                pass
            if time.time() > deadline:
                raise RuntimeError("server did not come up")
            time.sleep(2)

        # ---- changelog -------------------------------------------------
        s, log = get_json("/changelog")
        entries = log.get("changelog", [])
        versions = [e.get("version") for e in entries]
        check("changelog: public 200, no key", s == 200)
        check("changelog: v1, v2 and agent commerce entries, newest first",
              versions[:1] == ["3.0.0"] and "2.0.0" in versions
              and "1.0.0" in versions
              and all(e.get("date") for e in entries))
        check("changelog: agent commerce entry names self-serve keys",
              any("keys" in " ".join(e.get("changes", [])).lower()
                  for e in entries if e["version"] == "3.0.0"))

        # ---- happy path: issue + use -----------------------------------
        s, _h, issued = post_keys({"email": "Agent-A@Example.com"})
        raw_key = issued.get("api_key", "")
        check("keys: 201 with an sd_free_ key shown once",
              s == 201 and raw_key.startswith("sd_free_")
              and issued.get("shown_once") is True)
        check("keys: free tier terms in the response",
              issued.get("tier") == "free"
              and issued.get("daily_limit") == 50
              and issued.get("scope") == {"type": "all"})
        s, cat = get_json("/v1/catalog", key=raw_key)
        check("keys: issued key reads the catalog",
              s == 200 and cat.get("totals", {}).get("families") == 18)
        s, biz = get_json("/v1/businesses?state=LA&limit=1", key=raw_key)
        check("keys: issued key reads filings (scope all)",
              s == 200 and len(biz.get("results", [])) == 1
              and biz.get("count", 0) > 1)

        # ---- stored hashed, never raw ----------------------------------
        on_disk = issued_path.read_bytes()
        digest = hashlib.sha256(raw_key.encode()).hexdigest()
        check("keys: raw key absent from the store file",
              raw_key.encode() not in on_disk)
        check("keys: sha256 hash present in the store file",
              digest.encode() in on_disk)

        # ---- one active key per email ----------------------------------
        s, _h, dup = post_keys({"email": "agent-a@example.com"})
        check("keys: second issue for same email -> 409 with next step",
              s == 409 and dup.get("error") == "key_already_issued"
              and "next_step" in dup)

        # ---- documented error bodies -----------------------------------
        s, _h, e = post_keys({"email": "not-an-email"})
        check("keys: malformed email -> 400 invalid_email",
              s == 400 and e.get("error") == "invalid_email"
              and "next_step" in e)
        s, _h, e = post_keys({"email": 42})
        check("keys: non-string email -> 400 invalid_email",
              s == 400 and e.get("error") == "invalid_email")
        s, _h, e = post_keys({})
        check("keys: missing email -> 400 missing_field",
              s == 400 and e.get("error") == "missing_field")
        s, _h, e = post_keys({"email": "x@example.com", "admin": True})
        check("keys: extra field -> 400 unexpected_field",
              s == 400 and e.get("error") == "unexpected_field")
        s, _h, raw = request("POST", "/v1/keys",
                             raw_body=b'{"email": ')
        e = json.loads(raw)
        check("keys: malformed JSON -> 400 bad_json",
              s == 400 and e.get("error") == "bad_json")
        s, _h, raw = request("POST", "/v1/keys",
                             raw_body=b'{"email": "' + b"a" * 70000
                             + b'@x.com"}')
        e = json.loads(raw)
        check("keys: oversized body -> 413 body_too_large",
              s == 413 and e.get("error") == "body_too_large")

        # ---- FARMING: one email, rotating IPs ---------------------------
        statuses = []
        last = {}
        for i in range(6):
            s, h, e = post_keys(
                {"email": "farmer@example.com"},
                headers={"X-Forwarded-For": f"10.9.0.{i + 1}"})
            statuses.append(s)
            last = (h, e)
        check("farming: first issue from a fresh email -> 201",
              statuses[0] == 201)
        check("farming: repeats -> 409 while attempts remain",
              all(x == 409 for x in statuses[1:5]))
        h, e = last
        check("farming: 6th attempt in the hour -> 429 rate_limited",
              statuses[5] == 429 and e.get("error") == "rate_limited"
              and e.get("retry_after_seconds", 0) > 0
              and "Retry-After" in h)

        # ---- FARMING: many emails, one IP -------------------------------
        farm_keys = []
        statuses = []
        for i in range(11):
            s, _h, e = post_keys(
                {"email": f"farm{i}@example.com"},
                headers={"X-Forwarded-For": "203.0.113.9"})
            statuses.append(s)
            if s == 201:
                farm_keys.append(e["api_key"])
        check("farming: 10 issues from one IP succeed, 11th -> 429",
              statuses[:10] == [201] * 10 and statuses[10] == 429)

        # ---- issued keys still capped at 50/day -------------------------
        burn = farm_keys[0]
        ok = all(get_json("/v1/catalog", key=burn)[0] == 200
                 for _ in range(50))
        s, e = get_json("/v1/catalog", key=burn)
        check("keys: issued key capped at 50/day (51st -> 429)",
              ok and s == 429
              and e.get("error") == "daily_quota_exceeded")

        # ---- issued key on the board ------------------------------------
        s, _h, raw = request(
            "POST", "/v1/jobs", key=raw_key,
            body={"title": "V5 Test Role", "company": "Second Desk Test",
                  "location": "Remote", "description": "Round trip.",
                  "contact": "agent-a@example.com"})
        job = json.loads(raw)
        s2, jobs = get_json("/v1/jobs", key=raw_key)
        s3, _h, raw3 = request("DELETE", f"/v1/jobs/{job.get('id')}",
                               key=raw_key)
        check("keys: issued key can post + delete its own board entry",
              s == 201 and s2 == 200 and s3 == 200
              and json.loads(raw3).get("deleted") == job.get("id"))

        # ---- discovery documents name the self-serve path ---------------
        s, card = get_json("/.well-known/agent.json")
        check("agent.json: self-serve key endpoint documented",
              card.get("auth", {}).get("free_key_endpoint")
              == "POST /v1/keys")
        plans = {p["name"]: p for p in card.get("plans", [])}
        check("agent.json: Employer plan listed at $20/mo",
              plans.get("Employer", {}).get("price_usd_monthly") == 20)
        check("agent.json: first-year price lock policy served",
              "lock" in (card.get("pricing_policy") or "").lower()
              and "price_lock" in plans.get("City Heavy", {})
              and plans.get("Full State Heavy",
                            {}).get("price_usd_monthly") == 59)
        s, root = get_json("/")
        check("root: lists POST /v1/keys and GET /changelog",
              "POST /v1/keys" in root.get("endpoints", {})
              and "GET /changelog" in root.get("endpoints", {}))

        # ---- paid plan lifecycle gate ------------------------------------
        s, e = get_json("/v1/catalog", key="v5-suspended-key")
        check("lifecycle: suspended key -> 402 plan_past_due + next step",
              s == 402 and e.get("error") == "plan_past_due"
              and "next_step" in e)
        s, e = get_json("/v1/catalog", key="v5-revoked-key")
        check("lifecycle: revoked key -> 401 api_key_revoked",
              s == 401 and e.get("error") == "api_key_revoked")

    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        if FAILED:
            print("\n".join(out.splitlines()[-25:]))

    print(f"\n{PASSED}/{PASSED + len(FAILED)} v5 checks passed")
    if FAILED:
        print("FAILED:", *FAILED, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())