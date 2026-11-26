# APILayer Listing Draft — New Business Filings API

Prepared Nov 25, 2026. Adapted from `../RAPIDAPI-LISTING.md`. Nothing here is
submitted yet — this is copy ready to paste on launch day, once the live base
URL from the deploy step exists (see `LAUNCH-RUNBOOK.md`, step d).

**Submission needs:** live base URL (runbook step b) · APILayer seller account
under seconddesks@proton.me · the endpoint list below.
**Not in the source material — confirm at submission, do not assume:**
APILayer's current seller revenue share and payout terms. The pricing below
is the gross listing price; whatever APILayer's cut is at submission time
comes out of it. (For comparison: RapidAPI takes 25%, API.market pays out
80% to the seller.)

---

## Title

New Business Filings API

## Short description

Every new business filing in 8 US states — 5.7M+ records searchable by state,
county/parish, city, industry, and likely need. Clean REST API, refreshed
weekly.

## Long description

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

**Total: 5,724,302 filings across 8 states** — every state, every city,
every filter. You get a simple REST API:

- Search filings by name, state, region (parish/county), city, entity type,
  industry category, likely-need segment, filing age (`age_days`), year, and
  date range — all filters compose
- Look up any filing by its state charter/entity number
- Get filing counts by region and by category for coverage checks and
  territory planning

**Who it's for:** lead generators and local marketers who sell to brand-new
businesses, lenders and insurers screening fresh entities, franchisors
watching a territory, registered-agent and formation services, driver
recruiters watching new trucking filings, and anyone who needs a state's
newest companies before they show up anywhere else.

**Data honesty:** records are reproduced from the public filings as
published, each with a source link. Filings data carries **no phone numbers
and no email addresses** — those do not exist in the source filings, and we
do not invent them. Industry is *inferred from the business name* (filings
carry no industry code) and need segments are *likely needs* inferred from
business type — both documented as such, never presented as verified.
Louisiana refreshes weekly, matching its source's cadence; the other states
are the registries as published by their states (see each state's date
range above).

## Endpoints

- `GET /v1/businesses` — search (params: q, state, parish, county, region,
  city, type, origin, category, need, age_days, year, from, to, limit,
  offset — all composing)
- `GET /v1/businesses/{charter_number}` — single-filing lookup (optional `state=`)
- `GET /v1/regions` — parishes for LA, counties elsewhere (optional `state=`)
- `GET /v1/parishes` — Louisiana view of `/v1/regions`
- `GET /v1/categories` — industries + likely-need segments with live counts (optional `state=`)
- `GET /health` — dataset status and freshness (public)

Auth: API key header, per APILayer's gateway model. Geographic scope is
enforced per key server-side (see pricing): a City key only ever returns its
bound city, a State key only its bound state — including in counts, and
single-record lookups outside scope return 404.

## Category / tags

Category: Business / Data
Tags: new business leads, business filings, secretary of state, company
registry, lead generation, new business data, llc filings, business
registration, local marketing, territory planning

## Pricing

Scope-based pricing: buyers pay for the geography they actually use — one
city, or a full state — at heavy-use volumes.

| | Free | City Heavy — $29/mo | Full State Heavy — $59/mo |
|---|---|---|---|
| Scope | sample queries | ONE city (buyer's choice, bound to the key) | ONE full state (bound to the key) |
| Requests | 50/day, hard cap | 5,000/day | 10,000/day |
| Overage | — | $0.005/request | $0.005/request |
| All filters (city / region / category / need / age / date range) | ✔ (within scope) | ✔ | ✔ |
| Support | — | email | email, priority |

A Full State key is bought per state (any of the 8 covered states).

## Notes for the person submitting

- Paste the live base URL from runbook step b; test one search, one lookup,
  one regions call, and one over-quota call in APILayer's console before
  publishing.
- If APILayer demands tax/identity verification at seller onboarding, that
  step is Jeremy's (see runbook step d).
