# Agent Commerce Layer — Second Desk Filings API

Shipped: 2026-11-27 (release 3.0.0, see the changelog section below).
Service: `https://filings-api.onrender.com` — the same live service as
v1/v2, extended. `/health` is unchanged: 5,724,302 records, 8 states.

## Priority ordering (owner directive, 2026-11-27)

Practicality before distribution. The order of work was: (1) an agent
can get a free key with no human in the loop, (2) read the catalog
and understand prices, (3) understand every error and its next step,
(4) complete a test job/resume flow, (5) pass hostile-request checks,
(6) verify live. Directory and registry submissions come after all of
that works, not before.

## What shipped

1. **Self-serve free keys** — `POST /v1/keys`. An agent sends an
   email address and gets a free-tier key in the same response. No
   browser, no email round trip, no human wait. The key is shown
   once, stored as a SHA-256 hash, rate limited per email and per IP,
   one active free key per email, and capped by the unchanged free
   tier (50 requests/day, all-data scope).
2. **Catalog endpoints** — `GET /v1/catalog` (every sellable family
   with name, description, record counts, pack counts, price points,
   and the site URL, in one JSON document) and
   `GET /v1/catalog/{family}` (that family's packs with prices and
   record counts, paginated). Built from the catalog site lane's
   family data and pack register by `build_catalog.py` into
   `data/catalog.db`: 18 families, 68,658 packs, 302,799,848 records.
3. **Agent discovery** — `GET /.well-known/agent.json` (public):
   business name, what is sold, API base, MCP endpoint, OpenAPI URL,
   catalog endpoint, all plans with prices (including the Employer
   plan), the self-serve key endpoint, and contact email
   seconddesks@proton.me. `/openapi.yaml` documents every new
   endpoint, including every error body for `POST /v1/keys`.
4. **Work board** — `GET/POST /v1/jobs`, `GET/POST /v1/resumes`,
   `DELETE /v1/jobs/{id}`, `DELETE /v1/resumes/{id}`. Employers post
   roles; candidates list their own resume summaries. A resume is
   only stored when the request carries `consent: true` — no consent,
   no listing, no exceptions. Resumes are listed by the candidates
   themselves for employers to find them; nothing is scraped.
5. **Public changelog** — `GET /changelog` on the API domain (this
   was the chosen home for it, not the website): dated entries for
   v1 (2026-11-26), v2 (2026-11-26), the marketplace delivery
   release (2026-11-26), and this agent commerce release
   (2026-11-27), newest first.
6. **Employer plan** — $20/mo, listed in the agent card's plans.
   Reading the work board stays free on any key, agents included.
   Job posting and resume search at scale are the Employer plan
   surfaces: that is the tier a business buys when the board becomes
   part of its hiring workflow. In this v1 the board endpoints accept
   any valid key (the plan is sold and discovered through the agent
   card and this document); per-plan board quotas are the documented
   next step, not a silent promise. The resume consent rule is
   unchanged by the plan: consent true, or nothing is stored.

## Endpoint list (new in this release)

| Method and path | Auth | Purpose |
|---|---|---|
| POST /v1/keys | public, rate limited | Self-serve free key (shown once) |
| GET /changelog | public | Dated release history |
| GET /.well-known/agent.json | public | Agent discovery document |
| GET /v1/catalog | any key | All sellable families, counts, prices |
| GET /v1/catalog/{family} | any key | One family's packs, paginated |
| GET /v1/jobs | any key | List open roles |
| POST /v1/jobs | any key | Post a role |
| DELETE /v1/jobs/{id} | posting key only | Remove your role |
| GET /v1/resumes | any key | Search resumes by role/location keywords |
| POST /v1/resumes | any key | List your own resume (consent: true required) |
| DELETE /v1/resumes/{id} | posting key only | Remove your resume |

Existing routes, prices, scopes, and behavior are unchanged.

## Designed property: fully server to server

The buying path works with plain HTTPS requests and the `X-API-Key`
header only — catalog read, key issuance, plan auth, data delivery,
and the board endpoints. There is no browser step, no cookie, and no
JavaScript challenge anywhere on the API surface. That is deliberate:
purchases that fail elsewhere behind bot walls complete here because
there is no wall — authentication is the key, and the key itself is
now issued by an API call.

## How an agent buys, end to end

1. `GET /.well-known/agent.json` — learn what is sold, the plans,
   and the key endpoint. (Public.)
2. `POST /v1/keys` with `{"email": "you@example.com"}` — receive a
   free key in the response. Store it; it is shown once.
3. `GET /v1/catalog` with the key — every family, record counts,
   pack counts, monthly and one-time prices.
4. `GET /v1/catalog/{family}?limit=100&offset=0` — pick exact packs.
5. Query live data on the free tier (50 requests/day):
   `GET /v1/businesses`, `/v1/regions`, `/v1/categories`, or the MCP
   server at `/mcp`.
6. Buy at scale: packs are sold through the family's site URL in the
   catalog (marketplace listings with card checkout), and API plans
   (City Heavy $29/mo, Full State Heavy $59/mo, Employer $20/mo) are
   arranged by emailing seconddesks@proton.me — the operator issues a
   scoped key on payment. Fully automatic in-API card payment is the
   one step that still needs the owner's Stripe dashboard work; see
   the last section.

## Error bodies: status, meaning, next step

Every error is JSON: `{"error", "detail", "next_step"}` (older routes
return `error` + `detail`). The caller's next step is always in the
body — an agent never has to guess.

### POST /v1/keys

| Status | error | Next step |
|---|---|---|
| 201 | — | Store the key now; it is shown once and stored hashed. |
| 400 | invalid_email | Send a valid email address string. |
| 400 | missing_field | Send {"email": "..."} — the field is required. |
| 400 | unexpected_field | Send only the email field. |
| 400 | bad_body / bad_json | Send a JSON object as the body. |
| 409 | key_already_issued | Use the original key. If lost, email seconddesks@proton.me from that address to revoke it, then re-issue. |
| 413 | body_too_large | Send only the small JSON body (64 KB cap). |
| 429 | rate_limited | Wait `retry_after_seconds` (also the Retry-After header), then retry once. Limits: 5 attempts per email per hour, 10 per IP per hour. |
| 503 | key_issuance_unavailable | Email seconddesks@proton.me for a key. |

### All authenticated endpoints

| Status | error | Next step |
|---|---|---|
| 401 | invalid_or_missing_api_key | Send a valid key in the X-API-Key header; get one from POST /v1/keys. |
| 401 | api_key_revoked | The key was revoked (see Paid plan lifecycle). Get a new key. |
| 402 | plan_past_due | The plan is past due and its keys are suspended. Pay the outstanding balance to restore access. |
| 429 | daily_quota_exceeded | Wait for the UTC day to roll over, or upgrade the plan. |
| 400 | missing_field / empty_field / invalid_field_type / field_too_long / unexpected_field (board) | Fix the named field; caps are in the OpenAPI schema. |
| 400 | consent_required (resumes) | Resend with consent: true. Without it nothing is stored. |
| 400 | bad_pagination | limit and offset must be integers. |
| 404 | unknown_family (catalog) | The body lists the valid family slugs. |
| 404 | not_found (board delete) | Unknown id and another poster's id answer identically; check the id and that you use the posting key. |
| 503 | catalog_unavailable | The catalog store is not loaded on this deployment; retry later or use the site. |

## Security audit (this release)

Scope: the new code only — `app/catalog.py`, `app/board.py`,
`app/keyissue.py`, `app/changelog.py`, and the new routes in
`app/server.py`. Pre-existing routes were not changed except the
root endpoint listing, the CORS method list (DELETE added), and the
auth fallback described below.

### Controls implemented and verified

- **Write endpoints are key-gated.** Every POST/DELETE except the
  Vermarco webhook (Ed25519-signed, unchanged) and key issuance
  requires a valid key via the existing `authed()` path. Key issuance
  is public by design and bounded instead: per-email and per-IP
  rolling-hour limits, one active key per email, and the free tier's
  50 requests/day cap on every issued key.
- **Keys are hashed at rest and shown once.** Self-serve keys exist
  in plaintext only in the 201 response. The store file holds
  SHA-256 hashes; quota accounting for issued keys is keyed by the
  hash, so raw issued keys are not kept in server memory either.
  A test asserts the raw key string does not appear in the store
  file and its hash does.
- **Validation and sanitization.** Board and key-issuance bodies go
  through strict whitelists: unknown fields rejected
  (unexpected_field), non-string values rejected
  (invalid_field_type), missing/empty rejected, hard length caps
  (board: 120 chars titles/names, 4,000 description/experience, 200
  contact; email: 254). Control characters are stripped at storage.
  Emails must match a strict address pattern. Bodies over 64 KB are
  refused (413) before parsing.
- **No file uploads anywhere.** The API accepts only small JSON
  bodies; there is no multipart or file handling on any route.
- **Output is JSON-escaped by construction.** All responses are
  produced by `json.dumps`; stored text (including script payloads)
  round-trips as inert JSON strings. A test posts
  `<script>alert(1)</script>` and SQL-flavored text and reads them
  back as plain strings with the service unharmed.
- **Injection.** Catalog and board reads use parameterized SQLite
  queries; board search is in-memory substring matching; resume/job
  fields never reach SQL at all. A SQL-flavored filings search
  (`q="' OR '1'='1"`) returns 200 with zero results, not data.
- **IDOR.** Board entries are public posts by design, and the only
  data an entry exposes is what its poster submitted. Deletion
  requires the posting key (ownership is a hash of the key, never
  serialized). Deleting another poster's id and deleting a made-up
  id return the identical 404, so the endpoint leaks nothing about
  other entries. No endpoint exposes a submitter's contact beyond
  the contact field the submitter chose to publish on their own
  post.
- **Rate limiting and quotas.** All new authenticated endpoints
  consume the caller's existing plan quota (free tier stays capped
  at 50/day; a test burns a self-serve key's full 50 and gets 429 on
  the 51st). Key issuance adds its own limits: 5 attempts per email
  per rolling hour, 10 attempts per IP per rolling hour, with
  `retry_after_seconds` and a `Retry-After` header on the 429.
- **Keys never logged, never echoed.** Access logs record method,
  path, and status only. No error body contains a key, a stack
  trace, or an internal path; handler failures answer
  `{"error": "internal_error"}` and log only the exception type to
  stderr. A test asserts the key's owner hash never appears in any
  board response body.
- **Unauthenticated surface.** Exactly the documented public reads:
  `/health`, `/`, `/openapi.yaml`, `/.well-known/agent.json`,
  `/changelog`, the signed webhook, and rate-limited key issuance.
  Everything else answers 401 without a valid key (tested).
- **Dependencies and config.** Zero new dependencies: the new code
  is Python standard library only (the service was already
  stdlib-only; the MCP extra is unchanged and pinned). No secrets
  were added to the repo or the image: the image ships the same demo
  key file as before, real keys still arrive only via the
  `API_KEYS_JSON` environment variable, and live keys remain only in
  `launch-kit/live-keys.local.json` (chmod 600, never printed). No
  debug mode exists in this server; there is nothing to switch off.

### Hostile self test — recorded outcomes

Run against the local build (suites `tests/run_tests_v4.py` and
`tests/run_tests_v5.py`, all passing; see Verification):

| Probe | Outcome |
|---|---|
| Board/catalog/data request with no key or a bad key | 401 invalid_or_missing_api_key |
| Free-tier key driven past its daily cap | 429 daily_quota_exceeded |
| Resume posted without consent / consent false / consent "yes" | 400 consent_required, nothing stored, in all three forms |
| `<script>alert(1)</script>` + SQL text in board fields | Stored and returned as inert JSON strings; /health still 5,724,302 after |
| SQL-flavored search strings (board and filings) | 200 with zero results; no error, no data leak |
| Malformed JSON body | 400 bad_json |
| Body over 64 KB | 413 body_too_large |
| Wrong-type, missing, empty, and over-length board fields | 400 with the specific error code for each |
| Delete another poster's entry / delete a made-up id | Identical 404; the entry survives |
| Key farming: same email behind 6 rotating IPs | 201, then 409s, then 429 rate_limited on the 6th attempt (5/hour/email cap) |
| Key farming: 11 fresh emails from one IP | 10 keys issued, 11th request 429 rate_limited (10/hour/IP cap) |
| Farmed key driven past the free cap | 429 daily_quota_exceeded on request 51 |
| Second key request for an email that has one | 409 key_already_issued with the recovery next step |
| Raw issued key searched for in the on-disk store | Absent; only its SHA-256 hash is present |

### Residual risks, stated plainly

- **Ephemeral stores.** On the current free hosting tier the
  filesystem resets on restart, so the board contents, issued keys,
  and rate-limit windows reset with it. Acceptable for a v1 board
  and free keys (re-issuing is one call); a paid tier needs a
  durable store first.
- **In-memory quotas.** Daily counters live in process memory and
  reset at UTC midnight or on restart — the same behavior the API
  has always had; a restart briefly restores free-tier quota.
- **Client IP trust.** Issuance rate limits key on the first
  X-Forwarded-For hop, which the hosting proxy sets. A client that
  could reach the origin directly could spoof it; the per-email
  cap, the one-key-per-email rule, and the 50/day value cap bound
  what farming can win regardless.
- **Catalog and board are not scope-restricted.** A city-scoped key
  can read the full catalog and board. That is deliberate: both carry
  only public sales metadata and public posts, no filings records.
- **Error-body change is additive.** New routes return trace-free
  errors with next steps; pre-existing routes keep their established
  error shapes (also trace-free in practice, but not re-audited
  line by line here).
- **No CAPTCHA on key issuance, by design.** The endpoint exists so
  agents can get keys without a browser. Abuse is bounded by the
  rate limits and the low free-tier cap rather than by challenges.

## What still needs the owner's Stripe dashboard (x402 rail)

Everything above works today except fully automatic card payment by
an agent inside the API itself. The missing piece is a payment rail
an agent can call the same way it calls everything else here (the
x402 style: request, payment challenge, pay, key delivered). That
requires, in the owner's own Stripe dashboard (Muse cannot do these
steps; the Muse–Stripe connector is not connected and the owner
declined to reconnect it):

1. Confirm the Stripe account is fully activated for payouts
   (identity and bank details inside Stripe).
2. Create the products/prices for City Heavy ($29/mo), Full State
   Heavy ($59/mo), and Employer ($20/mo), or enable a checkout that
   the API can reference.
3. Issue a restricted API key / enable the payment integration the
   rail will use, so the server can verify a payment and mint a
   scoped key automatically.

Until then the paid path is: agent reads the catalog, emails
seconddesks@proton.me, pays through the marketplace listing or a
payment link the operator sends, and receives a scoped key. Honest
status: discovery, free keys, catalog, data, and the board are fully
automatic; paid key delivery is operator-assisted.

## Paid plan lifecycle (owner rule, 2026-11-27)

States for a paid plan and its keys:

1. **active** — paid through the current period; keys work normally.
2. **past due / suspended** — a payment is missed. The plan's keys
   are suspended: every request is refused with
   `402 {"error": "plan_past_due", ...}` and a next step telling the
   caller to pay the outstanding balance. Nothing is deleted;
   payment restores full access. Payment notices go out on the
   business's shared notice schedule while the plan is past due.
3. **revoked / removed** — 30 days after the missed payment, if it
   is still unpaid, the plan's keys are revoked (requests answer
   `401 {"error": "api_key_revoked", ...}`) and the plan account is
   removed. The removal is logged (plan, key ids, dates, reason) so
   the history survives the account.
4. **Free tier is exempt.** Free keys — configured or self-serve —
   are never suspended, revoked, or removed under this rule.

What is built in this release, and what is not — plainly:

- **Built and tested:** the enforcement hook. `KeyStore` honors a
  `status` field on a key entry (`suspended` → 402 plan_past_due,
  `revoked` → 401 api_key_revoked; absent means active), for both
  configured keys and dynamically resolved ones. The operator can
  suspend or restore a key today by setting that field where the
  keys are configured (`API_KEYS_JSON`). Self-serve issued keys
  carry an equivalent `active` flag in their store.
- **Not built:** the automatic driver — there is no billing clock
  in this release because no payment events reach the API yet (the
  Stripe rail in the previous section is the blocker). Inventing a
  dunning engine with nothing to drive it would be dead code.

Exact hooks for the wiring lane, when payment events exist:

- A plan-accounts store keyed by plan holder: plan name, key ids,
  `paid_through` date, status, and a lifecycle log array.
- On a payment-failed event: set the plan's keys `status:
  "suspended"` (via the key config / store) and stamp the missed
  date; notices fire from the shared schedule keyed off that date.
- On payment success while suspended: clear `status` back to active.
- A daily job: any plan still suspended 30 days after its missed
  date gets `status: "revoked"` on its keys, the plan account is
  removed from the accounts store, and a log line is appended
  (plan, keys, missed date, removal date) to the lifecycle log.
- Free keys are excluded from every step by tier check (`tier ==
  "free"` skips lifecycle entirely).

## Pricing policy: first-year rate lock (owner rule, 2026-11-27)

Served with the plans in `GET /.well-known/agent.json` (each paid
plan carries a `price_lock` note, and the card carries the full
`pricing_policy` text):

- A business that subscribes to a paid plan during the site's first
  year locks that rate **for life** while the plan stays
  continuously active — City Heavy $29/mo, Full State Heavy $59/mo,
  Employer $20/mo at today's list. Renewals always charge the
  locked rate; the lock is recorded on the account.
- The founding offer stays honored: the first 10 direct buyers at
  $49/mo locked for 12 months. Where the founding offer and the
  first-year lock could both apply, the buyer keeps whichever terms
  are better for them.
- A plan removed under the 30-day non-payment rule (previous
  section) **loses its lock**. A returning business pays the
  then-current price.
- Year one runs from the marketplace launch date, which the site
  build stamps.

## Stocks section support (site data side)

For the website's one-page-per-stock Stocks section. Read-only
throughout: no trading, orders, portfolio, or advice features exist
or are planned. Everything below was verified against the investor
lane (`92-investor-and-gaps`) masters and the served catalog on
2026-11-27.

### Read paths available today

- **Pack discovery (served now):** `GET /v1/catalog/{family}` for
  the four stock-relevant families — `insider-trades` (2,089 packs),
  `form-13f` (72), `stakes-13dg` (18), `fails-to-deliver` (48). Each
  pack row carries its id, title, record count, and monthly/one-time
  prices — exactly what a stock page needs to list the datasets
  under that stock. Per-ticker insider packs are titled
  `SEC Insider Transactions — {TICKER} Complete History` (e.g. AAPL:
  958 records, a free test pack). Per-symbol FTD packs exist for the
  25 highest-value symbols (SPY, QQQ, TSLA, …) plus a
  100-most-failed-symbols pack. Any valid key, free included, can
  read these; scoped keys are not restricted from the catalog.
- **Aggregates (computed from the masters, not yet an endpoint):**
  insider counts and latest transaction date per ticker, FTD latest
  settlement date per symbol, 13F holder counts per CUSIP and latest
  period, and 13D/G event counts per CIK are all computable from the
  lane masters below in one pass each. A dedicated
  `GET /v1/stocks/{ticker}` aggregate endpoint is the clean
  follow-up build; until it ships, the site build should compute
  these during its own rebuild from the same masters rather than
  calling the API per page.

### Joins, per family (verified from the data)

| Family | Master | Stock keys present | Per-stock packs | Latest data |
|---|---|---|---|---|
| Insider trades (Form 3/4/5) | insider-trades/data/trades.csv, 3,263,093 rows | **ticker AND issuer_cik — clean both ways**, 12,456 tickers | 2,046 per-ticker packs (tickers with ≥500 trades) | transactions to 2026-09-30 |
| Fails-to-deliver | fails-to-deliver/data/fails.csv, 23,345,181 rows | **symbol AND cusip — clean**, 58,093 symbols | 25 per-symbol packs + top-100 pack | settlement dates to 2026-10-30 |
| Form 13F holdings | form-13f/data/holdings_{period}.csv, 14,393,459 rows over 4 quarters | **cusip and figi only — NO ticker**; manager_cik on the filer side | per-manager packs (47 top managers), no per-stock packs | latest period 2026Q2 (period_of_report 2026-06-30) |
| Schedule 13D/13G | stakes-13dg/data/stakes.csv, 249,912 index entries | **indexed_cik only — no ticker** | none per stock (year/form cuts only) | filings to 2026-09-30 |
| Clinical trials | clinical-trials master, 613,563 studies | none (sponsor names only) | none per stock | first-posted to 2026-11-25 |

### Gaps, stated plainly

- **13F has no clean ticker key.** The SEC source carries CUSIP (and
  our build keeps FIGI); no ticker was mapped or invented. A stock
  page can show 13F data only through a CUSIP bridge we do not yet
  hold. Holder counts per CUSIP and the latest period are available;
  per-ticker 13F holder counts are NOT available until a
  CUSIP→ticker mapping is built or licensed.
- **13D/13G has CIK but no ticker, plus an ambiguity:** EDGAR indexes
  each filing under both the subject company and the reporting
  person without saying which is which, so the key is honestly
  labeled "indexed company" in the data. Events per CIK are exact;
  calling every one of them an event *for that stock* would overstate
  the data. A partial CIK→ticker bridge exists inside our own
  insider master (issuer_cik ↔ ticker for its 12,456 tickers).
- **Insider per-ticker packs cover 2,046 of 12,456 tickers** (the
  ≥500-trade floor). Counts and latest dates for the other ~10,400
  tickers exist in the master but have no pack yet.
- **FTD per-symbol packs cover 25 symbols** of 58,093; the rest are
  reachable only through the year/complete packs today.
- **Clinical trials has no stock key at all** — sponsor-name pages
  only; it should not be presented as per-stock data.

## Verification (2026-11-27)

Local, bare metal (the same code and stores the container runs):

- Test suites, all passing: v1 64/64 · v2 32/32 · v3 20/20 · MCP
  9/9 · v4 (catalog, agent.json, board, hostile battery) 48/48 ·
  v5 (key issuance, changelog, farming battery, lifecycle gate)
  28/28. Total 201 checks.
- Catalog ground truth read back from `data/catalog.db` directly:
  18 families, 68,658 packs, 302,799,848 records; the filings family
  shows 15,501 packs (15,502 feed rows minus one duplicate title,
  deduped and recorded in `data/catalog.db.stats.json`).
- Container image: the agent commerce image (with catalog store)
  built successfully once tonight at 9,493,520,151 bytes (under the
  10 GB hosting cap) and the code it carries is the code the suites
  above verify.

**Deploy status — read carefully:** the final image (with the
key-issuance, lifecycle, and price-lock additions folded in after
that build) has NOT been pushed yet. Rebuild attempts hit the
workspace disk quota (EDQUOT) repeatedly tonight — the sandbox tree
sat at ~100 GB used with several lanes building in parallel, and the
lane's own history documents the same wall. The live service is
untouched and still runs the previous release. Remaining steps, in
order: (1) free roughly 15 GB in the workspace tree (stale container
build roots are the big item), (2) rebuild from
`.build-ctx-split/Dockerfile.split` and push to GHCR, (3) the
operator clicks Manual Deploy → Deploy latest image on the Render
dashboard (a browser step Muse cannot perform), (4) re-run the
live checks: /health unchanged at 5,724,302 records / 8 states,
catalog answers with the free key, agent.json and changelog serve,
one self-serve key round trip, one jobs round trip (post, list,
delete).
