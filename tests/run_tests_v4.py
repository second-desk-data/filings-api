#!/usr/bin/env python3
"""
Test battery for the agent commerce layer (v4): the sellable catalog
(GET /v1/catalog, GET /v1/catalog/{family}), the agent discovery
document (GET /.well-known/agent.json), and the work board
(GET/POST/DELETE /v1/jobs and /v1/resumes) — including the security
self test: hostile requests (bad keys, oversized bodies, script and
SQL-flavored payloads, wrong types, consent violations, foreign
deletes) and the guarantee that errors carry no traces, no internal
paths and no key material.

Boots app/server.py against the REAL filings store (data/filings.db)
and the REAL catalog store (data/catalog.db — built by
build_catalog.py; if missing, the battery builds it into a temp file
from the catalog site lane). Ground truth for the catalog comes from
direct sqlite3 reads of the catalog store, independent of the
server's own query paths. The board store is a temp file.

Run:  python3 tests/run_tests_v4.py
Exit code 0 = all checks passed.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8894
BASE = f"http://127.0.0.1:{PORT}"

KEYS = {
    "keys": [
        {"key": "v4-owner-a", "tier": "pro", "daily_limit": 100000,
         "scope": {"type": "all"}},
        {"key": "v4-owner-b", "tier": "pro", "daily_limit": 100000,
         "scope": {"type": "all"}},
        {"key": "v4-free", "tier": "free", "daily_limit": 50,
         "scope": {"type": "all"}},
        {"key": "v4-tiny", "tier": "free", "daily_limit": 2,
         "scope": {"type": "all"}},
    ]
}
A, B, FREE, TINY = "v4-owner-a", "v4-owner-b", "v4-free", "v4-tiny"

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def request(method, path, key=None, body=None, raw_body=None):
    data = raw_body
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data)
    if key:
        req.add_header("X-API-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(path, key=None):
    s, _h, raw = request("GET", path, key=key)
    try:
        return s, json.loads(raw)
    except ValueError:
        return s, {}


def clean_error(raw, key=None):
    """An error body must carry no traceback, no internal path, and
    never the caller's key."""
    text = raw.decode("utf-8", "replace")
    bad = ("Traceback" in text or "/home/hatch" in text
           or "server.py" in text or (key and key in text))
    return not bad


def main():
    tmp = Path(tempfile.mkdtemp(prefix="v4-tests-"))
    keys_path = tmp / "keys.json"
    keys_path.write_text(json.dumps(KEYS), encoding="utf-8")
    board_path = tmp / "board.json"

    catalog_db = ROOT / "data" / "catalog.db"
    if not catalog_db.exists():
        sys.path.insert(0, str(ROOT))
        import build_catalog
        catalog_db = tmp / "catalog.db"
        build_catalog.build(build_catalog.CATALOG_LANE, catalog_db)

    # Ground truth, straight from the catalog store.
    con = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    gt_families = con.execute("SELECT COUNT(*) FROM families").fetchone()[0]
    gt_packs = con.execute("SELECT COUNT(*) FROM packs").fetchone()[0]
    gt_records = con.execute(
        "SELECT value FROM meta WHERE key = 'records'").fetchone()[0]
    gt_filings = con.execute(
        "SELECT COUNT(*) FROM packs WHERE family_slug = 'filings'"
    ).fetchone()[0]
    gt_bundle = con.execute(
        "SELECT records, price_monthly, price_onetime FROM packs"
        " WHERE family_slug = 'filings' ORDER BY records DESC LIMIT 1"
    ).fetchone()
    con.close()

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "server.py"),
         "--port", str(PORT), "--host", "127.0.0.1",
         "--db", str(ROOT / "data" / "filings.db"),
         "--keys", str(keys_path),
         "--catalog-db", str(catalog_db),
         "--board-path", str(board_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        up = False
        for _ in range(90):
            try:
                s, body = get_json("/health")
                if s == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(1)
        check("server boots with catalog + board stores", up)
        if not up:
            return 1

        # ---- agent discovery -------------------------------------------
        s, card = get_json("/.well-known/agent.json")
        check("agent.json: public 200, no key needed", s == 200)
        check("agent.json: names the business + contact",
              card.get("name") == "Second Desk"
              and card.get("contact_email") == "seconddesks@proton.me")
        check("agent.json: api/openapi/mcp/catalog endpoints",
              card.get("api_base") == "https://filings-api.onrender.com"
              and card.get("openapi", "").endswith("/openapi.yaml")
              and card.get("mcp_endpoint", "").endswith("/mcp")
              and card.get("catalog_endpoint", "").endswith("/v1/catalog"))
        plans = {p["name"]: p for p in card.get("plans", [])}
        check("agent.json: plan prices unchanged",
              plans.get("Free", {}).get("daily_requests") == 50
              and plans.get("City Heavy", {}).get("price_usd_monthly") == 29
              and plans.get("Full State Heavy", {}).get(
                  "price_usd_monthly") == 59)

        # ---- catalog -----------------------------------------------------
        s, _h, raw = request("GET", "/v1/catalog")
        check("catalog: no key -> 401", s == 401 and clean_error(raw))
        s, cat = get_json("/v1/catalog", key=FREE)
        check("catalog: free key reads it", s == 200)
        check("catalog: totals match the store",
              cat.get("totals", {}).get("families") == gt_families
              and cat.get("totals", {}).get("packs") == gt_packs
              and cat.get("totals", {}).get("records")
              == json.loads(gt_records),
              f"api={cat.get('totals')} gt=({gt_families},{gt_packs},"
              f"{gt_records})")
        check("catalog: every family carries a packs endpoint",
              len(cat.get("families", [])) == gt_families
              and all(f.get("packs_endpoint")
                      == f"/v1/catalog/{f['slug']}"
                      for f in cat["families"]))

        s, page = get_json("/v1/catalog/filings?limit=5", key=FREE)
        check("catalog family: filings pack count matches store",
              s == 200 and page.get("count") == gt_filings)
        top = page["packs"][0]
        check("catalog family: biggest pack is the national bundle "
              "with register prices",
              top["records"] == gt_bundle[0]
              and top["price_monthly_usd"] == gt_bundle[1]
              and top["price_onetime_usd"] == gt_bundle[2])
        s, page2 = get_json("/v1/catalog/filings?limit=5&offset=5",
                            key=FREE)
        ids1 = {p["id"] for p in page["packs"]}
        ids2 = {p["id"] for p in page2["packs"]}
        check("catalog family: pagination pages are disjoint",
              s == 200 and not (ids1 & ids2) and len(ids2) == 5)
        s, _h, raw = request("GET", "/v1/catalog/not-a-family", key=FREE)
        body = json.loads(raw)
        check("catalog family: unknown slug -> 404 + valid list",
              s == 404 and body.get("error") == "unknown_family"
              and "filings" in body.get("valid_families", [])
              and clean_error(raw, FREE))
        s, _h, _raw = request("GET", "/v1/catalog/filings?limit=abc",
                              key=FREE)
        check("catalog family: bad pagination -> 400", s == 400)

        # Quota: the tiny key (2/day) is cut off on the third call.
        get_json("/v1/catalog", key=TINY)
        get_json("/v1/catalog", key=TINY)
        s, body = get_json("/v1/catalog", key=TINY)
        check("catalog: free-tier daily cap still enforced",
              s == 429 and body.get("error") == "daily_quota_exceeded")

        # ---- work board: jobs -------------------------------------------
        job = {"title": "CDL Driver, OTR", "company": "Acme Hauling LLC",
               "location": "Lafayette, LA",
               "description": "Regional OTR, home weekends.",
               "contact": "hr@acme-hauling.example"}
        s, _h, raw = request("POST", "/v1/jobs", body=job)
        check("jobs: POST without key -> 401",
              s == 401 and clean_error(raw))
        s, _h, raw = request("POST", "/v1/jobs", key="not-a-real-key",
                             body=job)
        check("jobs: POST with bad key -> 401, key not echoed",
              s == 401 and clean_error(raw, "not-a-real-key"))
        s, _h, created_raw = request("POST", "/v1/jobs", key=A, body=job)
        created = json.loads(created_raw)
        check("jobs: owner A posts -> 201 with id + created_at",
              s == 201 and bool(created.get("id"))
              and bool(created.get("created_at")))
        job_id = created["id"]
        s, listing = get_json("/v1/jobs", key=B)
        check("jobs: list shows the post to another key",
              s == 200 and listing.get("count") == 1
              and listing["jobs"][0]["id"] == job_id)
        s, _h, raw = request("GET", "/v1/jobs", key=B)
        check("jobs: owner hash never serialized",
              b"owner_hash" not in raw)
        s, listing = get_json("/v1/jobs?location=lafayette", key=B)
        check("jobs: location filter matches case-insensitively",
              s == 200 and listing.get("count") == 1)
        s, listing = get_json("/v1/jobs?location=nowhere", key=B)
        check("jobs: location filter excludes non-matches",
              s == 200 and listing.get("count") == 0)

        # ---- work board: validation --------------------------------------
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             body={**job, "title": 123})
        check("jobs: non-string field -> 400 invalid_field_type",
              s == 400 and json.loads(raw).get("error")
              == "invalid_field_type" and clean_error(raw, A))
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             body={**job, "salary": "$30/hr"})
        check("jobs: unexpected field -> 400",
              s == 400 and json.loads(raw).get("error")
              == "unexpected_field")
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             body={k: v for k, v in job.items()
                                   if k != "company"})
        check("jobs: missing field -> 400",
              s == 400 and json.loads(raw).get("error")
              == "missing_field")
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             body={**job, "title": "x" * 121})
        check("jobs: over-length field -> 400 field_too_long",
              s == 400 and json.loads(raw).get("error")
              == "field_too_long")
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             raw_body=b"{not json")
        check("jobs: malformed JSON -> 400 bad_json",
              s == 400 and json.loads(raw).get("error") == "bad_json")
        s, _h, raw = request("POST", "/v1/jobs", key=A,
                             raw_body=b" " * 70000)
        check("jobs: oversized body -> 413",
              s == 413 and json.loads(raw).get("error")
              == "body_too_large")

        # ---- hostile payloads --------------------------------------------
        evil = {"title": "<script>alert(1)</script>",
                "company": "Evil'); DROP TABLE filings;--",
                "location": "Nowhere",
                "description": "x\" OR \"1\"=\"1",
                "contact": "evil@example.com"}
        s, _h, created_raw = request("POST", "/v1/jobs", key=A, body=evil)
        evil_id = json.loads(created_raw)["id"] if s == 201 else None
        check("hostile: script/SQL payload accepted as inert data",
              s == 201 and evil_id)
        s, _h, raw = request("GET", "/v1/jobs?q=DROP", key=B)
        check("hostile: payload served back as a JSON string, "
              "application/json only",
              s == 200 and b"<script>alert(1)</script>" in raw
              and b"owner_hash" not in raw)
        s, body = get_json(
            "/v1/resumes?role=" + urllib.parse.quote("' OR '1'='1"),
            key=B)
        check("hostile: SQL-flavored search param -> 200, no rows, "
              "no error", s == 200 and body.get("count") == 0)
        s, body = get_json(
            "/v1/businesses?state=FL&q=" + urllib.parse.quote(
                "' OR '1'='1"), key=A)
        check("hostile: injection-flavored filings search -> 200, "
              "count 0 (parameterized, not interpreted)",
              s == 200 and body.get("count") == 0)
        s, body = get_json("/health")
        check("hostile: filings store untouched afterwards",
              s == 200 and body.get("records") == 5724302)

        # ---- work board: resumes + consent --------------------------------
        resume = {"name": "Sam Dispatcher", "role": "Logistics Dispatcher",
                  "location": "Baton Rouge, LA",
                  "experience": "Five years dispatching flatbed fleets.",
                  "contact": "sam@example.com", "consent": True}
        for label, mutate in (
                ("missing", lambda r: {k: v for k, v in r.items()
                                       if k != "consent"}),
                ("false", lambda r: {**r, "consent": False}),
                ("string", lambda r: {**r, "consent": "true"})):
            s, _h, raw = request("POST", "/v1/resumes", key=A,
                                 body=mutate(resume))
            check(f"resumes: consent {label} -> 400 consent_required",
                  s == 400 and json.loads(raw).get("error")
                  == "consent_required")
        s, _h, created_raw = request("POST", "/v1/resumes", key=A, body=resume)
        created = json.loads(created_raw)
        check("resumes: consent true -> 201, consent not stored/echoed",
              s == 201 and "consent" not in created
              and bool(created.get("id")))
        resume_id = created["id"]
        s, listing = get_json("/v1/resumes?role=dispatcher", key=B)
        check("resumes: role keyword search finds it",
              s == 200 and listing.get("count") == 1
              and listing["resumes"][0]["contact"] == "sam@example.com")
        check("resumes: list carries the self-listing note",
              "candidates themselves" in listing.get("note", ""))
        s, listing = get_json("/v1/resumes?role=dispatcher&location=houston",
                              key=B)
        check("resumes: role+location filters combine",
              s == 200 and listing.get("count") == 0)

        # ---- deletes: ownership / IDOR ------------------------------------
        s, _h, raw = request("DELETE", f"/v1/jobs/{job_id}", key=B)
        check("delete: foreign key -> 404 (indistinguishable)",
              s == 404 and clean_error(raw, B))
        s, listing = get_json("/v1/jobs", key=B)
        check("delete: foreign attempt removed nothing",
              listing.get("count") == 2)
        s, _h, _raw = request("DELETE", f"/v1/jobs/{job_id}", key=A)
        check("delete: owner removes own job -> 200", s == 200)
        s, _h, _raw = request("DELETE", f"/v1/jobs/{job_id}", key=A)
        check("delete: second delete -> 404", s == 404)
        s, _h, _raw = request("DELETE", f"/v1/resumes/{resume_id}", key=B)
        check("delete: foreign key cannot remove a resume", s == 404)
        s, _h, _raw = request("DELETE", f"/v1/resumes/{resume_id}", key=A)
        check("delete: owner removes own resume -> 200", s == 200)
        if evil_id:
            s, _h, _raw = request("DELETE", f"/v1/jobs/{evil_id}", key=A)
            check("cleanup: hostile entry deleted by owner", s == 200)
        s, listing = get_json("/v1/jobs", key=A)
        check("board: empty again after cleanup",
              listing.get("count") == 0)

        failed = [n for n, ok in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        return 1 if failed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
