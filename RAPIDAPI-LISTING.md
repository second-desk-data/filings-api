# RapidAPI Listing — New Business Filings API (Multi-State)

## Listing copy

**Name:** New Business Filings API

**Tagline:** Every new business filing in 8 states — searchable by state, county/parish, city, industry, and likely need. Refreshed weekly.

**Category:** Business / Data

**Description (long):**
Secretaries of State publish new business filings as raw, unstructured
bulk files and weekly text dumps: millions of lines, no search, no
region field, no way to plug any of it into your systems. This API does
that work for you — for every state it covers, not just one.

We ingest each state's published filings, clean them, and tag every
record with its region (Louisiana parish, county elsewhere), an industry
category inferred from the business name, and likely-need segments
(needs a website, needs bookkeeping, likely hiring CDL drivers, and
more). Every record also carries `agent_is_commercial` — a flag for
filings whose registered agent is a commercial formation mill
(Registered Agents Inc, Northwest, CSC, LegalZoom, and peers), so you
can filter the factory noise out of your lead lists. Connecticut records
additionally carry the filer's own published business email
(`email_as_filed`, 62% of CT records) and the NAICS code the filer
reported (`naics_code` / `naics_title`, 48%) — real filed data, not
inference. Coverage today:

| State | Filings | Filing dates |
|---|---|---|
| Louisiana | 126,710 | 2024-11-24 → 2026-11-21 (two full years, weekly feed) |
| Pennsylvania | 2,386,129 | full state registry (filings back to 1753, current through 2026-10-31) |
| Connecticut | 1,310,099 | full state registry (1900 → 2026-11-24) |
| New York | 750,671 | 2024-01-01 → 2026-11-24 |
| Colorado | 536,338 | 2024-01-01 → 2026-11-21 |
| Iowa | 351,617 | full active registry |
| Oregon | 203,815 | 2024-01-02 → 2026-11-23 |
| Florida | 58,923 | most recent ~30 days of daily filings |

**Total: 5,724,302 filings across 8 states** — every state, every city,
every filter. You get a simple REST API:

- Search filings by name, state, region (parish/county), city, entity
  type, industry category, likely-need segment, filing age (`age_days`),
  year, and date range — all filters compose
- Look up any filing by its state charter/entity number
- Get filing counts by region and by category for coverage checks and
  territory planning

**Who it's for:** lead generators and local marketers who sell to
brand-new businesses, lenders and insurers screening fresh entities,
franchisors watching a territory, registered-agent and formation
services, driver recruiters watching new trucking filings, and anyone
who needs a state's newest companies before they show up anywhere else.

**Data honesty:** records are reproduced from the public filings as
published, each with a source link. Industry is *inferred from the
business name* (filings carry no industry code) and need segments are
*likely needs* inferred from business type — both documented as such,
never presented as verified. Louisiana refreshes weekly, matching its
source's cadence; the other states are the registries as published by
their states (see each state's date range above).

**Endpoints to define in the marketplace:**
- `GET /v1/businesses` (params: q, state, parish, county, region, city, type, origin, category, need, age_days, year, from, to, limit, offset — all composing)
- `GET /v1/businesses/{charter_number}` (optional `state=`)
- `GET /v1/regions` (parishes for LA, counties elsewhere; optional `state=`)
- `GET /v1/parishes` (Louisiana view)
- `GET /v1/categories` (industries + likely-need segments with live counts; optional `state=`)
- `GET /health`

## Pricing tiers

Scope-based pricing set by Jeremy (Nov 25, 2026): buyers pay for the
geography they actually use — one city, or a full state — at heavy-use
volumes. Repriced Nov 25, 21:30 at his direction ("match them, not too
high"): matched to the low end of the new-filings comp range —
FirstFiling Leads charges $29/mo per county and $59/mo per state, and
full-state all-industry services run $99/mo — so **City Heavy $29/mo,
Full State Heavy $59/mo.** RapidAPI takes a 25% marketplace fee, which
is priced in. (Supersedes the earlier $15/$25 setting and the
Free / Basic $19 / Pro $99 structure — the $99 tier is gone.)

| | Free | City Heavy — $29/mo | Full State Heavy — $59/mo |
|---|---|---|---|
| Scope | sample queries | ONE city (buyer's choice, bound to the key) | ONE full state (bound to the key) |
| Requests | 50/day, hard cap | 5,000/day | 10,000/day |
| Overage | — | $0.005/request | $0.005/request |
| All filters (city / region / category / need / age / date range) | ✔ (within scope) | ✔ | ✔ |
| Support | — | email | email, priority |

Scope is enforced per API key: a City key only returns records for its
bound city; a State key only for its bound state. The local key store in
`api_keys.json` gains a `scope` field at deploy so the demo enforces
exactly what subscribers get. A Full State key is bought per state (any
of the 8 covered states); a multi-state/all-states bundle can be added
later as an upsell.

## Publish steps

1. **Host the API** — a deploy package is ready in this folder: `Dockerfile`,
   `render.yaml` (Render free-tier web service), and `fly.toml` (Fly.io).
   The server is disk-backed (SQLite store built by `build_db.py`, shipped
   in the image — see the Dockerfile header) and reads `PORT`/`HOST` from
   the environment and real keys from the `API_KEYS_JSON` env var (the
   bundled `api_keys.json` is DEMO keys only). Run
   `python3 refresh.py --weeks 2` weekly (after Tuesday; it merges into
   the backfilled history), re-run `python3 build_db.py`, and restart the
   service to pick up fresh data. See `LAUNCH-STATUS.md` for the ordered
   checklist.
2. **Create a RapidAPI provider account** (rapidapi.com → "My APIs" →
   "Add New API"), name it as above, and paste in the listing copy.
3. **Define the endpoints** with their parameters and the example
   responses from `README.md`; set the base URL to the hosted deployment.
4. **Auth:** choose RapidAPI's key model — the marketplace injects
   `X-RapidAPI-Key` per subscriber. Either validate that header at the proxy
   or map it to this API's own `X-API-Key` tiers; keep the local key store
   for direct (non-marketplace) sales later.
5. **Set pricing** to the tiers above (Free / City Heavy $29 / Full State
   Heavy $59, overages as listed). Add the "Freemium" plan type so the free
   tier is capped, not unlimited.
6. **Test in the marketplace console** (RapidAPI's endpoint tester) with a
   real subscriber key: one search, one lookup, one parishes call, one
   over-quota call.
7. **Publish**, then announce where the buyers are (the pattern that worked
   for comparable solo APIs: a Show HN / Indie Hackers post showing the
   messy source data next to the clean API response).
8. **Escape hatch later:** once a listing has traction, offer direct Stripe
   billing (same tiers, no 25% marketplace cut) — the local key store
   already supports issuing keys directly.

## Design note — adding the x402 rail later (candidate #3)

The same endpoints can later be sold pay-per-call to AI agents via the x402
protocol (HTTP 402 → USDC micropayment → response), with no marketplace and
no API keys: an agent discovers the endpoint in a catalog, pays cents per
call, and gets data. Design hooks already in place: the API is stateless,
every record carries provenance (`source_url`), and quota logic is isolated
in one `KeyStore.check()` call — an x402 payment check would sit in exactly
that spot as an alternative to a key check. **No crypto setup is part of
this build** — this is a note only. If added, Jeremy's one-time steps would
be creating a wallet to receive USDC and deciding per-call prices
(suggested: $0.01/search call, $0.005/lookup, mirroring the overage rates).
Expected near-term revenue from this rail is negligible (the buyer pool is
still mostly experiments); its value is positional — being listed early in
the catalogs agents browse.
