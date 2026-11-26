# New Business Filings API

Every new business filed with the state, as a clean JSON API — searchable
by **state**, region (parish/county), city, name, entity type, **industry
category**, **likely-need segment**, and **filing age**. Louisiana is live
today; the data model is multi-state (see "Adding a state" below).

The Louisiana Secretary of State publishes new filings once a week as raw
text newsletters: thousands of lines, no structure, no search, no parish
field. This API does the unglamorous work once — fetch, parse, clean,
region-tag, classify — and serves the result as a subscription REST API
for people who need new-business data in their own systems: lead
generators, lenders, insurers, franchisors, local marketers, and
researchers.

- **Coverage:** 126,710 Louisiana filings from 2024-11-24 → 2026-11-21
  (two full years, backfilled from 104 consecutive weekly files), 63 of
  64 parishes, refreshed weekly from the source (see `data/SOURCES.md`).
- **Stack:** Python 3 standard library only. No dependencies, nothing to pip.
- **Auth:** API key in the `X-API-Key` header, with per-tier daily quotas
  and per-key **scope** (all data / one state / one city — see "Scope").
- **Categories:** industry and likely-need classification is shared with
  the $9 CSV category packs — one rule set (`packs/classify.py`,
  documented in `packs/CATEGORIES.md`), so API filters and pack slices
  always agree. Industry is inferred from the business name (filings
  carry no industry code); needs are likely-need marketing segments,
  never verified needs.

## Quickstart

```bash
python3 refresh.py --weeks 2        # refresh data/seed.json (merges into history)
python3 build_db.py                 # rebuild the SQLite store (data/filings.db)
python3 app/server.py --port 8791   # serve
```

Deploying (Docker / Render / Fly): a `Dockerfile`, `render.yaml`, and
`fly.toml` are included. The server reads `PORT` and `HOST` from the
environment and takes real API keys from the `API_KEYS_JSON` environment
variable (same JSON shape as `api_keys.json`, which holds DEMO keys only).
See `LAUNCH-STATUS.md` for the launch checklist.

Demo keys (local testing only — see `api_keys.json`):

| Key | Tier | Daily quota | Scope |
|---|---|---|---|
| `demo-free-key-0001` | free | 50 | all |
| `demo-basic-key-0002` | basic | 1,000 | all |
| `demo-pro-key-0003` | pro | 10,000 | all |
| `demo-city-key-0004` | city (City Heavy, $29/mo) | 5,000 | city: Ville Platte, LA |
| `demo-state-key-0005` | state (Full State Heavy, $59/mo) | 10,000 | state: LA |

## Endpoints

### `GET /health` — public
Dataset status and freshness.

```json
{
  "status": "ok",
  "dataset": "Louisiana New Business Filings (Secretary of State NewCorp newsletters)",
  "records": 126710,
  "states": ["LA"],
  "records_by_state": {"LA": 126710},
  "data_generated_at": "2026-11-26",
  "filing_date_range": ["2024-11-24", "2026-11-21"],
  "classifier": "packs/classify.py (via app/classify.py adapter)"
}
```

### `GET /v1/businesses` — search filings 🔑
Query parameters (all optional, all combinable — filters compose):

| Param | Meaning | Example |
|---|---|---|
| `q` | substring match on business name (case-insensitive) | `q=transport` |
| `state` | filing state (the state whose filings these are — see `filing_state` below) | `state=LA` |
| `parish` | Louisiana parish (exact, case-insensitive) | `parish=Evangeline` |
| `county` | county for non-LA states (exact, case-insensitive) | `county=Travis` |
| `region` | parish or county, whichever the record's state uses | `region=Evangeline` |
| `city` | mailing city (exact, case-insensitive) | `city=Ville Platte` |
| `type` | `LLC`, `Corporation`, `Nonprofit Corporation`, `Foreign Entity`, `Other` | `type=LLC` |
| `origin` | `domestic` or `foreign` | `origin=domestic` |
| `category` | industry slug from `/v1/categories` (name-inferred) | `category=trucking-and-transport` |
| `need` | likely-need segment slug from `/v1/categories` | `need=likely-hiring-cdl-drivers` |
| `age_days` | filed within the last N days of the dataset's newest filing (age < N, same definition as the packs) | `age_days=90` |
| `year` | calendar year of filing | `year=2025` |
| `from` / `to` | filing-date range, `YYYY-MM-DD` | `from=2026-11-20&to=2026-11-21` |
| `limit` | page size, 1–100 (default 25) | `limit=50` |
| `offset` | pagination offset (default 0) | `offset=50` |

Responses carry a `scope` object describing the key's bound scope, and a
`scope_note` when the request explicitly asked outside that scope.

Example — new LLCs in Evangeline Parish:

```bash
curl -H "X-API-Key: demo-basic-key-0002" \
  "http://127.0.0.1:8791/v1/businesses?parish=Evangeline&limit=2"
```

```json
{
  "count": 441,
  "limit": 2,
  "offset": 0,
  "results": [
    {
      "id": "47303845K",
      "charter_number": "47303845K",
      "name": "Turning Right Indoor Golf, LLC",
      "entity_type": "LLC",
      "origin": "domestic",
      "filing_date": "2026-11-20",
      "address": "1100 SAWMILL RD VILLE PLATTE, LA 70586",
      "city": "Ville Platte",
      "state": "LA",
      "zip": "70586",
      "parish": "Evangeline",
      "registered_agent": "Joshua Ortego",
      "agents": ["Joshua Ortego"],
      "source_file": "NewCorp_20261124.txt",
      "source_url": "https://corafiles.sos.la.gov/NewCorpNewsletters/NewCorp_20261124.txt",
      "filing_state": "LA",
      "region": "Evangeline",
      "category": "other-unclassified",
      "category_label": "Other / Unclassified",
      "needs": ["needs-website", "needs-phone-internet", "needs-bookkeeping"]
    }
  ],
  "scope": {"type": "all"}
}
```

Filters compose — e.g. trucking companies statewide filed in the last
90 days: `/v1/businesses?category=trucking-and-transport&age_days=90`.

### `GET /v1/businesses/{charter_number}` — one record 🔑
```bash
curl -H "X-API-Key: demo-basic-key-0002" http://127.0.0.1:8791/v1/businesses/47303845K
```
Returns the single record, or `404 {"error": "not_found"}`. Pass `?state=`
if a charter number exists in more than one state (otherwise `400
ambiguous_charter`). Records outside the key's scope answer 404, exactly
like nonexistent ones.

### `GET /v1/regions` — coverage by region 🔑
The general coverage endpoint: parishes for Louisiana, counties for other
states. Optional `?state=` narrows to one state.
```json
{
  "states": {
    "LA": {
      "region_type": "parish",
      "regions_covered": 63,
      "records_with_region": 110693,
      "records_total": 126710,
      "regions": [{"region": "East Baton Rouge", "filings": 13208}, {"region": "Orleans", "filings": 12116}]
    }
  },
  "records_total": 126710,
  "scope": {"type": "all"}
}
```

### `GET /v1/parishes` — Louisiana coverage 🔑
Backward-compatible Louisiana view (same numbers as `/v1/regions` for LA):
```json
{
  "parishes_covered": 63,
  "parishes_total_in_louisiana": 64,
  "records_with_parish": 110693,
  "records_total": 126710,
  "parishes": [{"parish": "East Baton Rouge", "filings": 13208}, {"parish": "Orleans", "filings": 12116}]
}
```

### `GET /v1/categories` — industries + need segments 🔑
Lists every industry category and likely-need segment with **live record
counts** (optional `?state=`; counts always respect the key's scope).
This powers the pack catalog and buyer browsing.
```json
{
  "state": "all",
  "total_records": 126710,
  "industries": [{"slug": "other-unclassified", "label": "Other / Unclassified", "records": 80808}],
  "needs": [{"slug": "needs-website", "label": "Needs a Website", "records": 126710}]
}
```
(Counts shown are the real Louisiana numbers as of the last test run;
call the endpoint for live ones.)

## Auth, quotas, scope, errors

Every `/v1/*` response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
and `X-Plan-Tier` headers. Errors are JSON:

| Status | Meaning |
|---|---|
| `401` | missing/invalid key — `{"error": "invalid_or_missing_api_key"}` |
| `429` | daily quota used up — `{"error": "daily_quota_exceeded", "tier": ..., "daily_limit": ...}` |
| `400` | bad parameter (malformed date, unknown `category`/`need` slug, bad `age_days`/`year`) |
| `404` | unknown route, unknown charter number, or a record outside the key's scope |

**Scope** (the pricing model in `RAPIDAPI-LISTING.md`): each key carries a
`scope` in the key store — `{"type": "all"}`, `{"type": "state",
"state": "LA"}` (Full State Heavy, $59/mo), or `{"type": "city",
"state": "LA", "city": "Ville Platte"}` (City Heavy, $29/mo). Scope is
enforced on every endpoint: out-of-scope records are excluded from search
results and counts (with a `scope_note` in the response metadata when the
request explicitly asked outside the scope), `/v1/regions` and
`/v1/categories` count only in-scope records, and single-record lookups
outside the scope return 404. Free keys are scope `all` with the 50/day
cap.

In production on RapidAPI, the marketplace injects and validates subscriber
keys itself; the local key store here mirrors the same tier/quota structure
so the paid model is real end-to-end in the demo.

## Record schema

| Field | Notes |
|---|---|
| `charter_number` / `id` | the state's charter number — the stable ID |
| `name` / `name_raw` | display name / name exactly as filed |
| `entity_type` | heuristic from charter suffix — see `data/SOURCES.md` |
| `origin` | `domestic` (new in-state entity) or `foreign` (out-of-state entity qualifying in-state) |
| `filing_date` | ISO date the filing was recorded |
| `address`, `city`, `state`, `zip` | mailing address as filed — **`state` here is the mailing-address state**, which for foreign entities is often out-of-state |
| `filing_state` | the state whose filings the record belongs to — this is what `state=` filtering, scope, and `/v1/regions` use |
| `parish` | Louisiana only: derived from city; `null` when it can't be assigned honestly |
| `county` | non-LA states, when the source provides it |
| `region` | the record's parish or county, whichever its state uses |
| `category` / `category_label` | industry slug + label, inferred from the business name by `packs/classify.py` |
| `needs` | likely-need segment slugs the record belongs to (same rules as the packs) |
| `registered_agent`, `agents` | from the filing |
| `agent_is_commercial` | true when the registered agent's name matches a known commercial formation/registered-agent mill (Registered Agents Inc, Northwest, CSC, LegalZoom, ZenBusiness, Incfile/Bizee, Cogency Global, CT Corporation, Paracorp) — name-matched only, additive only; the record itself is never altered or dropped |
| `email_as_filed` | Connecticut only: the business email address the filer itself published in the state registry (813,554 of 1,310,099 CT records) — a public record, **not** marketing consent |
| `naics_code`, `naics_title` | Connecticut only: the NAICS code/title the filer reported to the registry (625,411 / 612,190 records) — as filed, unlike the name-inferred `category` |
| `source_file`, `source_url` | provenance for every record |

## Adding a state

Drop a file at `data/states/<ST>.json` (`{"records": [...]}`; an optional
`"filing_state"` in the payload wins, otherwise the filename decides),
then rebuild the store: `python3 build_db.py`. The build normalizes the
expansion adapters' schema to the API contract: `id` becomes
`charter_number`, `county_or_parish` becomes `region`/`county`, missing
`origin` defaults to `domestic`, missing `agents`/`address` are filled,
and a null `filing_date` stays null (date/age/year filters simply never
match it). Records gain `filing_state`, `region`, `category`, and
`needs` at build time, and every endpoint — search, `/v1/regions`,
`/v1/categories`, scope — covers the new state immediately. The server
itself is disk-backed (SQLite): it holds no records in RAM, starts
instantly, and serves all 8 built states (5.72M records) in ~30 MB of
memory, so free-tier hosting holds the full corpus with room to spare
(see `LAUNCH-STATUS.md`).

## Refreshing the data

The source publishes weekly (Tuesdays). Run:

```bash
python3 refresh.py --weeks 2
```

It finds the newest published files, downloads them politely (descriptive
User-Agent, delays, no crawling of the state's search site), re-parses, and
merges the result into `data/seed.json` (fresh parses win; the backfilled
history is kept — pass `--no-merge` to replace instead). Restart the server
to pick up new data. `backfill.py` rebuilds the multi-year history from the
published archive if the dataset ever needs to be reconstructed.

## Layout

```
app/server.py        the API (stdlib only)
app/classify.py      slug-interface adapter over the canonical classifier
packs/classify.py    THE canonical industry/need classifier (shared with
                     the $9 packs; documented in packs/CATEGORIES.md)
packs/               category pack folders + PACK-CATALOG.md
refresh.py           weekly data pipeline (fetch -> parse -> merge into seed.json)
backfill.py          one-time history builder (walks the weekly archive back)
build_db.py          converts the JSON corpus into data/filings.db (SQLite)
data/seed.json       the Louisiana dataset
data/states/         additional per-state datasets (<ST>.json)
data/filings.db      the built SQLite store the server serves (all states)
data/SOURCES.md      source documentation, licensing notes, derivations
api_keys.json        DEMO key store only (real keys via API_KEYS_JSON env var)
tests/run_tests.py   end-to-end battery against the real dataset
Dockerfile           dependency-free container image (ships packs/classify.py too)
render.yaml          Render blueprint (free-tier web service)
fly.toml             Fly.io app config
RAPIDAPI-LISTING.md  marketplace listing copy, pricing, publish steps
LAUNCH-STATUS.md     launch checklist — what's done, what remains, whose step
```

## Honest limitations

- History currently loaded: two years (126,710 filings, Nov 2024 → Nov
  2026). The published archive goes back further — the backfill stopped at
  its 104-week cap, not at the archive's start. The weekly refresh must
  keep running to stay current.
- Parish and entity type are derived fields — documented, not hidden
  (`data/SOURCES.md`). 87.4% of records carry a parish; Cameron Parish is
  the only one of the 64 with no mappable filings in the window.
- Industry is a keyword guess from the business name, and ~64% of filings
  honestly land in Other / Unclassified (generic names say nothing about
  industry). Need segments are likely-need inferences from business type.
  Both are labelled as such in `/v1/categories` and on every pack.
- Only Louisiana data is loaded today. The multi-state model, `state=`
  filtering, `/v1/regions`, and scope are built and tested; per-state
  files land in `data/states/` as the expansion adapters finish.
- The state's official Commercial API subscription ($500/yr) is the
  sanctioned channel for full-population/real-time data and the checkpoint
  to review before scaling this beyond the published weekly newsletters.
