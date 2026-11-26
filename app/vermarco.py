#!/usr/bin/env python3
"""
Vermarco (sellmydata.ai / Vertical Marketplace) delivery endpoint.

Implements the seller-callback protocol from
85-sellmydata-bulk/RUNBOOK.md "Gate 2":

- The platform POSTs to our registered callback URL. Every POST is
  Ed25519-signed: headers X-VM-Key-Id, X-VM-Signature (base64url,
  detached), X-VM-Signature-Alg: ed25519, X-VM-Timestamp,
  X-VM-Body-Sha256 (lowercase hex SHA-256 of the raw body). The signed
  message is "POST\n<callbackUrl exactly as registered>\n<timestamp>\n
  <body-sha256-hex>". The platform public key comes from
  GET https://vermarco.com/api/signing-key (match keyId; re-fetch on
  unknown keyId — keys rotate).
- {"type":"verification_challenge", ...} must be answered within 15 s
  with HTTP 200 {"nonce": "<same nonce>"}.
- {"type":"delivery_request", listingId, fingerprint, title,
  verticalSlug, query, issuedAt} must be answered within 30 s with
  HTTP 200 {"data": {...}} — data a JSON object, response capped at
  10 MB. No data -> HTTP 404 / {"available": false} (buyer not charged).
- Optional integrity attestation: "__sha256": "sha256:<hex>" inside
  data, over the canonical JSON (keys recursively sorted, JCS /
  RFC 8785 with the JS integer-like-key exception; encoding label
  vermarco.sha256.v1).

Delivery is PER-QUERY over the filings DB (the 10 MB cap makes
whole-file delivery impossible): the buyer's query is mapped onto the
same filters the public search endpoint uses and the matching slice is
returned as JSON records.

Channel rules honored here (RUNBOOK per-family eligibility): the CT
enrichment column email_as_filed is NEVER delivered on this channel,
and only the forms-filings vertical is served from this deployment —
listings in other verticals answer {"available": false} until a data
service for their family exists.

Configuration (environment):
  VERMARCO_CALLBACK_URL       the URL exactly as registered with the
                              platform (signature verification needs it
                              byte-exact). Default:
                              https://filings-api.onrender.com/webhooks/vermarco
  VERMARCO_SIGNING_KEY_URL    default https://vermarco.com/api/signing-key
  VERMARCO_SIGNING_KEYS_JSON  optional {"keyId": "<base64url raw public
                              key>"} seed for the key cache (tests use
                              this; production normally fetches).

SPEC GAPS (also recorded in 85-sellmydata-bulk/state.json): the
RUNBOOK does not state (a) the exact field name carrying the challenge
nonce in the inbound challenge POST — "nonce" is assumed, "challenge"
accepted as a fallback; (b) the payload shape of checkout pre-probes
(6 s) and the ~10 min health sweep — any signed POST whose type is not
recognized is answered 200 {"ok": true} so probes succeed; (c) the
exact field vocabulary of delivery_request.query — a tolerant mapping
of the filings API's own filter names is applied and unknown keys are
ignored; (d) whether __sha256 is computed over `data` with or without
the __sha256 member itself — computed here over data WITHOUT the
member (the only self-consistent reading); (e) any timestamp
freshness tolerance — none is enforced (the signature + body hash are
verified; the timestamp is logged).
"""

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519

CALLBACK_URL = os.environ.get(
    "VERMARCO_CALLBACK_URL",
    "https://filings-api.onrender.com/webhooks/vermarco")
SIGNING_KEY_URL = os.environ.get(
    "VERMARCO_SIGNING_KEY_URL", "https://vermarco.com/api/signing-key")
SERVED_VERTICAL = "forms-filings"
MAX_RESPONSE_BYTES = 9_500_000  # platform cap is 10 MB; keep headroom
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# Staged $0 test-pack listings (85-sellmydata-bulk/state.json) whose
# titles promise a 25-record sample; used only as defaults UNDER an
# explicit buyer query (the query always wins).
LISTING_DEFAULTS = {
    "37cf116e-f3ed-4b5c-8471-5d5d843640eb": {"state": "PA", "limit": 25},
    "5bcb1558-9b55-47c7-937e-6987e6ef0bb3": {"state": "LA", "limit": 25},
}

# ---------------------------------------------------------------------------
# Platform signing keys
# ---------------------------------------------------------------------------

_KEY_CACHE = {}
_KEY_LOCK = threading.Lock()


def _b64url_decode(text):
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _seed_cache_from_env():
    raw = os.environ.get("VERMARCO_SIGNING_KEYS_JSON")
    if not raw:
        return
    try:
        for key_id, b64 in json.loads(raw).items():
            _KEY_CACHE[key_id] = _b64url_decode(b64)
    except Exception as exc:  # misconfigured env must not kill the API
        print(f"WARNING: VERMARCO_SIGNING_KEYS_JSON ignored: {exc}",
              file=sys.stderr)


_seed_cache_from_env()


def _fetch_platform_key():
    """Fetch the current signing key; returns (keyId, raw_pubkey) or None."""
    try:
        req = urllib.request.Request(
            SIGNING_KEY_URL, headers={"User-Agent": "SecondDesk-FilingsAPI/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"vermarco: signing-key fetch failed: {exc}", file=sys.stderr)
        return None
    key_id = doc.get("keyId")
    raw = None
    if doc.get("publicKeyRaw"):
        try:
            raw = _b64url_decode(doc["publicKeyRaw"])
        except Exception:
            raw = None
    if raw is None and doc.get("publicKeyPem"):
        try:
            b64 = "".join(line for line in doc["publicKeyPem"].splitlines()
                          if line and not line.startswith("-----"))
            der = base64.b64decode(b64)
            raw = der[-32:]  # SubjectPublicKeyInfo wraps the 32 raw bytes last
        except Exception:
            raw = None
    if key_id and raw and len(raw) == 32:
        return key_id, raw
    return None


def get_platform_key(key_id, allow_fetch=True):
    """Cached platform public key for key_id; fetches once on a miss."""
    with _KEY_LOCK:
        cached = _KEY_CACHE.get(key_id)
    if cached is not None or not allow_fetch:
        return cached
    fetched = _fetch_platform_key()
    if fetched is None:
        return None
    fetched_id, raw = fetched
    with _KEY_LOCK:
        _KEY_CACHE[fetched_id] = raw
    return raw if fetched_id == key_id else _KEY_CACHE.get(key_id)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_request(raw_body, headers, url_candidates):
    """Verify a platform POST. Returns (ok, detail_for_log).

    headers is any mapping with case-insensitive .get (the handler's
    email.message.Message qualifies). url_candidates are tried in
    order — the signed message must match one byte-exactly; the first
    candidate should be the registered callback URL.
    """
    key_id = headers.get("X-VM-Key-Id")
    sig_b64 = headers.get("X-VM-Signature")
    alg = headers.get("X-VM-Signature-Alg") or ""
    timestamp = headers.get("X-VM-Timestamp")
    body_sha = headers.get("X-VM-Body-Sha256")
    if not all([key_id, sig_b64, timestamp, body_sha]):
        return False, "missing X-VM-* signature headers"
    if alg.lower() != "ed25519":
        return False, f"unexpected signature alg {alg!r}"
    actual_sha = hashlib.sha256(raw_body).hexdigest()
    if not hmac.compare_digest(actual_sha, body_sha.strip().lower()):
        return False, "body sha256 mismatch"
    try:
        signature = _b64url_decode(sig_b64)
    except Exception:
        return False, "signature is not valid base64url"
    if len(signature) != 64:
        return False, "signature must be 64 bytes"
    pubkey = get_platform_key(key_id)
    if pubkey is None:
        return False, f"unknown key id {key_id!r}"
    for url in url_candidates:
        message = f"POST\n{url}\n{timestamp}\n{actual_sha}".encode("utf-8")
        if ed25519.verify(pubkey, message, signature):
            return True, "ok"
    return False, "signature does not verify"


# ---------------------------------------------------------------------------
# JCS (RFC 8785) canonical JSON + the __sha256 attestation
# ---------------------------------------------------------------------------


def _integer_like_index(key):
    """JS array-index-like key: canonical decimal of an integer in
    [0, 2**32 - 2]. Such keys order numerically BEFORE all other keys
    in JS object iteration — the 'integer-like-key exception'."""
    if not key or not key.isascii() or not key.isdigit():
        return None
    if len(key) > 1 and key[0] == "0":
        return None
    n = int(key)
    return n if n <= 2 ** 32 - 2 and str(n) == key else None


def _js_number(value):
    """ECMAScript Number::toString for the values JSON can carry."""
    if isinstance(value, int):
        return str(value)
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError("non-finite numbers are not valid JSON")
    if f == int(f) and abs(f) < 1e21:
        return str(int(f))
    r = repr(f)  # shortest round-trip decimal, like JS
    if "e" in r or "E" in r:
        mant, exp = re.split("[eE]", r)
        e = int(exp)
        return f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"
    return r


def canonical_json(obj):
    """Serialize obj as JCS (RFC 8785) canonical JSON (a str)."""
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, (int, float)):
        return _js_number(obj)
    if isinstance(obj, str):
        # json.dumps with ensure_ascii=False applies exactly the
        # escaping JCS requires (quote, backslash, C0 controls).
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in obj) + "]"
    if isinstance(obj, dict):
        indexed, plain = [], []
        for k in obj:
            n = _integer_like_index(k)
            (indexed if n is not None else plain).append((n, k))
        indexed.sort(key=lambda t: t[0])
        plain.sort(key=lambda t: t[1].encode("utf-16-be", "surrogatepass"))
        ordered = [k for _n, k in indexed] + [k for _n, k in plain]
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + canonical_json(obj[k])
            for k in ordered) + "}"
    raise TypeError(f"not JSON-canonicalizable: {type(obj)}")


def attestation(data):
    """'sha256:<hex>' over the canonical JSON of data (data must NOT
    already contain the __sha256 member)."""
    digest = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------
# Delivery: per-query slice over the filings store
# ---------------------------------------------------------------------------

_QUERY_KEYS = ("state", "city", "parish", "county", "region", "q", "name",
               "category", "need", "type", "origin", "year", "from", "to",
               "age_days", "limit")


def _loads_record(store_loads, raw):
    rec = store_loads(raw)
    # Channel rule (RUNBOOK eligibility): the CT enrichment email is
    # never delivered on this channel.
    rec.pop("email_as_filed", None)
    return rec


def run_delivery(store, loads_record, industries, needs, query, listing_id):
    """Execute a delivery query against the filings store.

    Returns (status, payload): (200, {"data": {...}}) on success,
    (404, {"available": False, ...}) when nothing matches.
    `loads_record` is the server's record_json parser (handles the
    zlib-compressed deploy build).
    """
    merged = dict(LISTING_DEFAULTS.get(listing_id) or {})
    for k in _QUERY_KEYS:
        if isinstance(query, dict) and query.get(k) not in (None, ""):
            merged[k] = query[k]

    def _s(key):
        v = merged.get(key)
        return str(v).strip() if v is not None else ""

    state = _s("state").upper()
    city = _s("city").lower()
    parish = _s("parish").lower()
    county = _s("county").lower()
    region = _s("region").lower()
    q = (_s("q") or _s("name")).lower()
    category = _s("category").lower()
    need = _s("need").lower()
    etype = _s("type").lower()
    origin = _s("origin").lower()
    year = _s("year")
    dfrom, dto = _s("from"), _s("to")
    if category and category not in industries:
        return 404, {"available": False, "error": "unknown_category",
                     "detail": f"Unknown category '{category}'."}
    if need and need not in needs:
        return 404, {"available": False, "error": "unknown_need",
                     "detail": f"Unknown need segment '{need}'."}
    try:
        limit = min(max(int(merged.get("limit") or DEFAULT_LIMIT), 1), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    age_cutoff = None
    if _s("age_days"):
        try:
            days = int(_s("age_days"))
            anchor = store.max_date_for(state or None)
            if days >= 1 and anchor:
                from datetime import datetime as _dt, timedelta as _td
                ref = _dt.strptime(anchor, "%Y-%m-%d").date()
                age_cutoff = (ref - _td(days=days - 1)).isoformat()
        except ValueError:
            age_cutoff = None

    clauses, args = ["1 = 1"], []
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

    count = store.query(
        f"SELECT COUNT(*) FROM filings WHERE {where}", args)[0][0]
    if count == 0:
        return 404, {"available": False, "error": "no_matching_records",
                     "detail": "No filings match the delivery query."}
    id_rows = store.query(
        "SELECT rowid FROM filings "
        f"WHERE {where} "
        "ORDER BY filing_date DESC, name_raw DESC LIMIT ?",
        [*args, limit])
    page_ids = [r[0] for r in id_rows]
    records = []
    if page_ids:
        marks = ",".join("?" * len(page_ids))
        by_id = {rid: js for rid, js in store.query(
            f"SELECT rowid, record_json FROM filings WHERE rowid IN ({marks})",
            page_ids)}
        records = [_loads_record(loads_record, by_id[rid]) for rid in page_ids]

    applied = {k: merged[k] for k in _QUERY_KEYS if merged.get(k) not in (None, "")}
    data = {
        "dataset": "New Business Filings — Second Desk, delivered via Vermarco",
        "listingId": listing_id,
        "verticalSlug": SERVED_VERTICAL,
        "query": applied,
        "count": count,
        "returned": len(records),
        "records": records,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Enforce the platform's 10 MB response cap by truncation (the
    # attestation is computed last, over the final shape).
    truncated = False
    while len(records) > 1 and len(
            json.dumps({"data": data}, separators=(",", ":")).encode("utf-8")
    ) > MAX_RESPONSE_BYTES:
        records = records[: max(1, len(records) // 2)]
        data["records"] = records
        data["returned"] = len(records)
        truncated = True
    if truncated:
        data["truncated"] = True
    data["__sha256"] = attestation(data)
    return 200, {"data": data}


# ---------------------------------------------------------------------------
# Request dispatch (called by the HTTP handler after signature checks)
# ---------------------------------------------------------------------------


def handle(store, loads_record, industries, needs, body):
    """Dispatch a verified platform POST body. Returns (status, obj)."""
    if not isinstance(body, dict):
        return 400, {"error": "bad_request", "detail": "JSON object required."}
    ptype = body.get("type")
    if ptype == "verification_challenge":
        nonce = body.get("nonce")
        if nonce is None:
            nonce = body.get("challenge")  # fallback field name (spec gap a)
        if nonce is None:
            return 400, {"error": "missing_nonce",
                         "detail": "Challenge carried no nonce."}
        return 200, {"nonce": nonce}
    if ptype == "delivery_request":
        vertical = body.get("verticalSlug")
        if vertical and vertical != SERVED_VERTICAL:
            # This deployment holds only the filings dataset.
            return 200, {"available": False}
        return run_delivery(store, loads_record, industries, needs,
                            body.get("query") or {},
                            body.get("listingId") or "")
    # Checkout pre-probes and the health sweep have no documented
    # payload shape (spec gap b): any other signed POST is a probe.
    return 200, {"ok": True}
