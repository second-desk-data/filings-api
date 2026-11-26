#!/usr/bin/env python3
"""
v2 test battery for the New Business Filings API — the Nov 26, 2026
additions on top of tests/run_tests.py (which still covers the
original surface):

  * CORS: Access-Control-Allow-Origin on normal + error responses,
    OPTIONS preflight handling (incl. echoing requested headers).
  * GET /openapi.yaml: serves the spec; it parses, names the
    production server, and covers every route the root endpoint lists.
  * Vermarco delivery webhook (POST /webhooks/vermarco): Ed25519
    signature verification (challenge, delivery, tamper rejection),
    the __sha256 JCS attestation, the CT-email channel exclusion, and
    probe handling. The pure-Python Ed25519 is also pinned against
    the RFC 8032 §7.1 test vectors, and the JCS canonicalizer against
    known orderings.
  * MCP server checks live in tests/run_tests_mcp.py (they need the
    MCP SDK interpreter).

Boots app/server.py against a tiny fixture store (LA + TX + CT
records in the expansion adapters' schema, built with build_db.py),
with the Vermarco signing-key cache seeded from env — no network.

Run:  python3 tests/run_tests_v2.py
Exit code 0 = all checks passed.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8896
BASE = f"http://127.0.0.1:{PORT}"
CALLBACK_URL = f"{BASE}/webhooks/vermarco"

sys.path.insert(0, str(ROOT / "app"))
import ed25519  # noqa: E402
import vermarco  # noqa: E402

KEYS = {
    "keys": [
        {"key": "test-basic", "tier": "basic", "daily_limit": 100000,
         "scope": {"type": "all"}},
        {"key": "test-city", "tier": "city", "daily_limit": 5000,
         "scope": {"type": "city", "state": "LA", "city": "Ville Platte"}},
    ]
}

# The "platform" keypair for this battery: the server is seeded with
# the public half via VERMARCO_SIGNING_KEYS_JSON; requests are signed
# with the seed, exactly as Vermarco signs with its private key.
VM_SEED = bytes(range(32))
VM_PUB = ed25519.publickey(VM_SEED)
VM_KEY_ID = "vm-test-key-1"

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def request(method, path, body=None, headers=None, raw_headers=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_json(path, key=None):
    headers = {"X-API-Key": key} if key else {}
    s, h, raw = request("GET", path, headers=headers)
    return s, h, json.loads(raw.decode("utf-8"))


def vm_headers(raw_body, key_id=VM_KEY_ID, seed=VM_SEED, url=CALLBACK_URL,
               body_sha=None, signature=None):
    sha = body_sha or hashlib.sha256(raw_body).hexdigest()
    ts = "2026-11-26T19:00:00Z"
    if signature is None:
        msg = f"POST\n{url}\n{ts}\n{sha}".encode("utf-8")
        signature = base64.urlsafe_b64encode(
            ed25519.sign(seed, msg)).rstrip(b"=").decode()
    return {
        "Content-Type": "application/json",
        "X-VM-Key-Id": key_id,
        "X-VM-Signature": signature,
        "X-VM-Signature-Alg": "ed25519",
        "X-VM-Timestamp": ts,
        "X-VM-Body-Sha256": sha,
    }


def vm_post(payload, **kw):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    s, h, resp = request("POST", "/webhooks/vermarco", body=raw,
                         headers=vm_headers(raw, **kw))
    try:
        return s, json.loads(resp.decode("utf-8"))
    except ValueError:
        return s, {}


def build_fixture():
    fx = Path(tempfile.mkdtemp(prefix="v2fx_"))
    (fx / "states").mkdir()
    la_rec = {"id": "LA100", "charter_number": "LA100",
              "name": "Acme Trucking LLC", "name_raw": "ACME TRUCKING LLC",
              "entity_type": "LLC", "origin": "domestic",
              "filing_date": "2026-11-20",
              "address": "1 MAIN ST VILLE PLATTE, LA 70586",
              "city": "Ville Platte", "state": "LA", "zip": "70586",
              "parish": "Evangeline", "registered_agent": "A",
              "agents": ["A"], "source_file": "x", "source_url": "x"}
    tx_recs = [
        {"id": "TX100", "name": "Lone Star Cafe LLC",
         "name_raw": "LONE STAR CAFE LLC", "state": "TX", "city": "Austin",
         "county_or_parish": "Travis", "entity_type": "LLC",
         "filing_date": "2026-11-10", "registered_agent": "B",
         "zip": "78701", "source_url": "y"},
        {"id": "TX200", "name": "Undated Ventures LLC",
         "name_raw": "UNDATED VENTURES LLC", "state": "TX", "city": "Dallas",
         "county_or_parish": None, "entity_type": "LLC",
         "filing_date": None, "registered_agent": None,
         "zip": "75001", "source_url": "y"},
    ]
    ct_recs = [
        {"id": "CT100", "name": "Hartford Widgets LLC",
         "name_raw": "HARTFORD WIDGETS LLC", "state": "CT",
         "city": "Hartford", "entity_type": "LLC",
         "filing_date": "2026-11-15", "registered_agent": "C",
         "zip": "06103", "source_url": "z",
         "email_as_filed": "owner@example.com", "naics_code": "541511",
         "naics_title": "Custom Computer Programming Services"},
    ]
    (fx / "seed.json").write_text(json.dumps(
        {"dataset": "fixture", "generated_at": "2026-11-26",
         "record_count": 1, "sources": [], "records": [la_rec]}))
    (fx / "states" / "TX.json").write_text(json.dumps({"records": tx_recs}))
    (fx / "states" / "CT.json").write_text(json.dumps({"records": ct_recs}))
    out = fx / "filings.db"
    r = subprocess.run([sys.executable, str(ROOT / "build_db.py"),
                        "--data", str(fx / "seed.json"), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("build_db.py failed")
    return out


def boot(db_path):
    env = dict(
        os.environ,
        API_KEYS_JSON=json.dumps(KEYS),
        PORT=str(PORT), HOST="127.0.0.1",
        # This battery covers the filings-only deployment shape: point
        # the packs store at a nonexistent path so a production
        # data/packs.db in the repo is never picked up here. The
        # all-verticals delivery is covered by run_tests_v3.py.
        VERMARCO_PACKS_DB=str(db_path.parent / "no-packs.db"),
        VERMARCO_CALLBACK_URL=CALLBACK_URL,
        VERMARCO_SIGNING_KEYS_JSON=json.dumps(
            {VM_KEY_ID: base64.urlsafe_b64encode(VM_PUB).rstrip(b"=").decode()}),
        # Unknown key ids must fail closed without network dependence.
        VERMARCO_SIGNING_KEY_URL="http://127.0.0.1:9/dead",
    )
    # Log beside the fixture DB, not /tmp: the box's /tmp tmpfs is
    # small and was full during the Nov 26 deploys (server prints at
    # startup would die on ENOSPC before ever answering /health).
    log_path = db_path.parent / "v2-server.log"
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "server.py"), "--db", str(db_path)],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 120
    while True:
        try:
            s, _h, _b = get_json("/health")
            if s == 200:
                return proc, log
        except Exception:
            pass
        if time.time() > deadline:
            print(log_path.read_text()[-2000:])
            proc.terminate()
            raise SystemExit("server did not come up")
        time.sleep(0.5)


def main():
    # ---- unit: Ed25519 against the RFC 8032 vectors -------------------
    pk1 = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a"
                        "0ee172f3daa62325af021a68f707511a")
    sig1 = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                         "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    check("ed25519: RFC 8032 TEST 1 verifies", ed25519.verify(pk1, b"", sig1))
    sk2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f"
                        "5b8a319f35aba624da8cf6ed4fb8a6fb")
    pk2 = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc"
                        "9c982ccf2ec4968cc0cd55f12af4660c")
    sig2 = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
                         "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    check("ed25519: RFC 8032 TEST 2 (pubkey + sign + verify)",
          ed25519.publickey(sk2) == pk2
          and ed25519.sign(sk2, b"\x72") == sig2
          and ed25519.verify(pk2, b"\x72", sig2))
    check("ed25519: tampered message rejected",
          not ed25519.verify(pk1, b"x", sig1))

    # ---- unit: JCS canonicalization ------------------------------------
    check("jcs: keys sorted recursively",
          vermarco.canonical_json({"b": 1, "a": {"d": 4, "c": 3}})
          == '{"a":{"c":3,"d":4},"b":1}')
    check("jcs: integer-like keys first, numeric order",
          vermarco.canonical_json({"10": "x", "2": "y", "a": "z"})
          == '{"2":"y","10":"x","a":"z"}')
    check("jcs: string escaping + numbers",
          vermarco.canonical_json({"s": 'a"b\\c\nd', "n": 2.5, "i": 7})
          == '{"i":7,"n":2.5,"s":"a\\"b\\\\c\\nd"}')

    db = build_fixture()
    proc, log = boot(db)
    try:
        # ---- CORS -------------------------------------------------------
        s, h, _ = request("GET", "/health")
        check("cors: /health carries Allow-Origin *",
              s == 200 and h.get("Access-Control-Allow-Origin") == "*")
        s, h, _ = request("GET", "/v1/businesses")
        check("cors: 401 error response carries Allow-Origin *",
              s == 401 and h.get("Access-Control-Allow-Origin") == "*")
        s, h, _ = request("OPTIONS", "/v1/businesses", headers={
            "Origin": "https://catalog.example.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key, Content-Type",
        })
        check("cors: preflight 204 + origin + echoed headers",
              s == 204 and h.get("Access-Control-Allow-Origin") == "*"
              and h.get("Access-Control-Allow-Headers") == "X-API-Key, Content-Type",
              f"status={s} headers={h.get('Access-Control-Allow-Headers')}")
        s, h, _ = request("OPTIONS", "/webhooks/vermarco",
                          headers={"Origin": "https://x.example"})
        check("cors: preflight on webhook route", s == 204)

        # ---- OpenAPI ------------------------------------------------------
        s, h, raw = request("GET", "/openapi.yaml")
        check("openapi: served 200 as yaml",
              s == 200 and "yaml" in (h.get("Content-Type") or ""))
        spec = None
        try:
            import yaml
            spec = yaml.safe_load(raw.decode("utf-8"))
        except ImportError:
            check("openapi: pyyaml available to parse the spec", False,
                  "install pyyaml to run this check")
        if spec is not None:
            check("openapi: production server URL",
                  spec["servers"][0]["url"] == "https://filings-api.onrender.com")
            check("openapi: X-API-Key security scheme",
                  spec["components"]["securitySchemes"]["ApiKeyAuth"]["name"]
                  == "X-API-Key")
            s2, _h2, root = get_json("/")
            missing = []
            for label in root["endpoints"]:
                method, path = label.split(" ", 1)
                if method.lower() not in spec["paths"].get(path, {}):
                    missing.append(label)
            check("openapi: covers every route the root endpoint lists",
                  not missing, f"missing: {missing}")
            check("root: version 0.4 + new routes listed",
                  root["version"] == "0.4"
                  and "GET /openapi.yaml" in root["endpoints"]
                  and "POST /webhooks/vermarco" in root["endpoints"])

        # ---- Vermarco webhook ---------------------------------------------
        s, _h, body = get_json("/webhooks/vermarco")
        check("webhook: GET liveness view",
              s == 200 and body.get("endpoint") == "vermarco-delivery")

        raw_unsigned = json.dumps({"type": "verification_challenge",
                                   "nonce": "n-1"}).encode()
        s, _h, _r = request("POST", "/webhooks/vermarco", body=raw_unsigned,
                            headers={"Content-Type": "application/json"})
        check("webhook: unsigned POST -> 401", s == 401)

        s, body = vm_post({"type": "verification_challenge", "nonce": "n-42"})
        check("webhook: signed challenge -> same nonce back",
              s == 200 and body == {"nonce": "n-42"}, f"{s} {body}")

        raw_a = json.dumps({"type": "verification_challenge",
                            "nonce": "signed"}).encode()
        hdrs = vm_headers(raw_a)
        raw_b = json.dumps({"type": "verification_challenge",
                            "nonce": "tampered"}).encode()
        s, _h, _r = request("POST", "/webhooks/vermarco", body=raw_b,
                            headers=hdrs)
        check("webhook: body tampered after signing -> 401", s == 401)

        raw_c = json.dumps({"type": "verification_challenge",
                            "nonce": "x"}).encode()
        s, _h, _r = request("POST", "/webhooks/vermarco", body=raw_c,
                            headers=vm_headers(raw_c, body_sha="0" * 64))
        check("webhook: wrong body-sha header -> 401", s == 401)

        s, _b = vm_post({"type": "verification_challenge", "nonce": "x"},
                        key_id="vm-unknown-key")
        check("webhook: unknown key id -> 401", s == 401)

        s, body = vm_post({"type": "verification_challenge"})
        check("webhook: challenge without nonce -> 400",
              s == 400 and body.get("error") == "missing_nonce")

        s, body = vm_post({"type": "health_probe", "at": "now"})
        check("webhook: unknown signed type -> probe ack",
              s == 200 and body == {"ok": True})

        s, body = vm_post({"type": "delivery_request", "listingId": "L1",
                           "verticalSlug": "forms-filings",
                           "query": {"state": "TX"}})
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("webhook: delivery TX slice",
              s == 200 and data.get("count") == 2 and len(recs) == 2
              and {r["name"] for r in recs}
              == {"Lone Star Cafe LLC", "Undated Ventures LLC"},
              f"{s} {str(body)[:200]}")
        stripped = dict(data)
        att = stripped.pop("__sha256", None)
        expect = "sha256:" + hashlib.sha256(
            vermarco.canonical_json(stripped).encode("utf-8")).hexdigest()
        check("webhook: __sha256 attestation recomputes",
              att == expect, f"{att} != {expect}")
        check("webhook: delivery under the 10 MB cap",
              len(json.dumps(body).encode()) < 10_000_000)

        s, body = vm_post({"type": "delivery_request", "listingId": "L2",
                           "verticalSlug": "forms-filings",
                           "query": {"state": "CT"}})
        recs = (body.get("data") or {}).get("records") or []
        check("webhook: CT delivery strips email_as_filed (channel rule)",
              s == 200 and len(recs) == 1
              and "email_as_filed" not in recs[0]
              and recs[0].get("naics_code") == "541511",
              f"{s} {str(body)[:200]}")
        s3, _h3, rest_rec = get_json("/v1/businesses/CT100", key="test-basic")
        check("sanity: REST still serves email_as_filed on the API itself",
              s3 == 200 and rest_rec.get("email_as_filed") == "owner@example.com")

        s, body = vm_post({"type": "delivery_request", "listingId": "L3",
                           "verticalSlug": "demographics",
                           "query": {"state": "TX"}})
        check("webhook: non-filings vertical -> available:false",
              s == 200 and body == {"available": False})

        s, body = vm_post({"type": "delivery_request", "listingId": "L4",
                           "verticalSlug": "forms-filings",
                           "query": {"state": "ZZ"}})
        check("webhook: no matching records -> 404 available:false",
              s == 404 and body.get("available") is False)

        s, body = vm_post({"type": "delivery_request",
                           "listingId": "37cf116e-f3ed-4b5c-8471-5d5d843640eb",
                           "verticalSlug": "forms-filings", "query": {}})
        check("webhook: staged PA test-pack listing defaults apply (state=PA, none in fixture -> 404)",
              s == 404 and body.get("available") is False)

        s, _h, _r = request("POST", "/nope", body=b"{}",
                            headers={"Content-Type": "application/json"})
        check("webhook: POST to unknown route -> 404 JSON", s == 404)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} v2 checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
