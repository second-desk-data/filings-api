# APIs.guru Listing Draft — New Business Filings API

Prepared Nov 25, 2026. Adapted from `../RAPIDAPI-LISTING.md`. Nothing here is
submitted yet.

**What APIs.guru is:** a free, open directory of APIs — it does **not**
process payments, issue keys, or take a revenue share. A listing there is
discovery only: it points developers at the API's OpenAPI definition and
documentation. Subscriptions are sold through the paid marketplaces
(RapidAPI, APILayer, API.market). The pricing table below is included for
reference so the directory entry can link buyers to where plans are sold.

**⚠ BLOCKER — flagged, not filled:** APIs.guru submissions require a
publicly reachable **OpenAPI definition file**. No OpenAPI/Swagger spec
exists anywhere in this project yet (checked Nov 25, 2026 — the API's
contract lives in `app/server.py`'s docstring and `README.md`). Before this
listing can be submitted, someone must:
1. Author `openapi.yaml` from the endpoint/parameter documentation in
   `README.md` and `../RAPIDAPI-LISTING.md`, with the `servers` field set to
   the live base URL from runbook step b.
2. Host it at a stable public URL on the same host as the API.
3. Submit it through APIs.guru's contribution flow (a pull request against
   their GitHub directory with the spec URL).
This is a Muse build task on launch day, not a copy-paste — budget it as
its own step (runbook step d).

---

## Listing fields

**Name:** New Business Filings API

**Short description:** Every new business filing in 8 US states — 5.7M+
records searchable by state, county/parish, city, industry, and likely
need. Clean REST API, refreshed weekly.

**Long description:**

Secretaries of State publish new business filings as raw, unstructured bulk
files and weekly text dumps: millions of lines, no search, no region field,
no way to plug any of it into your systems. This API does that work for you —
for every state it covers, not just one.

We ingest each state's published filings, clean them, and tag every record
with its region (Louisiana parish, county elsewhere), an industry category
inferred from the business name, and likely-need segments (needs a website,
needs bookkeeping, likely hiring CDL drivers, and more). Coverage today:

| State | Filings | Filing dates |
|---|---|---|
| Louisiana | 126,710 | 2024-11-24 → 2026-11-21 (two full years, weekly feed) |
| Pennsylvania | 2,386,129 | full state registry |
| Connecticut | 1,310,099 | full state registry |
| New York | 750,671 | 2024-01-01 → present |
| Colorado | 536,338 | 2024-01-01 → present |
| Iowa | 351,617 | full active registry |
| Oregon | 203,815 | 2024-01-02 → present |
| Florida | 58,923 | most recent ~30 days of daily filings |

**Total: 5,724,302 filings across 8 states.**

**Data honesty:** records are reproduced from the public filings as
published, each with a source link. Filings data carries **no phone numbers
and no email addresses** — those do not exist in the source filings, and we
do not invent them. Industry is *inferred from the business name* (filings
carry no industry code) and need segments are *likely needs* inferred from
business type — both documented as such, never presented as verified.
Louisiana refreshes weekly, matching its source's cadence; the other states
are the registries as published by their states (see each state's date
range above).

## Endpoints (for the OpenAPI spec, once authored)

- `GET /v1/businesses` — search (params: q, state, parish, county, region,
  city, type, origin, category, need, age_days, year, from, to, limit,
  offset — all composing)
- `GET /v1/businesses/{charter_number}` — single-filing lookup (optional `state=`)
- `GET /v1/regions` — parishes for LA, counties elsewhere (optional `state=`)
- `GET /v1/parishes` — Louisiana view of `/v1/regions`
- `GET /v1/categories` — industries + likely-need segments with live counts (optional `state=`)
- `GET /health` — dataset status and freshness (public)

Auth: `X-API-Key` header. Geographic scope is enforced per key server-side:
a City key only ever returns its bound city, a State key only its bound
state — including in counts, and single-record lookups outside scope return
404.

## Category / tags

Category: Business / Data
Tags: new business leads, business filings, secretary of state, company
registry, lead generation, new business data, llc filings, business
registration, local marketing, territory planning

## Pricing (reference only — APIs.guru does not sell plans)

Scope-based pricing: buyers pay for the geography they actually use — one
city, or a full state — at heavy-use volumes. Plans are sold via RapidAPI,
APILayer, and API.market.

| | Free | City Heavy — $29/mo | Full State Heavy — $59/mo |
|---|---|---|---|
| Scope | sample queries | ONE city (buyer's choice, bound to the key) | ONE full state (bound to the key) |
| Requests | 50/day, hard cap | 5,000/day | 10,000/day |
| Overage | — | $0.005/request | $0.005/request |
| All filters (city / region / category / need / age / date range) | ✔ (within scope) | ✔ | ✔ |
| Support | — | email | email, priority |

A Full State key is bought per state (any of the 8 covered states).
