# Launch Runbook — New Business Filings API

Prepared Nov 25, 2026. The exact launch sequence once hosting lands. Every
step lists what it needs, who does it, and how completion is verified.
Nothing in this runbook is executed yet — it exists so launch day is
same-day execution, not improvisation.

**Owners:** "Muse" = Muse does it end-to-end. "Jeremy" = only Jeremy can
do it (his credentials, his financial/tax details). Jeremy's total launch-day
load is deliberately tiny: at most one password card and, only if a
marketplace demands it, a tax/payout form.

**The critical path in one line:** the **live base URL** produced by step
(b) gates steps (c), (d), (e), (f), (g), and (h) — six of the seven
remaining steps. Step (b) is itself gated by exactly two things: the step
(a) verification, and the Render account existing. If launch day stalls, it
stalls there — everything else is copy-paste from this kit.

```
(a) verify rebuild ──► (b) deploy ──► live base URL ──┬─► (c) RapidAPI ──► (g) announce
                                                      ├─► (d) APILayer / API.market / APIs.guru
                                                      ├─► (e) Postman workspace
                                                      ├─► (f) Kaggle / data.world / GitHub
                                                      └─► (h) carriers page + job-board link
```

---

## Step (a) — Verify the multi-state SQLite rebuild

**Needs:** `build_db.py` output already on disk: `data/filings.db` +
`data/filings.db.stats.json`. No accounts, no network.
**Owner:** Muse.

Do:
1. Read `data/filings.db.stats.json` and confirm the totals: **5,724,302**
   records — LA 126,710 · PA 2,386,129 · CT 1,310,099 · NY 750,671 ·
   CO 536,338 · IA 351,617 · OR 203,815 · FL 58,923.
2. Run the test battery (`tests/run_tests.py`) against the DB-backed
   server, including the scope tests: city key returns only its city,
   state key only its state, out-of-scope lookup → 404, over-quota → 429
   `daily_quota_exceeded`, missing/bad key → 401, `/v1/categories` counts
   match a direct database count for every industry and need segment.
3. Measure memory: start the server, run a mixed query load (search,
   lookup, regions, categories across several states), and record peak RSS.
4. Spot-check `/health` output against the stats file.

**Verified when:** totals match the stats file exactly · test battery
passes in full · peak RSS is recorded in writing and sits comfortably
inside the free tier's 512 MB (the server is disk-backed and expected at a
few tens of MB — if RSS is anywhere near the cap, stop and fix before
deploying; do not ship a store that OOMs).

*Note: `LAUNCH-STATUS.md`'s "What remains" item 0 still describes the old
in-memory loader's RAM problem as an open decision. The SQLite rebuild
superseded it (the doc was not updated). This step is where that gets
closed out with a measured number.*

## Step (b) — Deploy to Render

**Needs:** the Render account to exist (free tier, seconddesks@proton.me —
account creation is a browser step outside this runbook; as of prep, that
signup was already queued) · step (a) verified · `render.yaml` +
`Dockerfile` from this folder (deploy package is ready).
**Owner:** Muse (deploy + verification). Jeremy: nothing, unless Render
demands a card for the free tier — if it does, STOP and report; no card is
added without his say-so.

Do:
1. In Render: New → Blueprint, point at this repo/folder (it reads
   `render.yaml`), Docker runtime, free plan, health check `/health`.
2. Set the `API_KEYS_JSON` environment secret with **freshly generated real
   keys** (free / city / state tiers with scopes, matching the pricing
   model). Never deploy the bundled demo keys as the live store.
3. First `docker build` runs here — watch it.

**⚠ Watch item — flagged, not assumed away:** the Docker image ships
`data/filings.db`, which is **~8 GB** on disk (7,946,309,632 bytes per the
build stats). Confirm the built image actually deploys and the service
starts within the host's limits. If Render's free tier rejects or chokes
on the image size, the fallbacks, in order: (i) Fly.io — `fly.toml` is
ready in this folder (Dallas region, same Dockerfile); (ii) report to
Jeremy with options (paid host with room, or a trimmed store) — do **not**
silently shrink the dataset to fit.

**Verified when** (all against the public URL):
- `GET /health` returns 5,724,302 records and all 8 states
- One successful call per endpoint (`/v1/businesses`, lookup,
  `/v1/regions`, `/v1/parishes`, `/v1/categories`) with a real key
- Scope enforcement live: a city-scoped key returns only its city (with
  `scope_note` when the request asks outside scope); an out-of-scope
  single-record lookup returns 404
- Over-quota call returns 429; the bundled demo keys are **rejected**
  (proves the env key store is in force)
- The resulting base URL is written down — it is the input to steps
  (c)–(h). Note in the launch report that the free tier sleeps after
  ~15 min idle (cold start on next request); a ~$7/mo always-on plan is
  Jeremy's call before actively selling, never automatic.

## Step (c) — Finish and publish the RapidAPI listing

**Needs:** live base URL (step b) · RapidAPI provider account
(seconddesks@proton.me; signup already queued as a browser step — the
account password is set by Jeremy via the secure card, as always) ·
`../RAPIDAPI-LISTING.md` (the listing copy is final there).
**Owner:** Muse for the listing build, test, and publish. Jeremy for
exactly two possible steps: setting the account password on the secure
card, and the tax/W-9 portion of payout setup if RapidAPI requires it.
The PayPal payout email itself — **Jeremy.nicks@icloud.com** — is entered
by Muse; Jeremy authorized that on Nov 25.

Do:
1. Create the API listing from `../RAPIDAPI-LISTING.md`: name, tagline,
   long description, the six endpoints with parameters and example
   responses from `README.md`, base URL = step (b).
2. Auth: RapidAPI key model — the marketplace injects `X-RapidAPI-Key` per
   subscriber; map it to this API's key tiers (validate the header at the
   gateway or translate to `X-API-Key`).
3. Pricing — exactly: **Free** (50/day, hard cap) · **City Heavy $29/mo**
   (5,000/day, one city bound to the key, overage $0.005/request) ·
   **Full State Heavy $59/mo** (10,000/day, one state bound to the key,
   overage $0.005/request). Plan type: **Freemium** so the free tier is
   capped, not unlimited.
4. Connect payout: PayPal → Jeremy.nicks@icloud.com. Park on any
   tax-interview/W-9 screen and hand it to Jeremy — never fill tax details
   for him.
5. Test in RapidAPI's console with a real subscriber key, then publish.

**Verified when:** the listing is public at its RapidAPI URL · console
tester passes one search, one lookup, one regions call, and one
over-quota call · the public pricing page shows Free / $29 / $59 with the
overage rate · payout method shows as connected (or the exact screen
waiting on Jeremy is reported).

## Step (d) — Submit the three marketplace listings

**Needs:** live base URL (step b) · the three drafts in this folder
(`listing-apilayer.md`, `listing-api-market.md`, `listing-apis-guru.md`) ·
seller accounts on each marketplace (seconddesks@proton.me).
**Owner:** Muse. Jeremy only if a marketplace demands tax/identity
verification during seller onboarding — park and hand off, same rule as
step (c).

Do:
1. **APILayer** — submit `listing-apilayer.md` (title, descriptions,
   endpoints, pricing table). At submission, read APILayer's *current*
   seller revenue share and payout terms off their seller pages and record
   them in the launch report — no figure exists in the source material, so
   none is assumed in the draft.
2. **API.market** — submit `listing-api-market.md`. Revenue share is known:
   seller keeps **80%** (a $29 plan pays out $23.20, a $59 plan $47.20).
3. **APIs.guru** — **blocked on a build task, not on copy.** APIs.guru
   requires a public OpenAPI definition and no spec exists in this project
   yet. First: author `openapi.yaml` from `README.md` +
   `../RAPIDAPI-LISTING.md` with `servers` = the live base URL, host it at
   a stable public URL, then submit via APIs.guru's GitHub contribution
   flow. (APIs.guru is a free directory — no payments, no rev share; it
   points developers at the spec and, through it, at the paid listings.)

**Verified when:** APILayer and API.market listing URLs are live with the
pricing table intact and a test call through each gateway succeeds ·
APIs.guru is either submitted (PR link recorded) or explicitly reported as
waiting on the OpenAPI build — never silently skipped.

## Step (e) — Update the Postman public workspace

**Needs:** live base URL (step b) · the "Second Desk Data" Postman public
workspace (shell created in the distribution sweep; if it turns out not to
exist, create it under seconddesks@proton.me).
**Owner:** Muse.

Do: replace the placeholder collection with the real one — the six
endpoints with example requests/responses from `README.md`, base URL
variable set to the live URL, a note that keys come from the RapidAPI
listing (link it). Publish the workspace version.

**Verified when:** the public workspace URL resolves without login, shows
the live base URL (no placeholder text remains), and the example search
request runs green against the live host with a demo/free key.

## Step (f) — Point the dataset listings at the live API

**Needs:** live base URL (step b) · RapidAPI listing URL (step c) · the
existing listings: Kaggle dataset + data.world dataset (both titled
"Louisiana New Business Filings — Free Sample (Needs a Website edition)"),
and the GitHub repo `louisiana-new-business-filings-sample`.
**Owner:** Muse.

Do: edit each listing's description/README to add the live API link and the
RapidAPI listing link where the copy currently says "API subscriptions"
generically. (The sample is the free 25-record file at
`~/workspace/niche-ai-products/34-pack-promo/SAMPLE-PACK/`; keep the honest
sample framing: 25 most recent filings, website-need is name-inferred, no
phone/email in filings data.)

**Verified when:** each of the three listings shows the working API link
(click through — the link resolves to the live `/health` or the RapidAPI
page), and no listing still describes the API as "coming soon."

## Step (g) — Post the announcements

**Needs:** RapidAPI listing live (step c) · `announce-show-hn.md` and
`announce-indie-hackers.md` in this folder with the bracketed links filled
in · the API warm (hit `/health` and one search right before posting, so
the free-tier host isn't cold for the first clickers).
**Owner:** Muse (posting from the brand accounts already in use for the
engagement rounds; Jeremy is not needed).

Do: post Show HN (Tue–Thu morning US time) with the before/after first
comment from the draft's posting notes; post the Indie Hackers piece the
same day or the next (per the draft's notes), linked from the Second Desk
IH product page.

**Verified when:** both post URLs are live and recorded in the launch
report · someone is watching the threads for the first 3–4 hours and
answering comments · the live API's `/health` is re-checked an hour after
posting (traffic + free-tier sleep is the known failure mode).

## Step (h) — Carriers page + job-board carriers link go live

**Needs:** the Render account from step (b) (same account, second service)
· the finished page at
`~/workspace/niche-ai-products/33-driver-leads/driver-landing/carriers.html`
(canonical copy; `site/carriers.html` is its mirror).
**Owner:** Muse.

Do: host `carriers.html` as a static site on the same Render account (free
static hosting) — or, if the driver site gets its own host first, there;
either way it must resolve at a public URL. Then update the driver
job-board listings (TruckDriverJobs411 / Class A Jobs 411 postings from
the distribution sweep) so their carriers link points at that URL instead
of only an email contact.

**Verified when:** the carriers URL resolves publicly · the page shows the
per-seated-hire offer, `seconddesks@proton.me` as the contact, and **no
postal address anywhere** (it was grep-verified at build; re-check after
any edit — the address rule is footer-only-in-email, never on pages) · at
least one job-board listing links to it.

---

## After launch (not launch-day steps)

- **Weekly refresh:** the `filings-api-weekly-refresh` cron (Wednesdays)
  already runs `refresh.py --weeks 2` (merge-only, Louisiana) and rebuilds
  packs. For the API: after each refresh, re-run `build_db.py` and
  redeploy so the hosted store picks up fresh data — until that's wired
  into the cron, a redeploy is a manual Muse step after each refresh.
- **Always-on hosting:** the ~$7/mo Render plan (kills the 15-min sleep)
  is Jeremy's spending call, recommended before any paid promotion.
- **x402 pay-per-call rail:** design note lives in `../RAPIDAPI-LISTING.md`.
  Later, optional, and Jeremy's steps (a wallet) if it ever happens.
