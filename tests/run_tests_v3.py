#!/usr/bin/env python3
"""
v3 test battery — the all-verticals Vermarco delivery extension
(Nov 26, 2026) on top of tests/run_tests.py + run_tests_v2.py:

  * Every live sellmydata vertical resolves at delivery time:
    demographics (census), lending-credit (UCC), labor-market (WARN),
    patents-trademarks (trademark) — plus the filings registry scopes
    (a full-state listing pins its state under the buyer query).
  * The fixture packs store is built by the REAL build_packs_db.py
    from tiny CSVs in the families' own schemas, and deliveries are
    requested under the REAL production listing ids from
    app/vermarco_listings.json, so registry + routing + store are all
    exercised end to end.
  * Scope pinning (a CO UCC listing cannot serve CT rows), the
    state name<->code tolerant matching, the vertical fallback for
    unknown listing ids, the unserved-vertical refusal, and the
    __sha256 attestation on pack deliveries.

Boots app/server.py against fixture stores with the Vermarco
signing-key cache seeded from env — no network.

Run:  python3 tests/run_tests_v3.py
Exit code 0 = all checks passed.
"""

import base64
import csv
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
PORT = 8907  # run_tests.py owns 8897-8899, run_tests_v2.py owns 8896
BASE = f"http://127.0.0.1:{PORT}"
CALLBACK_URL = f"{BASE}/webhooks/vermarco"

sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
import ed25519  # noqa: E402
import vermarco  # noqa: E402
import build_packs_db  # noqa: E402

KEYS = {
    "keys": [
        {"key": "test-basic", "tier": "basic", "daily_limit": 100000,
         "scope": {"type": "all"}},
    ]
}

VM_SEED = bytes(range(32))
VM_PUB = ed25519.publickey(VM_SEED)
VM_KEY_ID = "vm-test-key-1"

# Real production listing ids (app/vermarco_listings.json).
CENSUS_AL = "9e3b444a-8e38-490d-9b37-739cc09bade9"
CENSUS_TEST = "39e756c5-b6d8-45e6-9ec6-28bfe5df95e9"
UCC_CO = "a51b3385-5f06-414e-aada-f67db035b433"
UCC_NAT = "2670ddc4-e36e-4c9b-be04-b1854edb8580"
WARN_NAT = "331a8dc2-335f-4a62-a8c1-5ad83f4289f9"
WARN_CA = "66e9c950-7119-495f-879f-8e58c531a978"
TM_NAT = "b4c3c62b-ba91-44cc-9a1a-78ff1ce3fe6f"
FILINGS_PA_PAID = "ef001b89-1cb8-4a3d-aa83-8e968649f4af"
FILINGS_CT = "a4519005-f21c-4b15-a80e-4d48440f1cd7"

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def request(method, path, body=None, headers=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def vm_post(payload):
    raw = json.dumps(payload).encode()
    sha = hashlib.sha256(raw).hexdigest()
    ts = "2026-11-26T21:00:00Z"
    msg = f"POST\n{CALLBACK_URL}\n{ts}\n{sha}".encode("utf-8")
    sig = base64.urlsafe_b64encode(
        ed25519.sign(VM_SEED, msg)).rstrip(b"=").decode()
    s, _h, resp = request("POST", "/webhooks/vermarco", body=raw, headers={
        "Content-Type": "application/json",
        "X-VM-Key-Id": VM_KEY_ID,
        "X-VM-Signature": sig,
        "X-VM-Signature-Alg": "ed25519",
        "X-VM-Timestamp": ts,
        "X-VM-Body-Sha256": sha,
    })
    try:
        return s, json.loads(resp.decode("utf-8"))
    except ValueError:
        return s, {}


def deliver(listing_id, vertical, query=None):
    return vm_post({"type": "delivery_request", "listingId": listing_id,
                    "verticalSlug": vertical, "query": query or {}})


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def build_fixtures(fx):
    # --- filings store: one LA seed record + one CT record ----------
    (fx / "states").mkdir()
    la_rec = {"id": "LA100", "charter_number": "LA100",
              "name": "Acme Trucking LLC", "name_raw": "ACME TRUCKING LLC",
              "entity_type": "LLC", "origin": "domestic",
              "filing_date": "2026-11-20",
              "address": "1 MAIN ST VILLE PLATTE, LA 70586",
              "city": "Ville Platte", "state": "LA", "zip": "70586",
              "parish": "Evangeline", "registered_agent": "A",
              "agents": ["A"], "source_file": "x", "source_url": "x"}
    ct_rec = {"id": "CT100", "name": "Hartford Widgets LLC",
              "name_raw": "HARTFORD WIDGETS LLC", "state": "CT",
              "city": "Hartford", "entity_type": "LLC",
              "filing_date": "2026-11-15", "registered_agent": "C",
              "zip": "06103", "source_url": "z",
              "email_as_filed": "owner@example.com",
              "naics_code": "541511"}
    (fx / "seed.json").write_text(json.dumps(
        {"dataset": "fixture", "generated_at": "2026-11-26",
         "record_count": 1, "sources": [], "records": [la_rec]}))
    (fx / "states" / "CT.json").write_text(json.dumps({"records": [ct_rec]}))
    filings_db = fx / "filings.db"
    r = subprocess.run([sys.executable, str(ROOT / "build_db.py"),
                        "--data", str(fx / "seed.json"),
                        "--out", str(filings_db)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit("build_db.py failed")

    # --- packs store via the real builder ----------------------------
    census_cols = ["geo_id", "geography_name", "geography_level",
                   "state_fips", "state_name", "dimension",
                   "dimension_label", "source_table", "category",
                   "residents", "share_of_dimension_pct",
                   "suppressed_small_cell"]
    al_rows = [
        {"geo_id": "01", "geography_name": "Alabama",
         "geography_level": "state", "state_fips": "01",
         "state_name": "Alabama", "dimension": "age_sex",
         "dimension_label": "Age & Sex", "source_table": "B01001",
         "category": "Male (total)", "residents": "2469932",
         "share_of_dimension_pct": "48.56", "suppressed_small_cell": ""},
        {"geo_id": "01073", "geography_name": "Jefferson County",
         "geography_level": "county", "state_fips": "01",
         "state_name": "Alabama", "dimension": "age_sex",
         "dimension_label": "Age & Sex", "source_table": "B01001",
         "category": "Male (total)", "residents": "320000",
         "share_of_dimension_pct": "47.9", "suppressed_small_cell": ""},
        {"geo_id": "0107000", "geography_name": "Birmingham city",
         "geography_level": "place", "state_fips": "01",
         "state_name": "Alabama", "dimension": "age_sex",
         "dimension_label": "Age & Sex", "source_table": "B01001",
         "category": "Male (total)", "residents": "95000",
         "share_of_dimension_pct": "46.8", "suppressed_small_cell": ""},
    ]
    bundle_rows = [
        dict(al_rows[0]),
        {"geo_id": "48", "geography_name": "Texas",
         "geography_level": "state", "state_fips": "48",
         "state_name": "Texas", "dimension": "age_sex",
         "dimension_label": "Age & Sex", "source_table": "B01001",
         "category": "Male (total)", "residents": "14500000",
         "share_of_dimension_pct": "49.4", "suppressed_small_cell": ""},
    ]
    ucc_cols = ["state", "filing_number", "filing_date", "debtor_name",
                "debtor_city", "debtor_state", "debtor_zip",
                "secured_party_name"]
    ucc_co = [
        {"state": "CO", "filing_number": "C1", "filing_date": "2026-01-05",
         "debtor_name": "ACME TRUCKING INC", "debtor_city": "ENGLEWOOD",
         "debtor_state": "CO", "debtor_zip": "80110",
         "secured_party_name": "GMAC"},
        {"state": "CO", "filing_number": "C2", "filing_date": "2026-02-01",
         "debtor_name": "ROCKY MOUNTAIN PARTS LLC",
         "debtor_city": "DENVER", "debtor_state": "CO",
         "debtor_zip": "80237", "secured_party_name": "BANK OF WEST"},
        {"state": "CO", "filing_number": "C3", "filing_date": "2025-12-01",
         "debtor_name": "FRONT RANGE FOODS INC", "debtor_city": "BOULDER",
         "debtor_state": "CO", "debtor_zip": "80301",
         "secured_party_name": "FIRSTBANK"},
    ]
    ucc_ct = [
        {"state": "CT", "filing_number": "T1", "filing_date": "2026-03-01",
         "debtor_name": "HARTFORD GEAR LLC", "debtor_city": "HARTFORD",
         "debtor_state": "CT", "debtor_zip": "06103",
         "secured_party_name": "WEBSTER BANK"},
    ]
    tm_cols = ["serial_number", "filing_date", "mark_identification",
               "owner_name", "owner_city", "owner_state", "goods_services"]
    tm_rows = [
        {"serial_number": "900001", "filing_date": "2026-08-31",
         "mark_identification": "NTRY", "owner_name": "Marius Botha",
         "owner_city": "SIMSBURY", "owner_state": "CT",
         "goods_services": "Plastic bags"},
        {"serial_number": "900002", "filing_date": "2026-08-15",
         "mark_identification": "BAYOU TECH", "owner_name": "Bayou LLC",
         "owner_city": "LAFAYETTE", "owner_state": "LA",
         "goods_services": "Software"},
    ]
    warn_cols = ["employer", "site_city", "site_state", "county",
                 "workers_affected", "notice_date", "effective_date",
                 "notice_type", "source_state"]
    warn_nat = [
        {"employer": "Assembly Components Group", "site_city": "Ocala",
         "site_state": "FL", "county": "", "workers_affected": "80.0",
         "notice_date": "2026-11-20", "effective_date": "2027-01-25",
         "notice_type": "unspecified", "source_state": "FL"},
        {"employer": "Children's Place Distribution",
         "site_city": "Fort Payne", "site_state": "AL", "county": "",
         "workers_affected": "593.0", "notice_date": "2026-11-06",
         "effective_date": "2027-01-05", "notice_type": "closure",
         "source_state": "AL"},
        {"employer": "Golden State Foods", "site_city": "Irvine",
         "site_state": "CA", "county": "", "workers_affected": "120.0",
         "notice_date": "2026-10-01", "effective_date": "2026-12-01",
         "notice_type": "layoff", "source_state": "CA"},
    ]
    warn_ca = [warn_nat[2]]

    write_csv(fx / "al.csv", census_cols, al_rows)
    write_csv(fx / "bundle.csv", census_cols, bundle_rows)
    write_csv(fx / "ucc_co.csv", ucc_cols, ucc_co)
    write_csv(fx / "ucc_ct.csv", ucc_cols, ucc_ct)
    write_csv(fx / "tm.csv", tm_cols, tm_rows)
    write_csv(fx / "warn_nat.csv", warn_cols, warn_nat)
    write_csv(fx / "warn_ca.csv", warn_cols, warn_ca)
    packs_db = fx / "packs.db"
    stats = build_packs_db.build([
        ("census", "alabama-full-state-demographics", fx / "al.csv"),
        ("census", "us-census-demographics-national-bundle",
         fx / "bundle.csv"),
        ("ucc", "CO", fx / "ucc_co.csv"),
        ("ucc", "CT", fx / "ucc_ct.csv"),
        ("trademark", "", fx / "tm.csv"),
        ("warn", "us-warn-act-full-national-complete", fx / "warn_nat.csv"),
        ("warn", "ca-full-state-complete", fx / "warn_ca.csv"),
    ], packs_db)
    assert stats["total_records"] == 15, stats
    return filings_db, packs_db


def boot(filings_db, packs_db):
    env = dict(
        os.environ,
        API_KEYS_JSON=json.dumps(KEYS),
        PORT=str(PORT), HOST="127.0.0.1",
        VERMARCO_CALLBACK_URL=CALLBACK_URL,
        VERMARCO_PACKS_DB=str(packs_db),
        VERMARCO_SIGNING_KEYS_JSON=json.dumps(
            {VM_KEY_ID: base64.urlsafe_b64encode(VM_PUB).rstrip(b"=").decode()}),
        VERMARCO_SIGNING_KEY_URL="http://127.0.0.1:9/dead",
    )
    log_path = filings_db.parent / "v3-server.log"
    log = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "server.py"),
         "--db", str(filings_db)],
        env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 120
    while True:
        try:
            req = urllib.request.Request(BASE + "/health")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return proc, log
        except Exception:
            pass
        if time.time() > deadline:
            print(log_path.read_text()[-2000:])
            proc.terminate()
            raise SystemExit("server did not come up")
        time.sleep(0.5)


def attestation_ok(data):
    stripped = dict(data)
    att = stripped.pop("__sha256", None)
    expect = "sha256:" + hashlib.sha256(
        vermarco.canonical_json(stripped).encode("utf-8")).hexdigest()
    return att == expect


def main():
    check("unit: state variants name->code + name",
          set(vermarco.state_key_variants("Colorado"))
          == {"colorado", "co"})
    check("unit: state variants code->name + code",
          set(vermarco.state_key_variants("TX")) == {"tx", "texas"})
    check("unit: registry carries all 79 live listings",
          len(vermarco.LISTING_REGISTRY) == 79,
          f"got {len(vermarco.LISTING_REGISTRY)}")

    # /tmp is a small tmpfs on this box (and was full during the v2
    # deploys) — fixtures live on the workspace disk instead.
    fixbase = Path.home() / "workspace" / ".v3-test-tmp"
    fixbase.mkdir(exist_ok=True)
    fx = Path(tempfile.mkdtemp(prefix="v3fx_", dir=str(fixbase)))
    filings_db, packs_db = build_fixtures(fx)
    proc, log = boot(filings_db, packs_db)
    try:
        s, _h, raw = request("GET", "/webhooks/vermarco")
        view = json.loads(raw.decode("utf-8"))
        check("liveness: all five verticals served + packs present",
              s == 200
              and set(view.get("served_verticals") or [])
              == {"forms-filings", "demographics", "lending-credit",
                  "labor-market", "patents-trademarks"}
              and view.get("packs_store") == "present"
              and view.get("listings_registered") == 79,
              f"{s} {view}")

        s, body = deliver(CENSUS_AL, "demographics")
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("census: Alabama pack listing resolves to its pack",
              s == 200 and data.get("count") == 3 and len(recs) == 3
              and all(r.get("state_name") == "Alabama" for r in recs)
              and data.get("pack") == "alabama-full-state-demographics",
              f"{s} {str(body)[:200]}")
        check("census: pack delivery attestation recomputes",
              attestation_ok(data))
        check("census: pack delivery under the 10 MB cap",
              len(json.dumps(body).encode()) < 10_000_000)

        s, body = deliver(CENSUS_AL, "demographics",
                          {"city": "Birmingham"})
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("census: city filter matches the geography label",
              s == 200 and data.get("count") == 1
              and recs[0].get("geography_name") == "Birmingham city",
              f"{s} {str(body)[:200]}")

        s, body = deliver(CENSUS_TEST, "demographics")
        data = body.get("data") or {}
        check("census: $0 test pack serves the national bundle sample",
              s == 200 and data.get("count") == 2
              and data.get("returned") == 2,
              f"{s} {str(body)[:200]}")

        s, body = deliver(UCC_CO, "lending-credit")
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("ucc: CO listing resolves (3 CO rows)",
              s == 200 and data.get("count") == 3
              and all(r.get("state") == "CO" for r in recs),
              f"{s} {str(body)[:200]}")
        s, body = deliver(UCC_CO, "lending-credit", {"state": "CT"})
        check("ucc: CO listing cannot serve CT rows (scope pinned)",
              s == 404 and body.get("available") is False,
              f"{s} {str(body)[:200]}")
        s, body = deliver(UCC_NAT, "lending-credit",
                          {"state": "Colorado"})
        data = body.get("data") or {}
        check("ucc: national listing + state name matches code rows",
              s == 200 and data.get("count") == 3,
              f"{s} {str(body)[:200]}")
        s, body = deliver(UCC_NAT, "lending-credit", {"year": "2025"})
        data = body.get("data") or {}
        check("ucc: year filter applies (1 of 4 rows)",
              s == 200 and data.get("count") == 1
              and data["records"][0].get("filing_number") == "C3",
              f"{s} {str(body)[:200]}")

        s, body = deliver(WARN_NAT, "labor-market")
        data = body.get("data") or {}
        check("warn: national listing resolves (3 notices)",
              s == 200 and data.get("count") == 3
              and data.get("pack") == "us-warn-act-full-national-complete",
              f"{s} {str(body)[:200]}")
        s, body = deliver(WARN_CA, "labor-market")
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("warn: CA pack listing resolves to the CA pack only",
              s == 200 and data.get("count") == 1
              and recs[0].get("employer") == "Golden State Foods",
              f"{s} {str(body)[:200]}")

        s, body = deliver(TM_NAT, "patents-trademarks", {"state": "CT"})
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("trademark: bundle listing + owner-state filter",
              s == 200 and data.get("count") == 1
              and recs[0].get("mark_identification") == "NTRY",
              f"{s} {str(body)[:200]}")

        s, body = deliver("no-such-listing", "demographics")
        data = body.get("data") or {}
        check("fallback: unknown listing + served vertical -> family slice",
              s == 200 and data.get("count") == 5,
              f"{s} {str(body)[:200]}")
        s, body = deliver("no-such-listing", "real-estate")
        check("fallback: unserved vertical -> available:false",
              s == 200 and body == {"available": False},
              f"{s} {str(body)[:200]}")

        s, body = deliver(FILINGS_PA_PAID, "forms-filings")
        check("filings registry: paid PA listing pins state=PA (no PA in fixture -> 404)",
              s == 404 and body.get("available") is False,
              f"{s} {str(body)[:200]}")
        s, body = deliver(FILINGS_CT, "forms-filings")
        data = body.get("data") or {}
        recs = data.get("records") or []
        check("filings registry: CT listing serves CT record, email stripped",
              s == 200 and len(recs) == 1
              and recs[0].get("name") == "Hartford Widgets LLC"
              and "email_as_filed" not in recs[0],
              f"{s} {str(body)[:200]}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} v3 checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
