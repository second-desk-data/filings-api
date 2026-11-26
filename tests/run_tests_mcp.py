#!/usr/bin/env python3
"""
MCP server smoke battery for app/mcp_server.py.

Boots the MCP server over stdio (the transport local clients and
marketplace scanners use) against a tiny fixture store and checks:
initialize, the tool list, real tool answers from the fixture data,
API-key enforcement (missing key, city-scoped key), and scope
isolation. The streamable-http transport is exercised manually (see
app/mcp_server.py docstring); its header-key path shares the same
_auth() code as stdio's env-key path.

Requires the MCP SDK in the interpreter running this file:

    pip install -r requirements-mcp.txt
    python3 tests/run_tests_mcp.py

Without the SDK the battery prints SKIP and exits 0 (same convention
as phase 3 of tests/run_tests.py). Exit code 0 = all checks passed.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("SKIP  mcp SDK not installed in this interpreter — "
          "run: pip install -r requirements-mcp.txt")
    sys.exit(0)

KEYS = {
    "keys": [
        {"key": "test-basic", "tier": "basic", "daily_limit": 100000,
         "scope": {"type": "all"}},
        {"key": "test-city", "tier": "city", "daily_limit": 5000,
         "scope": {"type": "city", "state": "LA", "city": "Ville Platte"}},
    ]
}

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail and not cond else ""))


def build_fixture():
    fx = Path(tempfile.mkdtemp(prefix="mcpfx_"))
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
    out = fx / "filings.db"
    r = subprocess.run([sys.executable, str(ROOT / "build_db.py"),
                        "--data", str(fx / "seed.json"), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit("build_db.py failed")
    return out


async def with_session(db, key, fn):
    env = dict(os.environ, FILINGS_DB=str(db),
               API_KEYS_JSON=json.dumps(KEYS))
    if key is not None:
        env["FILINGS_API_KEY"] = key
    else:
        env.pop("FILINGS_API_KEY", None)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "app" / "mcp_server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


async def call(session, tool, args):
    result = await session.call_tool(tool, args)
    return json.loads(result.content[0].text)


async def main():
    db = build_fixture()

    async def basic(session):
        tools = await session.list_tools()
        check("mcp: tool list is the four filings tools",
              {t.name for t in tools.tools}
              == {"search_businesses", "get_business",
                  "list_regions", "list_categories"},
              str([t.name for t in tools.tools]))
        body = await call(session, "search_businesses", {"state": "TX"})
        check("mcp: search state=TX",
              body.get("count") == 2
              and {r["name"] for r in body["results"]}
              == {"Lone Star Cafe LLC", "Undated Ventures LLC"},
              str(body)[:200])
        body = await call(session, "get_business",
                          {"charter_number": "TX100"})
        check("mcp: get_business TX100",
              body.get("name") == "Lone Star Cafe LLC"
              and body.get("category") == "food-and-restaurants")
        body = await call(session, "list_categories", {"state": "TX"})
        check("mcp: list_categories TX total",
              body.get("total_records") == 2)
        body = await call(session, "list_regions", {})
        check("mcp: list_regions totals",
              body.get("records_total") == 3
              and set((body.get("states") or {}).keys()) == {"LA", "TX"})

    await with_session(db, "test-basic", basic)

    async def no_key(session):
        body = await call(session, "search_businesses", {"state": "TX"})
        check("mcp: missing key -> missing_api_key error",
              body.get("error") == "missing_api_key")

    await with_session(db, None, no_key)

    async def city_key(session):
        body = await call(session, "search_businesses", {})
        check("mcp: city key unfiltered search stays in Ville Platte",
              body.get("count") == 1
              and body["results"][0]["name"] == "Acme Trucking LLC",
              str(body)[:200])
        body = await call(session, "get_business",
                          {"charter_number": "TX100"})
        check("mcp: city key out-of-scope record -> not_found",
              body.get("error") == "not_found")
        body = await call(session, "search_businesses",
                          {"category": "bogus"})
        check("mcp: bad category -> bad_category with valid list",
              body.get("error") == "bad_category"
              and "valid_categories" in body)

    await with_session(db, "test-city", city_key)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} mcp checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
