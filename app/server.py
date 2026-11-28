#!/usr/bin/env python3
"""
New Business Filings API — stdlib-only implementation.

Serves new-business filing records for every state built into the
SQLite store (Louisiana + CO, CT, FL, IA, NY, OR, PA today), cleaned,
region-tagged, industry-classified, and segmented by likely need.

STORAGE: the server is DISK-BACKED. `build_db.py` converts data/seed.json
plus every data/states/<ST>.json into one SQLite database
(data/filings.db) at build time — normalization, industry classification
and need segmentation all happen there, with the one canonical
classifier (packs/classify.py). This process holds NO records in RAM:
every endpoint is an indexed SQL query against the database opened
read-only, so startup is instant and memory stays flat (~tens of MB)
regardless of corpus size. The full annotated record is stored as JSON
in the DB, so response shapes are unchanged from the old in-memory
server.

IMPORTANT field distinction: a record's `state` is the *mailing-address*
state exactly as filed (foreign entities often carry an out-of-state
mailing address). The state a filing belongs to — the one `state=`
filtering, per-key scope, and /v1/regions use — is `filing_state`.

Run:
    python3 build_db.py            # once, and after each data refresh
    python3 app/server.py [--port 8791] [--db data/filings.db] [--keys api_keys.json]

Deployment (Docker / Render / Fly): the host is configured entirely through
environment variables, no flags needed —
    PORT          listen port (default 8791; the host assigns this)
    HOST          bind address (default 127.0.0.1; deploy configs set 0.0.0.0)
    API_KEYS_JSON full key-store JSON (same shape as api_keys.json); when set
                  it is the deployment's key store. Keep real keys ONLY in
                  this variable on the host — never commit them.
    VAULT_KEYS_TOKEN
                  when API_KEYS_JSON is unset, a GitHub token with read
                  access to the PRIVATE second-desk-vault repo: the key
                  store is fetched at startup from the api-keys.json
                  asset on its keys-v1 release and held in memory only.
                  STAGE ONE: a failed fetch, or neither variable being
                  set, falls back to the bundled api_keys.json (the
                  published DEMO store) so current behavior is
                  unchanged until the vault route is proven live.
                  Stage two removes that fallback and fails closed.
                  The variable is declared in render.yaml with
                  sync: false, like API_KEYS_JSON, so the dashboard
                  value is part of the service's declared environment
                  on every deploy. At startup the server logs a
                  "Key store inputs" line (presence only, never a
                  value) and one "KEY STORE SOURCE" line naming the
                  store that won, so the resolution any boot actually
                  performed is provable from its log alone.

Auth: every /v1/* route requires an `X-API-Key` header. Keys carry a tier,
a daily request quota (in-memory counter, resets at UTC midnight), and a
`scope` (see RAPIDAPI-LISTING.md pricing):
    {"type": "all"}                          — unrestricted (free/demo keys)
    {"type": "state", "state": "LA"}         — one full state
    {"type": "city", "state": "LA", "city": "Ville Platte"} — one city
Out-of-scope records are excluded from results (with a `scope_note` in the
response metadata when the request explicitly asked outside the scope) and
single-record lookups outside the scope answer 404 — scope never leaks.

Endpoints:
    GET /health
    GET /openapi.yaml           (public OpenAPI 3 spec)
    GET /v1/businesses?q=&state=&parish=&county=&region=&city=&type=&origin=
                       &category=&need=&age_days=&year=&from=&to=&limit=&offset=
    GET /v1/businesses/{charter_number}?state=
    GET /v1/regions?state=
    GET /v1/parishes            (Louisiana alias of /v1/regions)
    GET /v1/categories?state=
    POST /webhooks/vermarco     (Vermarco delivery callback, Ed25519-signed)
    GET /.well-known/agent.json (public agent discovery document)
    GET /v1/catalog             (every sellable family: counts + prices)
    GET /v1/catalog/{family}    (one family's packs with prices)
    GET /v1/jobs, POST /v1/jobs, DELETE /v1/jobs/{id}
    GET /v1/resumes, POST /v1/resumes, DELETE /v1/resumes/{id}
                                (work board: roles posted by employers,
                                resumes listed by the candidates themselves)

Agent commerce: the entire buying path — catalog read, key auth, data
delivery, the work board — is plain HTTPS + the X-API-Key header.
No browser, no cookies, no JavaScript challenges anywhere on the API
surface, by design: an agent that can make an HTTPS request can buy
here. See AGENT-COMMERCE.md.

CORS: every response carries `Access-Control-Allow-Origin: *` and
OPTIONS preflights are answered for all routes. Wildcard is safe here
because the data routes authenticate per-request with X-API-Key (no
cookies/ambient authority) and the webhook authenticates by Ed25519
signature. Recorded in launch-kit/STEP-A-RESULTS.md per
launch-kit/CORS-REQUIREMENT.md.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
import zlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import classify`

# ---------------------------------------------------------------------------
# Classifier: app/classify.py is a slug-interface adapter over the ONE
# canonical rule set, packs/classify.py (shared with the $9 category packs
# so API filters and pack slices can never disagree). It raises at import
# time if the canonical file is missing — deliberately loud, never a
# silent divergent fallback. NOTE: classification itself now happens at
# DB build time (build_db.py); this import is for labels/slug validation.
# ---------------------------------------------------------------------------

import classify as CLASSIFY

CLASSIFIER_SOURCE = CLASSIFY.SOURCE

# Vermarco (sellmydata.ai) delivery-endpoint support: Ed25519-signed
# callbacks, per-query delivery slices, JCS attestation. See
# app/vermarco.py and 85-sellmydata-bulk/RUNBOOK.md (Gate 2).
import vermarco as VERMARCO

# Agent commerce layer: the sellable catalog (app/catalog.py, built by
# build_catalog.py from the catalog site lane's register) and the work
# board (app/board.py: jobs + self-listed resumes in one JSON store).
import catalog as CATALOG_MOD
import board as BOARD_MOD
import keyissue as KEYISSUE_MOD
import changelog as CHANGELOG_MOD

API_PUBLIC_BASE = "https://filings-api.onrender.com"
CONTACT_EMAIL = "seconddesks@proton.me"

PRICE_LOCK_POLICY = (
    "First-year price lock: a business that subscribes to a paid "
    "plan during the site's first year locks that rate for life "
    "while the plan stays continuously active; renewals always "
    "charge the locked rate. The founding offer (first 10 direct "
    "buyers at $49/mo locked for 12 months) stays honored, and where "
    "both could apply the buyer keeps whichever terms are better. A "
    "plan removed under the 30-day non-payment rule loses its lock; "
    "a returning business pays the then-current price.")

PLANS = [
    {"name": "Free", "price_usd_monthly": 0, "daily_requests": 50,
     "scope": "all data"},
    {"name": "City Heavy", "price_usd_monthly": 29,
     "daily_requests": 5000, "scope": "one city, bound to the key",
     "price_lock": "first-year subscribers lock $29 for life while "
                   "the plan stays continuously active"},
    {"name": "Full State Heavy", "price_usd_monthly": 59,
     "daily_requests": 10000, "scope": "one state, bound to the key",
     "price_lock": "first-year subscribers lock $59 for life while "
                   "the plan stays continuously active"},
    {"name": "Employer", "price_usd_monthly": 20,
     "scope": "work board: post jobs and search resumes at scale "
              "(reading the board stays free on any key)",
     "price_lock": "first-year subscribers lock $20 for life while "
                   "the plan stays continuously active"},
    {"name": "Overage", "price": "$0.005 per request beyond the "
     "plan's daily quota, on paid plans"},
]

# ---------------------------------------------------------------------------
# Data + key stores
# ---------------------------------------------------------------------------


def region_type_for(state):
    return "parish" if state == "LA" else "county"


def _loads_record(raw):
    """Parse one stored record_json value. Deploy builds made with
    `build_db.py --compress` store it as zlib-compressed bytes (BLOB);
    canonical builds store plain JSON text. Both parse to the identical
    dict, so responses are byte-identical either way."""
    if isinstance(raw, (bytes, bytearray)):
        raw = zlib.decompress(bytes(raw)).decode("utf-8")
    return json.loads(raw)


class Store:
    """Read-only SQLite store built by build_db.py.

    Holds only precomputed metadata (counts, date anchors) in memory —
    a few KB. All record access is SQL against the on-disk database via
    one read-only connection per serving thread.
    """

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._local = threading.local()
        con = self._con()
        meta = dict(con.execute("SELECT key, value FROM meta"))
        self.meta = json.loads(meta.get("dataset_meta") or "{}")
        self.total = int(meta["total_records"])
        self.states = json.loads(meta["states"])
        self.records_by_state = {k: int(v) for k, v in
                                 json.loads(meta["records_by_state"]).items()}
        self.min_date = meta.get("min_date") or None
        self.max_date = meta.get("max_date") or None
        self.state_min_dates = json.loads(meta.get("state_min_dates") or "{}")
        self.state_max_dates = json.loads(meta.get("state_max_dates") or "{}")
        self.has_counts = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'state_category_counts'").fetchone())

    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro",
                                  uri=True, check_same_thread=False)
            con.execute("PRAGMA cache_size = -1024")  # ~1 MB page cache
            con.execute("PRAGMA temp_store = MEMORY")
            self._local.con = con
        return con

    def query(self, sql, params=()):
        return self._con().execute(sql, params).fetchall()

    def max_date_for(self, state=None):
        """The age_days anchor: the newest filing date of the dataset in
        play — the given state's own newest filing when a single state is
        in play (explicit state= or a scoped key), else the global max.
        Mirrors the packs' definition (age vs the dataset's most recent
        filing) per state, so a state whose registry snapshot is older
        is not silently aged out by another state's fresher feed."""
        if state and self.state_max_dates.get(state):
            return self.state_max_dates[state]
        return self.max_date


class KeyStore:
    """API keys with per-tier daily quotas and per-key scope. In-memory usage."""

    def __init__(self, keys_path=None, cfg=None):
        if cfg is None:
            cfg = json.loads(Path(keys_path).read_text(encoding="utf-8"))
        if isinstance(cfg, list):  # tolerate a bare list of key entries
            cfg = {"keys": cfg}
        self.keys = {k["key"]: k for k in cfg["keys"]}
        self.usage = defaultdict(int)  # (key, yyyy-mm-dd) -> count
        self.lock = threading.Lock()

    @staticmethod
    def scope_of(entry):
        scope = entry.get("scope") or {"type": "all"}
        scope = dict(scope)
        if scope.get("state"):
            scope["state"] = scope["state"].upper()
        return scope

    def check(self, key):
        """Return (ok, info_dict). Consumes one unit of quota when ok."""
        if not key or key not in self.keys:
            return False, {"error": "invalid_or_missing_api_key",
                           "detail": "Send your key in the X-API-Key header."}
        return self._gate(key, self.keys[key])

    def _gate(self, usage_id, entry):
        """Plan lifecycle gate (see AGENT-COMMERCE.md, 'Paid plan
        lifecycle'): a key entry may carry status suspended/revoked,
        set by the operator or the billing wiring. Free keys are
        never suspended or revoked under that rule. Suspended answers
        a clear past-due body; revoked answers as an invalid key with
        its own code so the caller knows the difference."""
        status = entry.get("status", "active")
        if status == "suspended":
            return False, {
                "error": "plan_past_due",
                "detail": "This plan is past due, so its keys are "
                          "suspended until payment lands.",
                "next_step": "Pay the outstanding balance to restore "
                             "access. Keys are revoked 30 days after "
                             "the missed payment."}
        if status == "revoked":
            return False, {
                "error": "api_key_revoked",
                "detail": "This key has been revoked.",
                "next_step": "Get a new key: free keys are issued at "
                             "POST /v1/keys; paid plans via "
                             "seconddesks@proton.me."}
        return self._consume(usage_id, entry)

    def check_dynamic(self, usage_id, entry):
        """Quota check for a key resolved outside the static config
        (self-serve issued keys). usage_id is a non-secret identity
        (the key's hash), never the raw key."""
        return self._gate(usage_id, entry)

    def _consume(self, usage_id, entry):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            used = self.usage[(usage_id, today)]
            limit = entry.get("daily_limit", 100)
            if used >= limit:
                return False, {"error": "daily_quota_exceeded", "tier": entry["tier"],
                               "daily_limit": limit,
                               "detail": "Upgrade your plan for a higher daily quota."}
            self.usage[(usage_id, today)] = used + 1
            return True, {"tier": entry["tier"], "daily_limit": limit,
                          "daily_used": used + 1, "daily_remaining": limit - used - 1,
                          "scope": self.scope_of(entry)}


STORE = None
KEYS = None
CATALOG = None
BOARD = None
ISSUED = None

# Private key store route: when API_KEYS_JSON is unset but
# VAULT_KEYS_TOKEN is set, the key store is fetched at startup from
# the api-keys.json asset on the keys-v1 release of the PRIVATE
# second-desk-vault repo. The store is held in memory only — never
# written to disk. Stage one keeps the bundled-file fallback on any
# failure (see main()); stage two fails closed instead.
# VAULT_KEYS_RELEASE_URL overrides the release API URL for tests.
VAULT_KEYS_RELEASE_URL = os.environ.get(
    "VAULT_KEYS_RELEASE_URL",
    "https://api.github.com/repos/second-desk-data/second-desk-vault"
    "/releases/tags/keys-v1")
VAULT_KEYS_ASSET_NAME = "api-keys.json"


def fetch_vault_key_cfg(token):
    """Fetch and parse the key store JSON from the private vault
    release. Returns the parsed config (same shape as api_keys.json).
    Raises on any network, HTTP, or parse failure; callers fail
    closed. The response content is key material: it is returned to
    the caller only, never logged."""
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    req = urllib.request.Request(VAULT_KEYS_RELEASE_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read())
    asset = next((a for a in release.get("assets", [])
                  if a.get("name") == VAULT_KEYS_ASSET_NAME), None)
    if asset is None:
        raise ValueError("vault release carries no api-keys.json asset")
    headers = dict(headers, Accept="application/octet-stream")
    req = urllib.request.Request(asset["url"], headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_error_label(exc):
    """One safe log token for a failed vault fetch: the exception
    class, plus the HTTP status for an HTTPError. Never the message
    — messages can carry URLs or payload fragments, and no key
    material or token fragment may ever reach a log."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTPError {exc.code}"
    return type(exc).__name__


def fetch_vault_key_cfg_retry(token, attempts=4):
    """fetch_vault_key_cfg with retries. A cold-starting container
    can race its own network in the first seconds, and one transient
    failure must not decide the key store for the life of the
    process. Re-raises the last error after `attempts` tries."""
    delay = 2.0
    for attempt in range(attempts):
        try:
            return fetch_vault_key_cfg(token)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 1.5

_OPENAPI_CACHE = None


def openapi_spec_bytes():
    """The OpenAPI spec shipped at the repo root, served at
    /openapi.yaml. Read once and cached; None when the file is absent
    (e.g. a partial checkout) so the route can answer an honest 404."""
    global _OPENAPI_CACHE
    if _OPENAPI_CACHE is None:
        try:
            _OPENAPI_CACHE = (ROOT / "openapi.yaml").read_bytes()
        except OSError:
            return None
    return _OPENAPI_CACHE

# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


def scope_where(scope, prefix=""):
    """SQL fragment + params enforcing a key's scope. Mirrors the old
    in-memory scope_allows() exactly: a city key matches on filing_state
    and case-insensitive city; records with no city never match a city
    scope (NULL comparison), just as '' != the scoped city before.
    `prefix` (e.g. "f.") qualifies the columns for joined queries."""
    stype = scope.get("type", "all")
    if stype == "state":
        return f"{prefix}filing_state = ?", [scope.get("state")]
    if stype == "city":
        return (f"{prefix}filing_state = ? AND {prefix}city_lower = ?",
                [scope.get("state"), (scope.get("city") or "").lower()])
    return "1 = 1", []


def counts_state_key(scope, req_state):
    """Resolve precomputed-count access for /v1/categories + /v1/regions.

    Returns (mode, key):
      ("precomputed", state) — read the build-time per-state count tables
                               (key "*" = all states). Used whenever the
                               effective filter is global or one state:
                               counting a 2.4M-record state live joins
                               ~8M need rows per request (>120 s measured).
      ("live", None)         — city-scoped key: count live; the subset a
                               city key can see is small, so SQL is fast.
      ("empty", None)        — a state-scoped key asked for another state:
                               the result set is empty by definition.
    """
    stype = scope.get("type", "all")
    if stype == "city":
        return "live", None
    if stype == "state":
        if req_state and req_state != scope.get("state"):
            return "empty", None
        return "precomputed", scope.get("state")
    return "precomputed", req_state or "*"


def scope_public(scope):
    out = {"type": scope.get("type", "all")}
    if scope.get("state"):
        out["state"] = scope["state"]
    if scope.get("city"):
        out["city"] = scope["city"]
    return out


def scope_note_for(scope, req_state=None, req_city=None):
    """Explain scope limiting when the request explicitly asked outside it."""
    stype = scope.get("type", "all")
    if stype == "all":
        return None
    s_state = scope.get("state")
    if req_state and s_state and req_state != s_state:
        return (f"Your key is scoped to {scope_label(scope)}; records outside "
                f"that scope are excluded, so the requested state={req_state} "
                f"returns nothing.")
    if stype == "city" and req_city and req_city.lower() != (scope.get("city") or "").lower():
        return (f"Your key is scoped to {scope_label(scope)}; records outside "
                f"that scope are excluded, so the requested city={req_city} "
                f"returns nothing.")
    if stype == "city" and req_state and s_state and req_state == s_state and req_city is None:
        pass  # same state, no city asked: scope still applies silently via metadata
    return None


def scope_label(scope):
    if scope.get("type") == "city":
        return f"city {scope.get('city')}, {scope.get('state')}"
    if scope.get("type") == "state":
        return f"state {scope.get('state')}"
    return "all data"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "NewBizFilingsAPI/0.4"
    protocol_version = "HTTP/1.1"

    # -- plumbing -----------------------------------------------------------
    def log_message(self, fmt, *args):  # keep stdout readable
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # CORS (launch-kit/CORS-REQUIREMENT.md): the catalog storefront is
    # served from a separate origin and calls /v1/* from the browser.
    # Wildcard origin — see the module docstring for why that is safe
    # for this API's auth models.
    CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "X-API-Key, Content-Type, Authorization",
        "Access-Control-Max-Age": "86400",
    }

    def send_json(self, status, obj, extra_headers=None):
        body = json.dumps(obj, indent=1).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        headers = dict(self.CORS_HEADERS)
        headers.update(extra_headers or {})
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in self.CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # CORS preflight for any route. Echo the requested headers when
        # the browser names them; otherwise advertise the default set.
        headers = dict(self.CORS_HEADERS)
        requested = self.headers.get("Access-Control-Request-Headers")
        if requested:
            headers["Access-Control-Allow-Headers"] = requested
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()

    def authed(self):
        """Validate X-API-Key. Returns info dict or sends 401/429 and returns None."""
        raw = self.headers.get("X-API-Key")
        ok, info = KEYS.check(raw)
        if not ok and info.get("error") == "invalid_or_missing_api_key" \
                and ISSUED is not None and raw:
            entry = ISSUED.lookup(raw)
            if entry is not None:
                ok, info = KEYS.check_dynamic(ISSUED.usage_id(raw), entry)
        if not ok:
            status = {"daily_quota_exceeded": 429,
                      "plan_past_due": 402}.get(info.get("error"), 401)
            self.send_json(status, info)
            return None
        return info

    def client_ip(self):
        """Client IP for rate limiting: first X-Forwarded-For hop when
        a proxy sets it (the hosted deployment sits behind one), else
        the socket peer."""
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            first = fwd.split(",")[0].strip()
            if first:
                return first[:64]
        return self.client_address[0] if self.client_address else "unknown"

    def quota_headers(self, info):
        return {
            "X-RateLimit-Limit": str(info["daily_limit"]),
            "X-RateLimit-Remaining": str(info["daily_remaining"]),
            "X-Plan-Tier": info["tier"],
        }

    # -- routes ---------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path == "/health":
            return self.send_json(200, {
                "status": "ok",
                "dataset": STORE.meta.get("dataset"),
                "records": STORE.total,
                "states": STORE.states,
                "records_by_state": dict(STORE.records_by_state),
                "data_generated_at": STORE.meta.get("generated_at"),
                "filing_date_range": [STORE.min_date, STORE.max_date],
                "classifier": CLASSIFIER_SOURCE,
                "server_time": datetime.now(timezone.utc).isoformat(),
            })

        if path == "/":
            return self.send_json(200, {
                "name": "New Business Filings API",
                "version": "0.4",
                "description": "New business filings published by state "
                               "Secretaries of State, cleaned and tagged by "
                               "region (parish/county), industry, and likely "
                               "need. All built states are live from one "
                               "SQLite store; new states join by rebuilding "
                               "the store with build_db.py.",
                "docs": "See README.md",
                "openapi": "/openapi.yaml",
                "endpoints": {
                    "GET /health": "public health + dataset stats",
                    "GET /openapi.yaml": "OpenAPI 3 spec for this API (public)",
                    "GET /v1/businesses": "search filings (requires X-API-Key)",
                    "GET /v1/businesses/{charter_number}": "one record (requires X-API-Key)",
                    "GET /v1/regions": "coverage by region — parishes for LA, counties elsewhere (requires X-API-Key)",
                    "GET /v1/parishes": "Louisiana parishes (alias view of /v1/regions)",
                    "GET /v1/categories": "industries + likely-need segments with live counts (requires X-API-Key)",
                    "POST /webhooks/vermarco": "Vermarco marketplace delivery callback (Ed25519-signed, no API key)",
                    "GET /.well-known/agent.json": "agent discovery document: what is sold, plans, how to buy (public)",
                    "POST /v1/keys": "self-serve free key: send {\"email\": ...}, get a free-tier key immediately (public, rate limited, key shown once)",
                    "GET /changelog": "public release history with dates",
                    "GET /v1/catalog": "every sellable data family with record counts and price points (requires X-API-Key)",
                    "GET /v1/catalog/{family}": "one family's packs with prices and record counts (requires X-API-Key)",
                    "GET /v1/jobs": "open roles on the work board (requires X-API-Key)",
                    "POST /v1/jobs": "post a role to the work board (requires X-API-Key)",
                    "DELETE /v1/jobs/{id}": "remove a role you posted (requires X-API-Key)",
                    "GET /v1/resumes": "search resumes listed by the candidates themselves (requires X-API-Key)",
                    "POST /v1/resumes": "list your own resume summary (requires X-API-Key; consent required)",
                    "DELETE /v1/resumes/{id}": "remove your own resume listing (requires X-API-Key)",
                },
            })

        if path == "/.well-known/agent.json":
            return self.send_json(200, self.agent_card())

        if path == "/openapi.yaml":
            spec = openapi_spec_bytes()
            if spec is None:
                return self.send_json(404, {"error": "spec_not_found",
                                            "detail": "openapi.yaml is not present in this deployment."})
            return self.send_bytes(200, spec, "application/yaml")

        if path == "/webhooks/vermarco":
            # Unsigned liveness view of the delivery endpoint (the
            # protocol itself is POST + Ed25519; see do_POST).
            return self.send_json(200, {
                "endpoint": "vermarco-delivery",
                "status": "ok",
                "callback_url": VERMARCO.CALLBACK_URL,
                "served_vertical": VERMARCO.SERVED_VERTICAL,
                "served_verticals": list(VERMARCO.SERVED_VERTICALS),
                "packs_store": ("present" if VERMARCO.packs_store_present()
                                else "absent"),
                "listings_registered": len(VERMARCO.LISTING_REGISTRY),
            })

        if path == "/v1/regions":
            info = self.authed()
            if info is None:
                return
            return self.regions(qs, info)

        if path == "/v1/parishes":
            info = self.authed()
            if info is None:
                return
            return self.parishes(info)

        if path == "/v1/categories":
            info = self.authed()
            if info is None:
                return
            return self.categories(qs, info)

        if path == "/v1/businesses":
            info = self.authed()
            if info is None:
                return
            return self.search(qs, info)

        m = re.fullmatch(r"/v1/businesses/([A-Za-z0-9]+)", path)
        if m:
            info = self.authed()
            if info is None:
                return
            return self.lookup(m.group(1), qs, info)

        if path == "/v1/catalog":
            info = self.authed()
            if info is None:
                return
            return self.catalog_index(info)

        m = re.fullmatch(r"/v1/catalog/([a-z0-9][a-z0-9-]*)", path)
        if m:
            info = self.authed()
            if info is None:
                return
            return self.catalog_family(m.group(1), qs, info)

        if path == "/v1/jobs":
            info = self.authed()
            if info is None:
                return
            return self.board_list("jobs", qs, info)

        if path == "/v1/resumes":
            info = self.authed()
            if info is None:
                return
            return self.board_list("resumes", qs, info)

        if path == "/changelog":
            return self.send_json(200, CHANGELOG_MOD.document())

        return self.send_json(404, {"error": "unknown_route", "path": path})

    # -- POST routes ------------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/webhooks/vermarco":
            return self.vermarco_webhook()
        if path == "/v1/keys":
            return self.key_issue()
        if path in ("/v1/jobs", "/v1/resumes"):
            info = self.authed()
            if info is None:
                return
            return self.board_create(path.rsplit("/", 1)[1], info)
        return self.send_json(404, {"error": "unknown_route", "path": path})

    # -- DELETE routes ----------------------------------------------------------
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        m = re.fullmatch(r"/v1/(jobs|resumes)/([A-Za-z0-9-]+)", path)
        if m:
            info = self.authed()
            if info is None:
                return
            return self.board_delete(m.group(1), m.group(2), info)
        return self.send_json(404, {"error": "unknown_route", "path": path})

    # -- agent commerce: discovery, catalog, work board -------------------------

    def agent_card(self):
        """The public discovery document at /.well-known/agent.json."""
        return {
            "name": "Second Desk",
            "legal_name": "THE RAVEN AFFILIATES LLC",
            "description": (
                "Business data for agents and the teams behind them: "
                "new business filings, carrier, licensing, layoff, "
                "financial and registry datasets — read the catalog, "
                "authenticate with an API key, and query or buy, all "
                "server to server. No browser, no cookies, no "
                "JavaScript challenges anywhere on this API surface."),
            "site": "https://seconddesk.onrender.com/",
            "api_base": API_PUBLIC_BASE,
            "openapi": f"{API_PUBLIC_BASE}/openapi.yaml",
            "mcp_endpoint": f"{API_PUBLIC_BASE}/mcp",
            "catalog_endpoint": f"{API_PUBLIC_BASE}/v1/catalog",
            "contact_email": CONTACT_EMAIL,
            "auth": {
                "type": "api_key",
                "header": "X-API-Key",
                "how_to_get_a_key": (
                    "Self-serve: POST /v1/keys with "
                    "{\"email\": \"you@example.com\"} and a free-tier "
                    "key (50 requests/day, full catalog) comes back in "
                    "the same response — no browser, no human wait. "
                    "The key is shown once and stored hashed. Paid "
                    f"plans: email {CONTACT_EMAIL}."),
                "free_key_endpoint": "POST /v1/keys",
            },
            "plans": PLANS,
            "pricing_policy": PRICE_LOCK_POLICY,
            "changelog": f"{API_PUBLIC_BASE}/changelog",
            "endpoints": {
                "catalog": "GET /v1/catalog and "
                           "GET /v1/catalog/{family}",
                "data": "GET /v1/businesses, "
                        "GET /v1/businesses/{charter_number}, "
                        "GET /v1/regions, GET /v1/categories",
                "work_board": "GET/POST /v1/jobs, "
                              "GET/POST /v1/resumes",
            },
        }

    def key_issue(self):
        """POST /v1/keys — self-serve free key issuance. Public by
        design (it is how an agent gets its first key); abuse is
        bounded by per-email and per-IP rate limits, one active key
        per email, and the free tier's 50 requests/day cap."""
        if ISSUED is None:
            return self.send_json(503, {
                "error": "key_issuance_unavailable",
                "detail": "Key issuance is not configured on this "
                          "deployment.",
                "next_step": f"Email {CONTACT_EMAIL} for a key."})
        payload = self.read_json_body()
        if payload is None:
            return  # read_json_body already sent a clean 400/413
        if not isinstance(payload, dict):
            return self.send_json(400, {
                "error": "bad_body",
                "detail": "Request body must be a JSON object.",
                "next_step": "Send {\"email\": \"you@example.com\"}."})
        extra = sorted(set(payload) - {"email"})
        if extra:
            return self.send_json(400, {
                "error": "unexpected_field", "field": extra[0],
                "detail": "Only the email field is accepted.",
                "next_step": "Send {\"email\": \"you@example.com\"}."})
        if "email" not in payload:
            return self.send_json(400, {
                "error": "missing_field", "field": "email",
                "detail": "The email field is required.",
                "next_step": "Send {\"email\": \"you@example.com\"}."})
        try:
            result = ISSUED.issue(payload["email"], self.client_ip())
        except KEYISSUE_MOD.KeyIssueError as exc:
            headers = {}
            if exc.status == 429 and "retry_after_seconds" in exc.payload:
                headers["Retry-After"] = str(exc.payload["retry_after_seconds"])
            return self.send_json(exc.status, exc.payload, headers)
        except Exception as exc:  # never leak internals to the caller
            print(f"key issuance error: {type(exc).__name__}",
                  file=sys.stderr)
            return self.send_json(500, {"error": "internal_error"})
        return self.send_json(201, result)

    def catalog_index(self, info):
        if CATALOG is None:
            return self.send_json(503, {
                "error": "catalog_unavailable",
                "detail": "The catalog store is not loaded in this "
                          "deployment."}, self.quota_headers(info))
        return self.send_json(200, CATALOG.document(API_PUBLIC_BASE),
                              self.quota_headers(info))

    def catalog_family(self, slug, qs, info):
        if CATALOG is None:
            return self.send_json(503, {
                "error": "catalog_unavailable",
                "detail": "The catalog store is not loaded in this "
                          "deployment."}, self.quota_headers(info))
        fam = CATALOG.family(slug)
        if fam is None:
            return self.send_json(404, {
                "error": "unknown_family",
                "detail": f"No data family '{slug}'.",
                "valid_families": CATALOG.slugs},
                self.quota_headers(info))
        try:
            limit = min(max(int(qs.get("limit", 25)), 1), 100)
            offset = max(int(qs.get("offset", 0)), 0)
        except ValueError:
            return self.send_json(400, {
                "error": "bad_pagination",
                "detail": "limit and offset must be integers."},
                self.quota_headers(info))
        total, packs = CATALOG.packs(slug, limit, offset)
        return self.send_json(200, {
            "family": {k: fam[k] for k in
                       ("slug", "name", "description", "blurb", "packs",
                        "records", "site_url", "fields", "honesty",
                        "status")},
            "count": total,
            "limit": limit,
            "offset": offset,
            "packs": packs,
        }, self.quota_headers(info))

    def read_json_body(self, max_bytes=65536):
        """Read + parse a JSON request body for the board POSTs.

        Returns the parsed value, or None after sending a clean error
        (400 missing/invalid, 413 oversized). Error bodies never echo
        the request, the key, or any internal detail."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json(400, {"error": "bad_body",
                                 "detail": "Missing request body."})
            return None
        if length > max_bytes:
            self.send_json(413, {"error": "body_too_large",
                                 "detail": f"Request body exceeds "
                                           f"{max_bytes} bytes."})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "bad_json",
                                 "detail": "Request body is not valid JSON."})
            return None

    def board_create(self, kind, info):
        body = self.read_json_body()
        if body is None:
            return
        spec = BOARD_MOD.JOB_FIELDS if kind == "jobs" \
            else BOARD_MOD.RESUME_FIELDS
        try:
            fields = BOARD_MOD.validate(body, spec, kind)
            entry = BOARD.add(kind, fields, self.headers.get("X-API-Key"))
        except BOARD_MOD.BoardError as exc:
            status = 429 if exc.payload.get("error") == "board_full" \
                else 400
            return self.send_json(status, exc.payload,
                                  self.quota_headers(info))
        except Exception as exc:  # never leak a trace to the client
            sys.stderr.write(f"board: create {kind} failed: "
                             f"{type(exc).__name__}\n")
            return self.send_json(500, {"error": "internal_error"},
                                  self.quota_headers(info))
        return self.send_json(201, entry, self.quota_headers(info))

    def board_list(self, kind, qs, info):
        try:
            limit = min(max(int(qs.get("limit", 25)), 1), 100)
            offset = max(int(qs.get("offset", 0)), 0)
        except ValueError:
            return self.send_json(400, {
                "error": "bad_pagination",
                "detail": "limit and offset must be integers."},
                self.quota_headers(info))
        if kind == "jobs":
            q = qs.get("q", "").strip()
            loc = qs.get("location", "").strip()

            def match(e):
                if loc and not BOARD_MOD.keyword_match(e, "location", loc):
                    return False
                if q:
                    hay = " ".join(e.get(f, "") for f in
                                   ("title", "company", "location",
                                    "description")).lower()
                    if q.lower() not in hay:
                        return False
                return True
        else:
            role = qs.get("role", "").strip()
            loc = qs.get("location", "").strip()

            def match(e):
                return (BOARD_MOD.keyword_match(e, "role", role)
                        and BOARD_MOD.keyword_match(e, "location", loc))
        try:
            total, page = BOARD.list(kind, limit, offset, match)
        except Exception as exc:
            sys.stderr.write(f"board: list {kind} failed: "
                             f"{type(exc).__name__}\n")
            return self.send_json(500, {"error": "internal_error"},
                                  self.quota_headers(info))
        body = {"count": total, "limit": limit, "offset": offset,
                kind: page}
        if kind == "resumes":
            body["note"] = ("Resumes are listed by the candidates "
                            "themselves, with their consent, for "
                            "employers to find them. Nothing here is "
                            "scraped; the contact shown is the contact "
                            "each candidate chose to share.")
        return self.send_json(200, body, self.quota_headers(info))

    def board_delete(self, kind, entry_id, info):
        try:
            removed = BOARD.delete(kind, entry_id,
                                   self.headers.get("X-API-Key"))
        except Exception as exc:
            sys.stderr.write(f"board: delete {kind} failed: "
                             f"{type(exc).__name__}\n")
            return self.send_json(500, {"error": "internal_error"},
                                  self.quota_headers(info))
        if not removed:
            # Unknown id and another poster's id answer identically:
            # the board leaks nothing about other posters' entries.
            return self.send_json(404, {
                "error": "not_found",
                "detail": "No entry with that id for your key."},
                self.quota_headers(info))
        return self.send_json(200, {"deleted": entry_id},
                              self.quota_headers(info))

    def vermarco_webhook(self):
        """Vermarco delivery callback (85-sellmydata-bulk/RUNBOOK.md Gate 2).

        Auth is the platform's Ed25519 signature, NOT an API key. The
        raw body is read first because the signature covers it.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 1_048_576:
            return self.send_json(413 if length > 1_048_576 else 400,
                                  {"error": "bad_body",
                                   "detail": "Missing or oversized request body."})
        raw = self.rfile.read(length)
        proto = self.headers.get("X-Forwarded-Proto") or "http"
        host = self.headers.get("Host") or ""
        candidates = [VERMARCO.CALLBACK_URL]
        derived = f"{proto}://{host}/webhooks/vermarco"
        if derived not in candidates:
            candidates.append(derived)
        ok, detail = VERMARCO.verify_request(raw, self.headers, candidates)
        if not ok:
            sys.stderr.write(f"vermarco: rejected POST ({detail})\n")
            return self.send_json(401, {"error": "signature_verification_failed"})
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.send_json(400, {"error": "bad_json",
                                        "detail": "Request body is not valid JSON."})
        status, payload = VERMARCO.handle(
            STORE, _loads_record,
            CLASSIFY.INDUSTRY_SLUGS, CLASSIFY.NEED_SLUGS, body)
        return self.send_json(status, payload)

    # -- single record --------------------------------------------------------
    def lookup(self, charter, qs, info):
        scope = info["scope"]
        req_state = qs.get("state", "").strip().upper() or None
        frag, params = scope_where(scope)
        sql = ("SELECT record_json, filing_state FROM filings "
               f"WHERE charter_number = ? AND {frag}")
        args = [charter, *params]
        if req_state is not None:
            sql += " AND filing_state = ?"
            args.append(req_state)
        rows = STORE.query(sql, args)
        if not rows and charter.upper() != charter:
            # Historical behavior: LA-era callers may send a lowercase
            # charter. Retry uppercased — but only as a fallback, since
            # some states (CT) issue mixed-case ids that must match
            # exactly ("0018y000008iXJtAAM" is not "...IXJTAAM").
            args[0] = charter.upper()
            rows = STORE.query(sql, args)
        if not rows:
            # Out-of-scope records answer exactly like nonexistent ones.
            return self.send_json(404, {"error": "not_found",
                                        "detail": f"No filing with charter number {charter}."},
                                  self.quota_headers(info))
        if len(rows) > 1:
            states = sorted({r[1] for r in rows})
            return self.send_json(400, {"error": "ambiguous_charter",
                                        "detail": f"Charter number {charter} exists in "
                                                  f"{', '.join(states)}; pass state= to pick one."},
                                  self.quota_headers(info))
        return self.send_json(200, _loads_record(rows[0][0]), self.quota_headers(info))

    # -- regions / parishes ---------------------------------------------------
    def regions(self, qs, info):
        scope = info["scope"]
        req_state = qs.get("state", "").strip().upper() or None
        mode, key = counts_state_key(scope, req_state)
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
                    regs = sorted(by_state.get(st, []),
                                  key=lambda x: -x["filings"])
                    states_out[st] = {
                        "region_type": region_type_for(st),
                        "regions_covered": len(regs),
                        "records_with_region": sum(x["filings"] for x in regs),
                        "records_total": STORE.records_by_state[st],
                        "regions": regs,
                    }
                records_total = sum(STORE.records_by_state[st]
                                    for st in states_in_play)
            body = {"states": states_out, "records_total": records_total,
                    "scope": scope_public(scope)}
            note = scope_note_for(scope, req_state=req_state)
            if note:
                body["scope_note"] = note
            return self.send_json(200, body, self.quota_headers(info))
        frag, params = scope_where(scope)
        state_sql = " AND filing_state = ?" if req_state else ""
        state_args = [req_state] if req_state else []
        totals = dict(STORE.query(
            "SELECT filing_state, COUNT(*) FROM filings "
            f"WHERE {frag}{state_sql} GROUP BY filing_state",
            [*params, *state_args]))
        region_rows = STORE.query(
            "SELECT filing_state, region, COUNT(*) FROM filings "
            f"WHERE {frag}{state_sql} AND region IS NOT NULL AND region <> '' "
            "GROUP BY filing_state, region ORDER BY filing_state, COUNT(*) DESC, region",
            [*params, *state_args])
        by_state = {}
        for st, region, c in region_rows:
            by_state.setdefault(st, []).append({"region": region, "filings": c})
        states_out = {}
        for st in sorted(totals):
            regs = by_state.get(st, [])
            states_out[st] = {
                "region_type": region_type_for(st),
                "regions_covered": len(regs),
                "records_with_region": sum(x["filings"] for x in regs),
                "records_total": totals[st],
                "regions": regs,
            }
        body = {"states": states_out,
                "records_total": sum(totals.values()),
                "scope": scope_public(scope)}
        note = scope_note_for(scope, req_state=req_state)
        if note:
            body["scope_note"] = note
        return self.send_json(200, body, self.quota_headers(info))

    def parishes(self, info):
        scope = info["scope"]
        frag, params = scope_where(scope)
        total = STORE.query(
            "SELECT COUNT(*) FROM filings "
            f"WHERE filing_state = 'LA' AND {frag}", params)[0][0]
        rows = STORE.query(
            "SELECT parish, COUNT(*) FROM filings "
            f"WHERE filing_state = 'LA' AND {frag} "
            "AND parish IS NOT NULL AND parish <> '' "
            "GROUP BY parish ORDER BY COUNT(*) DESC, parish", params)
        body = {
            "parishes_covered": len(rows),
            "parishes_total_in_louisiana": 64,
            "records_with_parish": sum(c for _p, c in rows),
            "records_total": total,
            "parishes": [{"parish": p, "filings": c} for p, c in rows],
            "scope": scope_public(scope),
        }
        if scope.get("type") != "all" and scope.get("state") != "LA":
            body["scope_note"] = (f"Your key is scoped to {scope_label(scope)}; "
                                  "Louisiana parishes are outside that scope.")
        return self.send_json(200, body, self.quota_headers(info))

    # -- categories -------------------------------------------------------------
    def categories(self, qs, info):
        scope = info["scope"]
        req_state = qs.get("state", "").strip().upper() or None
        mode, key = counts_state_key(scope, req_state)
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
            frag, params = scope_where(scope)
            state_sql = " AND filing_state = ?" if req_state else ""
            state_args = [req_state] if req_state else []
            where = f"{frag}{state_sql}"
            args = [*params, *state_args]
            total = STORE.query(
                f"SELECT COUNT(*) FROM filings WHERE {where}", args)[0][0]
            ind_counts = dict(STORE.query(
                "SELECT category, COUNT(*) FROM filings "
                f"WHERE {where} GROUP BY category", args))
            frag_f, params_f = scope_where(scope, prefix="f.")
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
            "scope": scope_public(scope),
            "note": "Industry is inferred from the business name (state "
                    "filings carry no industry code). Needs are likely-need "
                    "marketing segments inferred from business type, never "
                    "verified needs.",
        }
        note = scope_note_for(scope, req_state=req_state)
        if note:
            body["scope_note"] = note
        return self.send_json(200, body, self.quota_headers(info))

    # -- search ---------------------------------------------------------------
    def search(self, qs, info):
        scope = info["scope"]
        try:
            limit = min(max(int(qs.get("limit", 25)), 1), 100)
            offset = max(int(qs.get("offset", 0)), 0)
        except ValueError:
            return self.send_json(400, {"error": "bad_pagination",
                                        "detail": "limit and offset must be integers."})
        q = qs.get("q", "").strip().lower()
        state = qs.get("state", "").strip().upper()
        parish = qs.get("parish", "").strip().lower()
        county = qs.get("county", "").strip().lower()
        region = qs.get("region", "").strip().lower()
        city = qs.get("city", "").strip().lower()
        etype = qs.get("type", "").strip().lower()
        origin = qs.get("origin", "").strip().lower()
        category = qs.get("category", "").strip().lower()
        need = qs.get("need", "").strip().lower()
        year = qs.get("year", "").strip()
        dfrom = qs.get("from", "").strip()
        dto = qs.get("to", "").strip()
        if year and not re.fullmatch(r"\d{4}", year):
            return self.send_json(400, {"error": "bad_year",
                                        "detail": "year must be YYYY."},
                                  self.quota_headers(info))
        for label, val in (("from", dfrom), ("to", dto)):
            if val and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
                return self.send_json(400, {"error": "bad_date",
                                            "detail": f"{label} must be YYYY-MM-DD."})
        if category and category not in CLASSIFY.INDUSTRY_SLUGS:
            return self.send_json(400, {"error": "bad_category",
                                        "detail": f"Unknown category '{category}'.",
                                        "valid_categories": CLASSIFY.INDUSTRY_SLUGS},
                                  self.quota_headers(info))
        if need and need not in CLASSIFY.NEED_SLUGS:
            return self.send_json(400, {"error": "bad_need",
                                        "detail": f"Unknown need segment '{need}'.",
                                        "valid_needs": CLASSIFY.NEED_SLUGS},
                                  self.quota_headers(info))
        age_days = None
        if qs.get("age_days", "").strip():
            try:
                age_days = int(qs["age_days"])
                if age_days < 1:
                    raise ValueError
            except ValueError:
                return self.send_json(400, {"error": "bad_age_days",
                                            "detail": "age_days must be a positive integer."},
                                      self.quota_headers(info))
        # age_days mirrors the pack slices exactly (packs/CATEGORIES.md):
        # age = (the dataset's most recent filing date - filing date).days,
        # and a record is in the window when age < age_days. The anchor is
        # per-state when one state is in play (explicit state= or the
        # key's scope state) — each state's registry is its own dataset
        # with its own "now" — and the global max otherwise. Measuring
        # from the dataset's max date keeps results stable between
        # weekly refreshes.
        age_cutoff = None
        age_ref = None
        if age_days is not None:
            anchor_state = state or (
                scope.get("state") if scope.get("type") in ("state", "city")
                else None)
            age_ref = STORE.max_date_for(anchor_state)
            if age_ref:
                ref = datetime.strptime(age_ref, "%Y-%m-%d").date()
                age_cutoff = (ref - timedelta(days=age_days - 1)).isoformat()

        frag, params = scope_where(scope)
        clauses = [frag]
        args = [*params]
        if state:
            clauses.append("filing_state = ?")
            args.append(state)
        if q:
            clauses.append("(instr(name_lower, ?) > 0 OR instr(name_raw_lower, ?) > 0)")
            args += [q, q]
        if parish:
            clauses.append("parish_lower = ?")
            args.append(parish)
        if county:
            clauses.append("county_lower = ?")
            args.append(county)
        if region:
            clauses.append("region_lower = ?")
            args.append(region)
        if city:
            clauses.append("city_lower = ?")
            args.append(city)
        if etype:
            clauses.append("entity_type_lower = ?")
            args.append(etype)
        if origin:
            clauses.append("origin = ?")
            args.append(origin)
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
            args.append(year)
        if dfrom:
            clauses.append("filing_date >= ?")
            args.append(dfrom)
        if dto:
            clauses.append("filing_date <= ?")
            args.append(dto)
        where = " AND ".join(clauses)

        count = STORE.query(
            f"SELECT COUNT(*) FROM filings WHERE {where}", args)[0][0]
        # Two-step page fetch: sort/order over narrow columns only
        # (rowid + filing_date + name_raw), then fetch the wide
        # record_json for just the page's rows. Sorting the full result
        # set WITH record_json attached moves gigabytes through the
        # sorter on state-sized queries; this way the sort stays small
        # and the wide fetch is ≤100 point lookups. Ordering matches the
        # old in-memory server: (filing_date, name_raw) descending with
        # undated records last (SQLite treats NULL as smallest, so DESC
        # puts them last; build_db stores '' dates as NULL).
        id_rows = STORE.query(
            "SELECT rowid FROM filings "
            f"WHERE {where} "
            "ORDER BY filing_date DESC, name_raw DESC LIMIT ? OFFSET ?",
            [*args, limit, offset])
        page_ids = [r[0] for r in id_rows]
        rows = []
        if page_ids:
            marks = ",".join("?" * len(page_ids))
            by_id = {rid: js for rid, js in STORE.query(
                f"SELECT rowid, record_json FROM filings WHERE rowid IN ({marks})",
                page_ids)}
            rows = [(by_id[rid],) for rid in page_ids]
        body = {
            "count": count,
            "limit": limit,
            "offset": offset,
            "results": [_loads_record(r[0]) for r in rows],
            "scope": scope_public(scope),
        }
        if age_days is not None:
            body["age_reference_date"] = age_ref
        note = scope_note_for(scope, req_state=state or None,
                              req_city=qs.get("city", "").strip() or None)
        if note:
            body["scope_note"] = note
        return self.send_json(200, body, self.quota_headers(info))


def main():
    global STORE, KEYS, CATALOG, BOARD, ISSUED
    try:
        default_port = int(os.environ.get("PORT", "8791"))
    except ValueError:
        print("ERROR: PORT environment variable is not an integer", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--db", default=None,
                    help="SQLite store built by build_db.py "
                         "(default: data/filings.db)")
    ap.add_argument("--data", default=str(ROOT / "data" / "seed.json"),
                    help="legacy: a seed JSON path — the filings.db built "
                         "beside it is used; or a .db path directly")
    ap.add_argument("--keys", default=str(ROOT / "api_keys.json"),
                    help="fallback key-store JSON file, loaded only "
                         "when neither API_KEYS_JSON nor a successful "
                         "VAULT_KEYS_TOKEN fetch provides a store")
    ap.add_argument("--catalog-db", default=None,
                    help="SQLite catalog store built by build_catalog.py "
                         "(default: data/catalog.db; when absent, "
                         "/v1/catalog answers 503)")
    ap.add_argument("--board-path", default=None,
                    help="work-board JSON store (default: "
                         "data/board.json; created on first post)")
    ap.add_argument("--issued-keys", default=None,
                    help="self-serve issued-keys JSON store (default: "
                         "data/issued_keys.json; keys stored hashed)")
    args = ap.parse_args()

    if args.db:
        db_path = Path(args.db)
    elif str(args.data).endswith(".db"):
        db_path = Path(args.data)
    else:
        db_path = Path(args.data).parent / "filings.db"
    if not db_path.exists():
        print(f"ERROR: SQLite store not found at {db_path}. "
              f"Build it first: python3 build_db.py", file=sys.stderr)
        return 1

    STORE = Store(db_path)
    # Key store resolution — exactly one source wins, in this order:
    #   (a) API_KEYS_JSON env var;
    #   (b) the private vault key store, fetched with
    #       VAULT_KEYS_TOKEN;
    #   (c) STAGE ONE fallback: the bundled demo file.
    # The inputs line records what this process actually saw —
    # presence only, never a value, a fragment, or a length — so a
    # boot whose environment did not carry a dashboard-set variable
    # is provable from the log alone. Values are stripped before
    # use: a dashboard paste can carry surrounding whitespace or a
    # trailing newline, which must not change the resolution.
    def _presence(name):
        raw = os.environ.get(name)
        if raw is None:
            return "absent"
        return "present" if raw.strip() else "present-but-empty"

    print("Key store inputs: "
          f"API_KEYS_JSON {_presence('API_KEYS_JSON')}, "
          f"VAULT_KEYS_TOKEN {_presence('VAULT_KEYS_TOKEN')}.")
    env_keys = (os.environ.get("API_KEYS_JSON") or "").strip()
    vault_token = (os.environ.get("VAULT_KEYS_TOKEN") or "").strip()
    if env_keys:
        try:
            KEYS = KeyStore(cfg=json.loads(env_keys))
        except (ValueError, KeyError, TypeError) as exc:
            print(f"ERROR: API_KEYS_JSON is not valid key-store JSON: {exc}",
                  file=sys.stderr)
            return 1
        print("KEY STORE SOURCE: API_KEYS_JSON environment variable.")
    elif vault_token:
        try:
            KEYS = KeyStore(cfg=fetch_vault_key_cfg_retry(vault_token))
        except Exception as exc:
            # Log the error class only — never the payload, the
            # token, or any key material. STAGE ONE: fall back to
            # the bundled key store so behavior is unchanged until
            # the vault route is proven live; stage two removes
            # this fallback and fails closed instead.
            KEYS = KeyStore(keys_path=args.keys)
            print("WARNING: vault key store fetch failed "
                  f"({fetch_error_label(exc)}) after retries. "
                  f"Falling back to {args.keys} "
                  "(stage one fallback).")
            print("KEY STORE SOURCE: bundled demo file "
                  f"{args.keys} (stage one fallback).")
        else:
            print("KEY STORE SOURCE: private vault key store "
                  "(release keys-v1, fetched at startup, "
                  "in memory only).")
    else:
        KEYS = KeyStore(keys_path=args.keys)
        print("KEY STORE SOURCE: bundled demo file "
              f"{args.keys} (stage one fallback).")
    catalog_db = Path(args.catalog_db
                      or os.environ.get("CATALOG_DB_PATH")
                      or (ROOT / "data" / "catalog.db"))
    if catalog_db.exists():
        CATALOG = CATALOG_MOD.CatalogStore(catalog_db)
        print(f"Catalog store {catalog_db}: "
              f"{len(CATALOG.families)} families, "
              f"{CATALOG.meta.get('packs')} packs.")
    else:
        print(f"Catalog store not found at {catalog_db}; "
              f"/v1/catalog will answer 503.")
    BOARD = BOARD_MOD.BoardStore(
        Path(args.board_path or os.environ.get("BOARD_STORE_PATH")
             or (ROOT / "data" / "board.json")))
    print(f"Work board store: {BOARD.path} "
          f"({len(BOARD.data['jobs'])} jobs, "
          f"{len(BOARD.data['resumes'])} resumes).")
    ISSUED = KEYISSUE_MOD.IssuedKeyStore(
        Path(args.issued_keys or os.environ.get("ISSUED_KEYS_PATH")
             or (ROOT / "data" / "issued_keys.json")))
    print(f"Issued-keys store: {ISSUED.path} "
          f"({len(ISSUED.state['keys'])} self-serve key(s)).")
    print(f"Serving SQLite store {db_path}: {STORE.total} records across "
          f"{len(STORE.states)} state(s) ({', '.join(STORE.states)}); "
          f"classifier: {CLASSIFIER_SOURCE}; "
          f"{len(KEYS.keys)} API key(s) configured.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
