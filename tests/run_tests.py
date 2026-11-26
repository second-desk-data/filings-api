#!/usr/bin/env python3
"""
End-to-end test battery for the New Business Filings API (SQLite store).

Phase 1 boots app/server.py against an LA-only SQLite store built from
the real 126,710-record dataset (seed copied to a temp dir and converted
with build_db.py, so per-state files landing in data/states/ can't
change the ground truth mid-run) and checks live responses against
ground truth computed directly from data/seed.json. Industry/need ground
truth uses the canonical classifier (packs/classify.py via the
app/classify.py adapter) — the same code the packs build uses.

Phase 2 boots a second instance over a tiny fixture dataset whose state
file uses the expansion adapters' schema (id instead of charter_number,
county_or_parish, a null filing_date, no origin/agents/address) and
verifies the multi-state merge + record normalization through the
build_db.py conversion.

Phase 3 boots a third instance against the REAL multi-state store
(data/filings.db, all built states) and checks per-state counts, a
city-scoped key on a non-LA city, per-state category counts, and
per-state age_days anchoring. Ground truth comes from the build's
stats sidecar (data/filings.db.stats.json) plus direct sqlite3
aggregates — independent of the server's own query paths. If the store
hasn't been built yet, phase 3 is skipped with a note (run
`python3 build_db.py` first).

Run:  python3 tests/run_tests.py
Exit code 0 = all checks passed.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"
PORT2 = 8898
BASE2 = f"http://127.0.0.1:{PORT2}"
PORT3 = 8899
BASE3 = f"http://127.0.0.1:{PORT3}"

sys.path.insert(0, str(ROOT / "app"))
import classify as CLF  # noqa: E402 — the shared classifier under test

KEYS = {
    "keys": [
        {"key": "test-free", "tier": "free", "daily_limit": 50, "scope": {"type": "all"}},
        {"key": "test-basic", "tier": "basic", "daily_limit": 100000, "scope": {"type": "all"}},
        {"key": "test-city", "tier": "city", "daily_limit": 5000,
         "scope": {"type": "city", "state": "LA", "city": "Ville Platte"}},
        {"key": "test-state", "tier": "state", "daily_limit": 10000,
         "scope": {"type": "state", "state": "LA"}},
        {"key": "test-city-pa", "tier": "city", "daily_limit": 5000,
         "scope": {"type": "city", "state": "PA", "city": "Harrisburg"}},
        {"key": "test-state-ny", "tier": "state", "daily_limit": 10000,
         "scope": {"type": "state", "state": "NY"}},
    ]
}

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))


def get(path, key=None, base=BASE):
    req = urllib.request.Request(base + path)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def build_store(data_dir):
    """Convert a data dir (seed.json [+ states/]) to filings.db in place."""
    out = Path(data_dir) / "filings.db"
    r = subprocess.run([sys.executable, str(ROOT / "build_db.py"),
                        "--data", str(Path(data_dir) / "seed.json"),
                        "--out", str(out)],
                       capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("build_db.py failed")
    return out


def boot(port, db_path, log_path):
    # The box's /tmp tmpfs is small and was full during the Nov 26
    # deploys; a server whose startup prints cannot be written dies
    # silently before answering /health. Route logs through the
    # TMPDIR-honoring temp dir (identical to /tmp when TMPDIR unset).
    log_path = str(Path(tempfile.gettempdir()) / Path(log_path).name)
    env = dict(os.environ, API_KEYS_JSON=json.dumps(KEYS),
               PORT=str(port), HOST="127.0.0.1")
    log = open(log_path, "w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "app" / "server.py"),
                             "--db", str(db_path)],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 300
    while True:
        try:
            status, health = get("/health", base=base)
            if status == 200:
                return proc, log, health
        except Exception:
            pass
        if time.time() > deadline:
            print(f"server on {port} did not come up; log tail:")
            print(open(log_path).read()[-2000:])
            proc.terminate()
            raise SystemExit(1)
        time.sleep(1)


def stop(proc, log):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def main():
    # ---- ground truth from the dataset -------------------------------------
    payload = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))
    records = payload["records"]
    for r in records:
        r["category"] = CLF.classify_industry(r.get("name") or "")
        r["needs"] = CLF.needs_for(r)
    total = len(records)
    max_date = max(r["filing_date"] for r in records)
    min_date = min(r["filing_date"] for r in records)
    evangeline = sum(1 for r in records if r.get("parish") == "Evangeline")
    ville_platte = sum(1 for r in records if (r.get("city") or "") == "Ville Platte")
    ind_counts = Counter(r["category"] for r in records)
    need_counts = Counter()
    for r in records:
        need_counts.update(r["needs"])
    parishes_covered = len({r["parish"] for r in records if r.get("parish")})

    # composed-query target: a (city, category) pair with a mid-size count
    pair_counts = Counter(((r.get("city") or ""), r["category"]) for r in records)
    pair = next(((c, k), n) for (c, k), n in sorted(pair_counts.items())
                if 30 <= n <= 250 and c)
    (pair_city, pair_cat), pair_n = pair

    def expected_age(n):
        # Mirror packs/CATEGORIES.md exactly: age = (max_date - filing).days,
        # in the window when age < n.
        ref = datetime.strptime(max_date, "%Y-%m-%d").date()
        return sum(1 for r in records
                   if (ref - datetime.strptime(r["filing_date"], "%Y-%m-%d").date()).days < n)

    outside = next(r for r in records if (r.get("city") or "") != "Ville Platte")
    inside = next(r for r in records if (r.get("city") or "") == "Ville Platte")

    # ---- boot the server (LA-only SQLite store built from the real dataset) --
    la_dir = Path(tempfile.mkdtemp(prefix="la_only_"))
    shutil.copy(ROOT / "data" / "seed.json", la_dir / "seed.json")
    la_db = build_store(la_dir)
    proc, log, health = boot(PORT, la_db, "/tmp/filings_api_test_server.log")
    try:
        # ---- original battery -------------------------------------------------
        check("health 200 + record count", health["records"] == total,
              f"{health.get('records')} != {total}")
        check("health states + date range",
              health["states"] == ["LA"] and
              health["filing_date_range"] == [min_date, max_date])
        s, _ = get("/v1/businesses")
        check("missing key -> 401", s == 401)
        s, _ = get("/v1/businesses", key="nope")
        check("bad key -> 401", s == 401)
        s, body = get("/v1/businesses?parish=Evangeline&limit=1", key="test-basic")
        check("parish=Evangeline count", body["count"] == evangeline,
              f"{body['count']} != {evangeline}")
        s, body = get("/v1/businesses?state=LA&limit=1", key="test-basic")
        check("state=LA count = total", body["count"] == total)
        s, body = get("/v1/businesses?state=TX&limit=1", key="test-basic")
        check("state=TX count = 0 (no TX dataset loaded)", body["count"] == 0)
        s, body = get("/v1/businesses?from=not-a-date", key="test-basic")
        check("malformed date -> 400", s == 400)
        s, body = get(f"/v1/businesses/{outside['charter_number']}", key="test-basic")
        check("record lookup 200 + annotations",
              s == 200 and body["filing_state"] == "LA" and
              body["category"] in CLF.INDUSTRY_SLUGS and isinstance(body["needs"], list))
        s, _ = get("/v1/businesses/ZZZZZZZZZ", key="test-basic")
        check("unknown charter -> 404", s == 404)

        # ---- category / need / age filters -------------------------------------
        s, body = get(f"/v1/businesses?city={urllib.parse.quote(pair_city)}"
                      f"&category={pair_cat}&limit=100", key="test-basic")
        fetched = list(body["results"])
        off = 100
        while len(fetched) < body["count"]:
            s2, page = get(f"/v1/businesses?city={urllib.parse.quote(pair_city)}"
                           f"&category={pair_cat}&limit=100&offset={off}", key="test-basic")
            fetched.extend(page["results"])
            off += 100
        check(f"composed city+category count ({pair_city} / {pair_cat})",
              body["count"] == pair_n and len(fetched) == pair_n,
              f"api={body['count']} fetched={len(fetched)} expected={pair_n}")
        check("composed results all match city+category",
              all((r.get("city") or "") == pair_city and r["category"] == pair_cat
                  for r in fetched))
        s, body = get("/v1/businesses?need=likely-hiring-cdl-drivers&limit=1", key="test-basic")
        check("need=likely-hiring-cdl-drivers count",
              body["count"] == need_counts["likely-hiring-cdl-drivers"],
              f"{body['count']} != {need_counts['likely-hiring-cdl-drivers']}")
        for n in (7, 30, 90):
            s, body = get(f"/v1/businesses?age_days={n}&limit=1", key="test-basic")
            check(f"age_days={n} count", body["count"] == expected_age(n),
                  f"{body['count']} != {expected_age(n)}")
        s, body = get("/v1/businesses?category=bogus", key="test-basic")
        check("bad category -> 400", s == 400 and body["error"] == "bad_category")
        s, body = get("/v1/businesses?need=bogus", key="test-basic")
        check("bad need -> 400", s == 400 and body["error"] == "bad_need")
        s, body = get("/v1/businesses?age_days=-3", key="test-basic")
        check("bad age_days -> 400", s == 400)
        expected_2025 = sum(1 for r in records if r["filing_date"].startswith("2025"))
        s, body = get("/v1/businesses?year=2025&limit=1", key="test-basic")
        check("year=2025 count", body["count"] == expected_2025,
              f"{body['count']} != {expected_2025}")
        s, body = get("/v1/businesses?year=25", key="test-basic")
        check("bad year -> 400", s == 400)

        # ---- categories endpoint ------------------------------------------------
        s, body = get("/v1/categories", key="test-basic")
        api_ind = {i["slug"]: i["records"] for i in body["industries"]}
        api_need = {n["slug"]: n["records"] for n in body["needs"]}
        check("/v1/categories total", body["total_records"] == total)
        check("/v1/categories industry counts match dataset",
              all(api_ind.get(slug, 0) == ind_counts.get(slug, 0)
                  for slug in CLF.INDUSTRY_SLUGS),
              str({s2: (api_ind.get(s2), ind_counts.get(s2)) for s2 in CLF.INDUSTRY_SLUGS
                   if api_ind.get(s2, 0) != ind_counts.get(s2, 0)}))
        check("/v1/categories need counts match dataset",
              all(api_need.get(slug, 0) == need_counts.get(slug, 0)
                  for slug in CLF.NEED_SLUGS))
        s, body = get("/v1/categories?state=TX", key="test-basic")
        check("/v1/categories?state=TX total 0", body["total_records"] == 0)

        # ---- regions / parishes ---------------------------------------------------
        s, body = get("/v1/regions", key="test-basic")
        la = body["states"]["LA"]
        ev = next(x["filings"] for x in la["regions"] if x["region"] == "Evangeline")
        check("/v1/regions LA parish view",
              la["region_type"] == "parish" and ev == evangeline and
              body["records_total"] == total)
        s, body = get("/v1/parishes", key="test-basic")
        ev2 = next(x["filings"] for x in body["parishes"] if x["parish"] == "Evangeline")
        check("/v1/parishes backward compatible",
              body["parishes_covered"] == parishes_covered and ev2 == evangeline
              and body["records_total"] == total)

        # ---- scope enforcement ------------------------------------------------------
        s, body = get("/v1/businesses?city=Baton%20Rouge&limit=5", key="test-city")
        check("city key: other city -> 0 + scope_note",
              body["count"] == 0 and "scope_note" in body)
        s, body = get("/v1/businesses?limit=100", key="test-city")
        check("city key: unfiltered search stays in scope",
              body["count"] == ville_platte and
              all((r.get("city") or "") == "Ville Platte" for r in body["results"]),
              f"count={body['count']} expected={ville_platte}")
        s, body = get("/v1/businesses?state=LA&limit=1", key="test-city")
        check("city key: own state still city-scoped", body["count"] == ville_platte)
        s, _ = get(f"/v1/businesses/{outside['charter_number']}", key="test-city")
        check("city key: out-of-scope record -> 404 (no leak)", s == 404)
        s, body = get(f"/v1/businesses/{inside['charter_number']}", key="test-city")
        check("city key: in-scope record -> 200", s == 200)
        s, body = get("/v1/categories", key="test-city")
        check("city key: /v1/categories counts are in-scope only",
              body["total_records"] == ville_platte)
        s, body = get("/v1/businesses?state=TX&limit=1", key="test-state")
        check("state key: other state -> 0 + scope_note",
              body["count"] == 0 and "scope_note" in body)
        s, body = get("/v1/businesses?state=LA&limit=1", key="test-state")
        check("state key: own state full count", body["count"] == total)

        # ---- phase 2: multi-state merge, adapters' schema -------------------------
        fx = Path(tempfile.mkdtemp(prefix="fx_"))
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
        (fx / "seed.json").write_text(json.dumps(
            {"dataset": "fixture", "generated_at": "2026-11-26",
             "record_count": 1, "sources": [], "records": [la_rec]}))
        (fx / "states" / "TX.json").write_text(json.dumps({"records": tx_recs}))
        fx_db = build_store(fx)
        proc2, log2, fx_health = boot(PORT2, fx_db,
                                      "/tmp/filings_api_fixture_server.log")
        try:
            check("fixture: states merged at load",
                  fx_health["records"] == 3 and fx_health["states"] == ["LA", "TX"]
                  and fx_health["records_by_state"] == {"LA": 1, "TX": 2})
            s, body = get("/v1/businesses?state=TX&limit=10", key="test-basic", base=BASE2)
            check("fixture: state=TX finds adapter-schema records",
                  body["count"] == 2 and
                  all(r["filing_state"] == "TX" for r in body["results"]))
            s, body = get("/v1/businesses?region=Travis&limit=10", key="test-basic", base=BASE2)
            check("fixture: region matches county_or_parish", body["count"] == 1)
            s, body = get("/v1/businesses?state=TX&age_days=30&limit=10",
                          key="test-basic", base=BASE2)
            check("fixture: null filing_date excluded from age window",
                  body["count"] == 1 and body["results"][0]["id"] == "TX100")
            s, body = get("/v1/businesses/TX100", key="test-basic", base=BASE2)
            check("fixture: lookup by adapter id",
                  s == 200 and body["charter_number"] == "TX100"
                  and body["category"] == "food-and-restaurants")
            s, body = get("/v1/categories?state=TX", key="test-basic", base=BASE2)
            check("fixture: /v1/categories?state=TX", body["total_records"] == 2)
            s, body = get("/v1/regions", key="test-basic", base=BASE2)
            tx = body["states"]["TX"]
            check("fixture: /v1/regions TX county view",
                  tx["region_type"] == "county" and
                  tx["regions"][0] == {"region": "Travis", "filings": 1})
        finally:
            stop(proc2, log2)

        # ---- quota cutoff (free key used only here) ----------------------------------
        ok = all(get("/v1/parishes", key="test-free")[0] == 200 for _ in range(50))
        s, body = get("/v1/parishes", key="test-free")
        check("free key: 50/day quota cutoff -> 429",
              ok and s == 429 and body["error"] == "daily_quota_exceeded")
    finally:
        stop(proc, log)

    # ---- phase 3: the real multi-state store -------------------------------------
    full_db = ROOT / "data" / "filings.db"
    stats_path = ROOT / "data" / "filings.db.stats.json"
    if not (full_db.exists() and stats_path.exists()):
        print("SKIP  phase 3 (multi-state store not built — run build_db.py)")
    else:
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        by_state = stats["records_by_state"]
        con = sqlite3.connect(f"file:{full_db}?mode=ro", uri=True)

        def sql1(sql, args=()):
            return con.execute(sql, args).fetchone()[0]

        proc3, log3, health3 = boot(PORT3, full_db,
                                    "/tmp/filings_api_full_server.log")
        try:
            check("multi: health total + states",
                  health3["records"] == stats["total_records"] and
                  health3["states"] == sorted(by_state) and
                  health3["records_by_state"] == by_state,
                  f"{health3.get('records')} vs {stats['total_records']}")
            for st in sorted(by_state):
                s, body = get(f"/v1/businesses?state={st}&limit=1",
                              key="test-basic", base=BASE3)
                check(f"multi: state={st} count", body["count"] == by_state[st],
                      f"{body['count']} != {by_state[st]}")

            # every-city reach: a non-LA city filter with an all-scope key
            denver = sql1("SELECT COUNT(*) FROM filings WHERE filing_state='CO' "
                          "AND city_lower='denver'")
            s, body = get("/v1/businesses?state=CO&city=Denver&limit=1",
                          key="test-basic", base=BASE3)
            check("multi: city filter on a non-LA city (Denver, CO)",
                  body["count"] == denver, f"{body['count']} != {denver}")

            # city-scoped key on a non-LA city (Harrisburg, PA)
            harrisburg = sql1("SELECT COUNT(*) FROM filings WHERE "
                              "filing_state='PA' AND city_lower='harrisburg'")
            s, body = get("/v1/businesses?limit=1", key="test-city-pa", base=BASE3)
            check("multi: PA city key unfiltered count = Harrisburg only",
                  body["count"] == harrisburg,
                  f"{body['count']} != {harrisburg}")
            s, body = get("/v1/businesses?city=Philadelphia&limit=1",
                          key="test-city-pa", base=BASE3)
            check("multi: PA city key other city -> 0 + scope_note",
                  body["count"] == 0 and "scope_note" in body)
            la_charter = sql1("SELECT charter_number FROM filings WHERE "
                              "filing_state='LA' AND charter_number <> '' LIMIT 1")
            s, _ = get(f"/v1/businesses/{la_charter}", key="test-city-pa", base=BASE3)
            check("multi: PA city key LA record -> 404 (no leak)", s == 404)

            # state-scoped key on a non-LA state
            s, body = get("/v1/businesses?state=NY&limit=1",
                          key="test-state-ny", base=BASE3)
            check("multi: NY state key own state full count",
                  body["count"] == by_state["NY"])
            s, body = get("/v1/businesses?state=PA&limit=1",
                          key="test-state-ny", base=BASE3)
            check("multi: NY state key other state -> 0 + scope_note",
                  body["count"] == 0 and "scope_note" in body)

            # per-state category counts
            ny_other = sql1("SELECT COUNT(*) FROM filings WHERE "
                            "filing_state='NY' AND category='other-unclassified'")
            s, body = get("/v1/categories?state=NY", key="test-basic", base=BASE3)
            api_ny = {i["slug"]: i["records"] for i in body["industries"]}
            check("multi: /v1/categories?state=NY totals + industry count",
                  body["total_records"] == by_state["NY"] and
                  api_ny.get("other-unclassified") == ny_other,
                  f"total={body['total_records']} other={api_ny.get('other-unclassified')} "
                  f"expected {by_state['NY']}/{ny_other}")

            # age_days anchored to the queried state's own newest filing
            co_max = stats["state_max_dates"]["CO"]
            ref = datetime.strptime(co_max, "%Y-%m-%d").date()
            cutoff = (ref - timedelta(days=89)).isoformat()
            co_expected = sql1("SELECT COUNT(*) FROM filings WHERE "
                               "filing_state='CO' AND filing_date >= ?", (cutoff,))
            s, body = get("/v1/businesses?state=CO&age_days=90&limit=1",
                          key="test-basic", base=BASE3)
            check("multi: age_days=90 anchored to CO's own max date",
                  body["count"] == co_expected and
                  body.get("age_reference_date") == co_max,
                  f"{body['count']} != {co_expected}, ref={body.get('age_reference_date')}")

            # regions across states: PA counties present, county region type
            s, body = get("/v1/regions?state=PA", key="test-basic", base=BASE3)
            pa = body["states"]["PA"]
            check("multi: /v1/regions?state=PA county view",
                  pa["region_type"] == "county" and
                  pa["records_total"] == by_state["PA"] and
                  pa["regions_covered"] > 0)

            # ---- 2026-11-26 upgrades: CT enrichment + mill flag ---------------
            s, body = get("/v1/businesses?state=CT&limit=100",
                          key="test-basic", base=BASE3)
            check("multi: every record carries agent_is_commercial (bool)",
                  all(isinstance(r.get("agent_is_commercial"), bool)
                      for r in body["results"]))
            ct_row = con.execute(
                "SELECT charter_number, "
                "json_extract(record_json, '$.email_as_filed'), "
                "json_extract(record_json, '$.naics_code') FROM filings "
                "WHERE filing_state = 'CT' AND "
                "json_extract(record_json, '$.email_as_filed') IS NOT NULL "
                "LIMIT 1").fetchone()
            s, body = get(f"/v1/businesses/{ct_row[0]}?state=CT",
                          key="test-basic", base=BASE3)
            check("multi: CT enriched record serves email_as_filed + naics_code",
                  s == 200 and body.get("email_as_filed") == ct_row[1] and
                  body.get("naics_code") == ct_row[2],
                  f"{body.get('email_as_filed')} / {body.get('naics_code')} "
                  f"vs {ct_row[1]} / {ct_row[2]}")
            mill_row = con.execute(
                "SELECT charter_number FROM filings WHERE record_json LIKE "
                "'%NORTHWEST REGISTERED AGENT%' LIMIT 1").fetchone()
            s, body = get(f"/v1/businesses/{mill_row[0]}",
                          key="test-basic", base=BASE3)
            check("multi: Northwest-agent record flagged agent_is_commercial",
                  s == 200 and body.get("agent_is_commercial") is True)
        finally:
            stop(proc3, log3)
        con.close()

    failed = [n for n, ok2, _ in RESULTS if not ok2]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
