#!/usr/bin/env python3
"""
New Business Filings API — MCP server (Model Context Protocol).

Exposes the filings dataset to MCP clients (Claude, Cursor, agent
runtimes, MCP marketplaces) as tools, backed by the SAME SQLite store,
the SAME key store, and the SAME scope/quota enforcement as the REST
API in app/server.py — this module imports that data layer rather than
re-implementing it, so the two surfaces can never disagree.

Tools:
    search_businesses   — same filters as GET /v1/businesses
    get_business        — one record by charter number (GET /v1/businesses/{n})
    list_regions        — coverage by parish/county (GET /v1/regions)
    list_categories     — industries + need segments with counts
                          (GET /v1/categories)

Requirements: the official Python MCP SDK (this module is NOT part of
the stdlib-only REST server and is not imported by it):

    pip install -r requirements-mcp.txt        # pins mcp>=1.2,<2

API key — the tools enforce the REST API's per-key scope and daily
quota, so every call needs a key from the same key store:
    * stdio transport: set the FILINGS_API_KEY environment variable.
    * streamable-http transport: send the key in the X-API-Key header
      of the MCP HTTP requests (read per-request); FILINGS_API_KEY is
      the fallback when no header is present.
Keys come from API_KEYS_JSON (same shape as the REST server), or
from the private vault key store when VAULT_KEYS_TOKEN is set —
identical semantics to app/server.py. With neither variable set the
server fails closed with an empty key store; the bundled
api_keys.json (published DEMO keys) is never loaded by default.

Data store: FILINGS_DB env var, else data/filings.db beside the repo
root (build it with build_db.py), exactly like the REST server.

Run:
    python3 app/mcp_server.py                          # stdio (default)
    python3 app/mcp_server.py --transport streamable-http --port 8792

The stdio transport is what local MCP clients and marketplace
scanners launch; streamable-http serves the tools at /mcp on the
given port for remote clients.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from mcp.server.fastmcp import Context, FastMCP  # noqa: E402

import classify as CLASSIFY  # noqa: E402 — shared slug/label tables
import server as API  # noqa: E402 — the REST server's data layer

# ---------------------------------------------------------------------------
# Boot: same store + key store as app/server.py
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get("FILINGS_DB") or (ROOT / "data" / "filings.db"))
if not DB_PATH.exists():
    raise SystemExit(f"SQLite store not found at {DB_PATH}. "
                     f"Build it first: python3 build_db.py")
STORE = API.Store(DB_PATH)
if os.environ.get("API_KEYS_JSON"):
    KEYS = API.KeyStore(cfg=json.loads(os.environ["API_KEYS_JSON"]))
elif os.environ.get("VAULT_KEYS_TOKEN"):
    try:
        KEYS = API.KeyStore(cfg=API.fetch_vault_key_cfg(
            os.environ["VAULT_KEYS_TOKEN"]))
    except Exception:
        # Fail closed; log only that the fetch failed, never content.
        print("WARNING: vault key store fetch failed. Failing closed.")
        KEYS = API.KeyStore(cfg={"keys": []})
else:
    # Fail closed, identical to app/server.py: never fall back to the
    # bundled api_keys.json — those are DEMO keys published in this
    # repo. With no key store configured, every call is refused.
    KEYS = API.KeyStore(cfg={"keys": []})

mcp = FastMCP(
    "New Business Filings API",
    instructions=(
        "Search new-business filings from state Secretaries of State "
        "(8 states, 5.7M+ records), cleaned and tagged by region "
        "(parish/county), industry category, and likely-need segment. "
        "Every call is scoped and quota-limited by the caller's API "
        "key, exactly like the REST API."),
)


# ---------------------------------------------------------------------------
# Auth: resolve the caller's key, then apply the REST KeyStore rules
# ---------------------------------------------------------------------------


def _caller_key(ctx):
    """X-API-Key header (HTTP transport) or FILINGS_API_KEY env (stdio)."""
    if ctx is not None:
        try:
            request = ctx.request_context.request
            if request is not None:
                key = request.headers.get("X-API-Key")
                if key:
                    return key
        except Exception:
            pass  # stdio transport has no HTTP request — env fallback
    return os.environ.get("FILINGS_API_KEY")


def _auth(ctx):
    """Returns (info, error_dict). Consumes one quota unit on success,
    exactly like one REST request."""
    key = _caller_key(ctx)
    if not key:
        return None, {
            "error": "missing_api_key",
            "detail": "Supply your API key: the FILINGS_API_KEY "
                      "environment variable (stdio) or the X-API-Key "
                      "header (streamable-http).",
        }
    ok, info = KEYS.check(key)
    if not ok:
        return None, info
    return info, None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_businesses(
    ctx: Context,
    q: Optional[str] = None,
    state: Optional[str] = None,
    parish: Optional[str] = None,
    county: Optional[str] = None,
    region: Optional[str] = None,
    city: Optional[str] = None,
    type: Optional[str] = None,
    origin: Optional[str] = None,
    category: Optional[str] = None,
    need: Optional[str] = None,
    age_days: Optional[int] = None,
    year: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Search business filings. All filters are optional and compose —
    the same parameters as GET /v1/businesses (from_date/to_date are
    its from/to). Returns {count, limit, offset, results, scope} with
    results ordered by filing date, newest first. Industry category
    and need are slugs from list_categories. age_days means filed
    within the last N days of the dataset's newest filing (per-state
    anchor when one state is in play)."""
    info, err = _auth(ctx)
    if err:
        return err
    scope = info["scope"]
    limit = min(max(int(limit), 1), 100)
    offset = max(int(offset), 0)
    category = (category or "").strip().lower()
    need = (need or "").strip().lower()
    if category and category not in CLASSIFY.INDUSTRY_SLUGS:
        return {"error": "bad_category",
                "detail": f"Unknown category '{category}'.",
                "valid_categories": CLASSIFY.INDUSTRY_SLUGS}
    if need and need not in CLASSIFY.NEED_SLUGS:
        return {"error": "bad_need",
                "detail": f"Unknown need segment '{need}'.",
                "valid_needs": CLASSIFY.NEED_SLUGS}
    state_u = (state or "").strip().upper()
    age_cutoff = None
    age_ref = None
    if age_days is not None:
        if int(age_days) < 1:
            return {"error": "bad_age_days",
                    "detail": "age_days must be a positive integer."}
        anchor_state = state_u or (
            scope.get("state") if scope.get("type") in ("state", "city")
            else None)
        age_ref = STORE.max_date_for(anchor_state)
        if age_ref:
            from datetime import datetime, timedelta
            ref = datetime.strptime(age_ref, "%Y-%m-%d").date()
            age_cutoff = (ref - timedelta(days=int(age_days) - 1)).isoformat()

    frag, params = API.scope_where(scope)
    clauses, args = [frag], [*params]
    if state_u:
        clauses.append("filing_state = ?")
        args.append(state_u)
    if q:
        clauses.append("(instr(name_lower, ?) > 0 OR instr(name_raw_lower, ?) > 0)")
        args += [q.strip().lower(), q.strip().lower()]
    for value, column in ((parish, "parish_lower"), (county, "county_lower"),
                          (region, "region_lower"), (city, "city_lower"),
                          (type, "entity_type_lower")):
        if value:
            clauses.append(f"{column} = ?")
            args.append(value.strip().lower())
    if origin:
        clauses.append("origin = ?")
        args.append(origin.strip().lower())
    if category:
        clauses.append("category = ?")
        args.append(category)
    if need:
        clauses.append("EXISTS (SELECT 1 FROM record_needs n "
                       "WHERE n.record_id = filings.rowid AND n.need_slug = ?)")
        args.append(need)
    if age_cutoff:
        clauses.append("filing_date >= ?")
        args.append(age_cutoff)
    if year:
        clauses.append("substr(filing_date, 1, 4) = ?")
        args.append(str(year).strip())
    if from_date:
        clauses.append("filing_date >= ?")
        args.append(from_date.strip())
    if to_date:
        clauses.append("filing_date <= ?")
        args.append(to_date.strip())
    where = " AND ".join(clauses)

    count = STORE.query(
        f"SELECT COUNT(*) FROM filings WHERE {where}", args)[0][0]
    id_rows = STORE.query(
        "SELECT rowid FROM filings "
        f"WHERE {where} "
        "ORDER BY filing_date DESC, name_raw DESC LIMIT ? OFFSET ?",
        [*args, limit, offset])
    page_ids = [r[0] for r in id_rows]
    results = []
    if page_ids:
        marks = ",".join("?" * len(page_ids))
        by_id = {rid: js for rid, js in STORE.query(
            f"SELECT rowid, record_json FROM filings WHERE rowid IN ({marks})",
            page_ids)}
        results = [API._loads_record(by_id[rid]) for rid in page_ids]
    body = {"count": count, "limit": limit, "offset": offset,
            "results": results, "scope": API.scope_public(scope),
            "quota": {"tier": info["tier"],
                      "daily_limit": info["daily_limit"],
                      "daily_remaining": info["daily_remaining"]}}
    if age_days is not None:
        body["age_reference_date"] = age_ref
    note = API.scope_note_for(scope, req_state=state_u or None,
                              req_city=(city or "").strip() or None)
    if note:
        body["scope_note"] = note
    return body


@mcp.tool()
def get_business(ctx: Context, charter_number: str,
                 state: Optional[str] = None) -> dict:
    """Look up one filing by its state charter/entity number (the
    record's stable ID). Pass state when a charter number exists in
    more than one state. Records outside the key's scope answer
    not_found, exactly like nonexistent ones."""
    info, err = _auth(ctx)
    if err:
        return err
    scope = info["scope"]
    frag, params = API.scope_where(scope)
    sql = ("SELECT record_json, filing_state FROM filings "
           f"WHERE charter_number = ? AND {frag}")
    args = [charter_number, *params]
    if state:
        sql += " AND filing_state = ?"
        args.append(state.strip().upper())
    rows = STORE.query(sql, args)
    if not rows and charter_number.upper() != charter_number:
        args[0] = charter_number.upper()
        rows = STORE.query(sql, args)
    if not rows:
        return {"error": "not_found",
                "detail": f"No filing with charter number {charter_number}."}
    if len(rows) > 1:
        states = sorted({r[1] for r in rows})
        return {"error": "ambiguous_charter",
                "detail": f"Charter number {charter_number} exists in "
                          f"{', '.join(states)}; pass state to pick one."}
    return API._loads_record(rows[0][0])


@mcp.tool()
def list_regions(ctx: Context, state: Optional[str] = None) -> dict:
    """Filing counts by region — parishes for Louisiana, counties for
    other states. Optional state narrows to one state. Counts respect
    the key's scope. Same data as GET /v1/regions."""
    info, err = _auth(ctx)
    if err:
        return err
    scope = info["scope"]
    req_state = (state or "").strip().upper() or None
    mode, key = API.counts_state_key(scope, req_state)
    if mode != "live" and STORE.has_counts:
        if mode == "empty":
            states_out, records_total = {}, 0
        else:
            states_in_play = (STORE.states if key == "*" else
                              [key] if key in STORE.records_by_state else [])
            by_state = {}
            if states_in_play:
                marks = ",".join("?" * len(states_in_play))
                for st, region, c in STORE.query(
                        "SELECT state, region, records FROM "
                        f"state_region_counts WHERE state IN ({marks})",
                        states_in_play):
                    by_state.setdefault(st, []).append(
                        {"region": region, "filings": c})
            states_out = {}
            for st in states_in_play:
                regs = sorted(by_state.get(st, []), key=lambda x: -x["filings"])
                states_out[st] = {
                    "region_type": API.region_type_for(st),
                    "regions_covered": len(regs),
                    "records_with_region": sum(x["filings"] for x in regs),
                    "records_total": STORE.records_by_state[st],
                    "regions": regs,
                }
            records_total = sum(STORE.records_by_state[st]
                                for st in states_in_play)
        body = {"states": states_out, "records_total": records_total,
                "scope": API.scope_public(scope)}
    else:
        frag, params = API.scope_where(scope)
        state_sql = " AND filing_state = ?" if req_state else ""
        state_args = [req_state] if req_state else []
        totals = dict(STORE.query(
            "SELECT filing_state, COUNT(*) FROM filings "
            f"WHERE {frag}{state_sql} GROUP BY filing_state",
            [*params, *state_args]))
        by_state = {}
        for st, region, c in STORE.query(
                "SELECT filing_state, region, COUNT(*) FROM filings "
                f"WHERE {frag}{state_sql} AND region IS NOT NULL AND region <> '' "
                "GROUP BY filing_state, region "
                "ORDER BY filing_state, COUNT(*) DESC, region",
                [*params, *state_args]):
            by_state.setdefault(st, []).append({"region": region, "filings": c})
        states_out = {}
        for st in sorted(totals):
            regs = by_state.get(st, [])
            states_out[st] = {
                "region_type": API.region_type_for(st),
                "regions_covered": len(regs),
                "records_with_region": sum(x["filings"] for x in regs),
                "records_total": totals[st],
                "regions": regs,
            }
        body = {"states": states_out,
                "records_total": sum(totals.values()),
                "scope": API.scope_public(scope)}
    note = API.scope_note_for(scope, req_state=req_state)
    if note:
        body["scope_note"] = note
    return body


@mcp.tool()
def list_categories(ctx: Context, state: Optional[str] = None) -> dict:
    """Every industry category and likely-need segment with live
    record counts. Optional state narrows to one state; counts respect
    the key's scope. Same data as GET /v1/categories."""
    info, err = _auth(ctx)
    if err:
        return err
    scope = info["scope"]
    req_state = (state or "").strip().upper() or None
    mode, key = API.counts_state_key(scope, req_state)
    if mode == "precomputed" and STORE.has_counts:
        total = (STORE.total if key == "*"
                 else STORE.records_by_state.get(key, 0))
        ind_counts = dict(STORE.query(
            "SELECT slug, records FROM state_category_counts "
            "WHERE state = ?", [key]))
        need_counts = dict(STORE.query(
            "SELECT slug, records FROM state_need_counts "
            "WHERE state = ?", [key]))
    elif mode == "empty":
        total, ind_counts, need_counts = 0, {}, {}
    else:
        frag, params = API.scope_where(scope)
        state_sql = " AND filing_state = ?" if req_state else ""
        state_args = [req_state] if req_state else []
        where = f"{frag}{state_sql}"
        args = [*params, *state_args]
        total = STORE.query(
            f"SELECT COUNT(*) FROM filings WHERE {where}", args)[0][0]
        ind_counts = dict(STORE.query(
            "SELECT category, COUNT(*) FROM filings "
            f"WHERE {where} GROUP BY category", args))
        frag_f, params_f = API.scope_where(scope, prefix="f.")
        f_state_sql = " AND f.filing_state = ?" if req_state else ""
        need_counts = dict(STORE.query(
            "SELECT n.need_slug, COUNT(*) FROM record_needs n "
            "JOIN filings f ON f.rowid = n.record_id "
            f"WHERE {frag_f}{f_state_sql} GROUP BY n.need_slug",
            [*params_f, *state_args]))
    body = {
        "state": req_state or "all",
        "total_records": total,
        "industries": [
            {"slug": slug, "label": CLASSIFY.INDUSTRY_LABELS[slug],
             "records": ind_counts.get(slug, 0)}
            for slug in sorted(CLASSIFY.INDUSTRY_SLUGS,
                               key=lambda s: -ind_counts.get(s, 0))
        ],
        "needs": [
            {"slug": slug, "label": CLASSIFY.NEED_LABELS[slug],
             "description": CLASSIFY.NEED_DESCRIPTIONS[slug],
             "records": need_counts.get(slug, 0)}
            for slug in sorted(CLASSIFY.NEED_SLUGS,
                               key=lambda s: -need_counts.get(s, 0))
        ],
        "scope": API.scope_public(scope),
    }
    note = API.scope_note_for(scope, req_state=req_state)
    if note:
        body["scope_note"] = note
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--transport", choices=["stdio", "streamable-http"],
                    default="stdio")
    ap.add_argument("--port", type=int, default=8792,
                    help="streamable-http listen port")
    ap.add_argument("--host", default="127.0.0.1",
                    help="streamable-http bind address")
    args = ap.parse_args()
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
    print(f"Filings MCP server: {STORE.total} records, "
          f"{len(STORE.states)} states, transport={args.transport}",
          file=sys.stderr)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
