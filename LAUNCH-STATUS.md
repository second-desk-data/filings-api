# Launch Status — New Business Filings API (Multi-State)

Updated 2026-11-26. The API now serves **all 8 built states** from a
disk-backed SQLite store — the Louisiana-only launch scope and the
storage/scale blocker are both gone. What remains is the same short
chain of account steps, listed in order at the bottom.

## Dataset (all built states, store built 2026-11-26)

| State | Records | Filing dates (as published) | Notes |
|---|---|---|---|
| Louisiana | 126,710 | 2024-11-24 → 2026-11-21 | Two full years, 104 consecutive weekly NewCorp newsletters; 63 of 64 parishes (87.4% parish-tagged; only Cameron absent); Evangeline Parish 441 |
| Pennsylvania | 2,386,129 | 1753-01-01 → 2026-10-31 | Full registry snapshot; county for ~97.6%; PA has no registered-agent system (0% agents is structural) |
| Connecticut | 1,310,099 | 1900-01-02 → 2026-11-24 | Full registry; ~95.7% with agent; enriched 2026-11-26 — `email_as_filed` on 813,554 (62.1%), `naics_code` on 625,411, `naics_title` on 612,190 |
| New York | 750,671 | 2024-01-01 → 2026-11-24 | City = DOS process city (~100%); county where published |
| Colorado | 536,338 | 2024-01-01 → 2026-11-21 | 99.1% city, 98.3% agent |
| Iowa | 351,617 | 1828-06-07 → 2027-01-01 | Active registry as published; a few records carry future effective dates (max 2027-01-01) exactly as filed |
| Oregon | 203,815 | 2024-01-02 → 2026-11-23 | 100% city; county for ~74.8% from the state's companion dataset |
| Florida | 58,923 | → 2027-02-15 | Last ~30 days of daily filings only (the 1.79 GB quarterly file is a later pull); some records carry future effective dates as filed |
| **Total** | **5,724,302** | | |

Louisiana mix: 105,181 LLC · 12,599 foreign entities · 5,258 nonprofit ·
2,877 corporation · 795 other; 126,000 with a registered agent. Top
parishes: East Baton Rouge 13,208 · Orleans 12,116 · Lafayette 11,647 ·
Jefferson 10,498 · St. Tammany 8,227.

Source rules held during the Louisiana backfill: published newsletter
files only, descriptive User-Agent, ~0.6 s between probes, ≥1.5 s
between downloads, sequential requests. The CORAWeb search site was
never touched (Louisiana's admin rule blocks automated harvesting
there — see `data/SOURCES.md`). State registries came from each state's
own open-data bulk feed (see `expansion/` and `data/SOURCES.md`).

## Storage: SQLite, disk-backed (the scale decision, resolved)

- `build_db.py` converts `data/seed.json` + every `data/states/*.json`
  into **one SQLite database** (`data/filings.db`), streaming the JSON
  (the 744 MB PA file never sits in RAM; builder RSS ~36 MB). Records
  are normalized and classified **at build time** with the one canonical
  classifier (`packs/classify.py`, via the `app/classify.py` adapter);
  the full annotated record is stored as JSON so response shapes are
  unchanged. Filter columns (state, city, region/parish/county,
  category, filing date, name) are indexed; per-state category/need/
  region counts are precomputed into side tables.
- **Store size: 8,616 MB** for 5,724,302 records; full build ~34 min
  uncontended (weekly rebuilds only need Louisiana re-merged + a
  rebuild, or an incremental path later).
- **Deploy build (2026-11-26 slimming): `data/filings.deploy.db`,
  6,626,488,320 bytes (6.63 GB)** — the artifact the Dockerfile ships.
  Identical content to the canonical store (every record, every field,
  every index, verified row-by-row: all 5,724,302 `record_json`
  payloads decompress byte-identical to canonical, all filter columns
  and side tables equal), with one change: `record_json` is stored
  zlib-compressed (3,987 MB of JSON → ~2,200 MB at the measured 0.552
  ratio). `build_db.py --compress` produces this build directly;
  `app/server.py` decompresses transparently on read (it accepts both
  text and blob payloads), so **API responses are byte-identical** —
  proven by an A/B battery of 16 representative requests against both
  stores (the only differing response was `/health`, whose
  `server_time` field differs between any two servers) and by the full
  test suite below. The canonical `data/filings.db` stays plain-text
  on purpose: the test suite's phase 3 reads it with direct SQL
  (`json_extract`, `LIKE`), which compressed blobs would break.
  Where the 8,616 MB lived (dbstat): filings table 5,944 MB
  (record_json 3,987 MB + filter columns ~1,957 MB), indexes 1,839 MB,
  record_needs 534 MB, precomputed count tables ~0 MB, freelist 0.
  No field was dropped: the only candidate duplication inside
  record_json (`name` vs `name_raw`) proved real data — the two differ
  in 26% of sampled records — and `id`/`charter_number` deduplication
  would have saved only ~80 MB post-compression at the risk of JSON
  key-order drift, so it was not taken.
- **Verified free-host limits (official docs, 2026-11-26):** Render —
  a Docker image's **compressed size cannot exceed 10 GB**
  (render.com/docs/deploying-an-image); free web services get 512 MB
  RAM and an ephemeral filesystem (the DB lives inside the image, so
  spin-down loses nothing). Fly.io — **uncompressed images on standard
  Machines are limited to 8 GB**, the maximum rootfs size
  (docs.fly.io/flyctl/integrating); the current Free plan is a $1
  one-time credit / $5 monthly with a card, up to 3 Machines. The
  deploy image (python:3.12-slim + the 6.63 GB store ≈ 7.1 GB
  uncompressed, far less compressed) **fits both limits with margin —
  the single-file deploy is the plan; no per-state split is needed.**
- **One index added (2026-11-26):** `idx_filings_state_city_date_name
  (filing_state, city_lower, filing_date DESC, name_raw DESC)` — in
  `build_db.py` and in both stores on disk (+329 MB on the deploy
  store, already included above). Reason: the page-fetch sort for a
  state+city query had no covering index, so a city key on Harrisburg
  PA (155,822 records — a registered-agent hub) took 51–79 s and the
  test suite's 60 s client timed out on it; with the index the same
  request answers in **0.27 s**. No response content changed.
  Known remaining slow path, unchanged in nature: composed
  city+category counts on hub cities (Denver: 128,774 records) walk
  the city subset fetching `category` from the table — ~50 s on this
  contended VM with a cold page cache, 27 ms warm on the idle box
  where the original battery ran. The query plan is optimal
  (index-ordered scan); it is a cold-cache I/O cost, not a defect
  introduced by compression (the count never touches record_json).
- `app/server.py` holds **no records in RAM** — every endpoint is SQL
  against the read-only database. Measured on this machine against the
  full 8-state store:
  - **RSS 23 MB after boot, 31 MB after the full query battery**
    (free-tier hosts allow 512 MB — the old in-memory design needed
    several GB and could not launch at all).
  - Startup is effectively instant (health answering ~1 s after launch).
  - Latency: `/health` 2–3 ms · state search page (PA, 2.39M records)
    119–306 ms · state+city+category 27 ms warm (~4 s cold-disk) ·
    `state=NY&age_days=90` 47–71 ms · unfiltered first page 147–326 ms ·
    `/v1/categories?state=PA` **14 ms** (precomputed counts; the live
    join it replaces measured >120 s) · `/v1/regions` 6 ms ·
    `/v1/parishes` ~0.8 s.
  - **Deploy-store re-measure (2026-11-26, compressed store, this VM
    under heavy multi-agent load — treat as worst-case):** boot RSS
    23 MB, 29 MB after the battery · `/health` 1–3 ms ·
    `/v1/categories` 3–9 ms · `/v1/regions` 4–7 ms · PA state page
    1.5–2.5 s warm · Harrisburg city page 0.3–0.6 s (post-index) ·
    Denver state+city+category ~50 s cold-cache (see Storage note).
  - Known slow paths, both full-corpus scans by nature: `q=` substring
    search across all states (~12–14 s cold) and a bare `need=` search
    (~11–12 s cold). Scoped/state-filtered variants of both are fast.
- **age_days anchoring:** age is measured from the newest filing date
  of the dataset in play — the queried/scoped state's own max date when
  one state is in play (each registry is its own dataset with its own
  "now"; PA's snapshot ends 2026-10-31 and is not aged out by LA's
  fresher feed), the global max otherwise. The packs' definition
  (age < N days) is unchanged.

## Done

- [x] **Backfill** — `backfill.py` walks the weekly archive back and rebuilds
      `seed.json`. Weekly `refresh.py` now *merges* into the history instead
      of replacing it (verified: a 1-week refresh kept all 126,710 records
      and 104 sources).
- [x] **50-state expansion adapters** — 7 more states built from their own
      open-data feeds (CO, CT, FL, IA, NY, OR, PA; see `expansion/builds/`).
      OH/AK/MS are bot-walled on this network, VT is login-gated, VA's
      published files are truncated — all documented, none bypassed.
- [x] **SQLite store + disk-backed server** (2026-11-26) — see "Storage"
      above. The Dockerfile now ships `data/filings.db` (built by
      `build_db.py` before `docker build`; the ~2 GB JSON corpus is
      docker-ignored). `render.yaml` / `fly.toml` unchanged in shape.
- [x] **Deploy package** — `Dockerfile` (python:3.12-slim, stdlib only,
      healthcheck on `/health`), `render.yaml` (Render free-tier web
      service), `fly.toml` (Fly.io, Dallas region, `/health` check).
      Either host works; nothing is host-specific in the code. The
      image ships the **compressed deploy store (6.63 GB)** — see
      "Deploy build" under Storage — and fits both hosts' verified
      image limits (Render ≤10 GB compressed; Fly ≤8 GB uncompressed).
- [x] **Environment config** — the server reads `PORT` and `HOST` from the
      environment and takes real keys from `API_KEYS_JSON` (same JSON shape
      as `api_keys.json`). The bundled `api_keys.json` is labelled
      DEMO KEYS ONLY and is overridden whenever `API_KEYS_JSON` is set.
- [x] **Filters, scope, categories** — every filter is live: `state=`,
      `region=`/`county=` (parish stays), `city=`, `category=` (industry),
      `need=` (likely-need segment), `age_days=` (per-state anchored,
      packs' exact definition), `year=`, plus the original
      q/type/origin/from/to filters — all composing. `/v1/regions`
      generalizes parishes (counties elsewhere); `/v1/parishes` remains
      as the Louisiana view. `/v1/categories` serves industries + need
      segments with live counts. Per-key **scope** is enforced everywhere
      (all / state / city per the $29/$59 pricing): out-of-scope records
      are excluded from results and counts with a `scope_note` when the
      request explicitly asked outside the scope, and single-record
      lookups outside scope answer 404.
- [x] **Pricing matched** (Nov 25, 21:30, Jeremy: "match them, not too
      high") — Free 50/day · City Heavy **$29/mo** · Full State Heavy
      **$59/mo**, overage $0.005/request, RapidAPI's 25% priced in.
      Basis: FirstFiling Leads $29/county, $59/state; full-state
      all-industry services run $99/mo — we match the low end.
- [x] **Connecticut enrichment** (2026-11-26, competitive-intel
      follow-up) — the CT feed carries fields the original adapter build
      dropped; verified against the live Socrata n7gp-d28j metadata and
      captured: `email_as_filed` on **813,554** CT records (62.1%; the
      filer's own published business email, stored only under the
      as-filed label per `data/SOURCES.md`), `naics_code` on **625,411**,
      `naics_title` on **612,190** (decomposed from the feed's combined
      "Title (531110)" strings; nothing backfilled). `build.py` selects
      all three natively now; the one-off capture was `enrich.py` +
      `merge_enrichment.py` under `expansion/builds/CT/`.
- [x] **Formation-mill flag** (2026-11-26) — every record carries
      `agent_is_commercial`, set at build time by the canonical matcher
      in `packs/classify.py` (Registered Agents Inc, Northwest
      Registered Agent, CSC / Corporation Service Company, LegalZoom,
      ZenBusiness, Incfile/Bizee, Cogency Global, CT Corporation,
      Paracorp; conservative name matching, "CSC" as a standalone token
      only). Additive only — records are never altered or dropped.
      **Flagged counts (full-store count, 2026-11-26): 360,239 records**
      — CO 89,510 · CT 100,282 · FL 8,163 · IA 46,940 · LA 17,889 ·
      NY 65,828 · OR 31,627 · PA 0 (structural: PA has no
      registered-agent system, so no record can be flagged). The same
      pass re-verified the CT enrichment counts independently:
      `email_as_filed` 813,554 and `naics_code` 625,411, matching the
      figures above exactly.
      Deferred, flagged: pack CSVs do not carry the column yet (the
      matcher is in the shared classifier, so the next pack build pass
      adds it with no further code change; left untouched to avoid
      changing pack shape while the state-pack build is in flight).
- [x] **Mixed-case charter lookup fix** (2026-11-26) — single-record
      lookup historically uppercased the requested charter, which made
      every mixed-case CT id (Salesforce-style, e.g. `0018y000008iXJtAAM`)
      unfindable. Lookup now matches exact case first and retries
      uppercased as a fallback, preserving the old behavior for LA-era
      callers.
- [x] **Test battery: 64/64 passed** (`tests/run_tests.py`) — the original
      43 checks (health/401/quota/parish battery, composed city+category
      queries verified record-by-record, age windows at 7/30/90 days,
      `year=2025`, bad-input 400s, `/v1/categories` counts matching a
      direct dataset count for every industry and need, city/state scope
      behavior including the no-leak 404, fixture-phase state merge +
      normalization) **plus 21 multi-state checks** against the real
      8-state store: per-state counts for all 8 states, a city filter on
      Denver CO, a city-scoped key on Harrisburg PA (count, other-city
      exclusion + scope_note, LA-record 404), a state-scoped key on NY,
      per-state `/v1/categories` counts, age_days anchored to CO's own
      max date, the PA county `/v1/regions` view, and (2026-11-26) the
      CT enrichment + mill-flag checks: every record carries a boolean
      `agent_is_commercial`, a CT record serves its `email_as_filed` /
      `naics_code` exactly as stored, and a Northwest-agent record is
      flagged true. Note: run the suite with `TMPDIR` pointed at real
      disk — this machine's /tmp is a 512 MB tmpfs that other jobs fill,
      which starves the fixture-phase build. **Re-run 2026-11-26 after
      the deploy slimming (server decompression + the new city-sort
      index): 64/64 passed again.** The first two attempts that day
      timed out at the Harrisburg city-key check (60 s client limit vs
      the then-unindexed 51–79 s sort) — the index above is the fix,
      verified at 0.27 s for that exact request.

Not yet verifiable from here: the Docker image build itself (no Docker on
this machine). The Dockerfile is a straight stdlib copy-and-run, and the
identical code path was tested natively; the first `docker build` happens
as part of the deploy step below.

## Remaining launch steps — in order

1. **Host account — IN FLIGHT.** A free Render account signup was launched
   Nov 25 under Jeremy's account-creation authorization (free tier, no
   card). Free-tier honesty: Render free web services spin down after
   ~15 minutes idle and cold-start on the next request (fine for
   validating the listing; a ~$7/mo plan keeps it awake before actively
   selling). **No spending without Jeremy's say-so.**
2. **Deploy — CLAUDE** (once the host account exists and Muse has access).
   Run `python3 build_db.py` (canonical store) **and**
   `python3 build_db.py --compress --out data/filings.deploy.db`
   (the compressed store the Dockerfile ships), point the
   host at this folder/repo (Render: New → Blueprint, it reads
   `render.yaml`; Fly: `fly deploy` with `fly.toml`), set the
   `API_KEYS_JSON` secret with freshly generated real keys (never the demo
   keys), first `docker build` runs here, then verify the live `/health`
   and one call per endpoint against the public URL.
3. **Weekly refresh on the host — CLAUDE.** Schedule `refresh.py --weeks 2`
   after each Tuesday file lands (host cron / scheduled job), re-run
   `build_db.py`, and restart the service, so the dataset stays current.
4. **RapidAPI provider listing — IN FLIGHT.** The RapidAPI signup was
   launched Nov 25 (Jeremy authorized Muse to fill it out; the account
   password is set by Jeremy himself via the secure card, as always).
   Then Muse creates the API listing from `RAPIDAPI-LISTING.md`: name,
   copy, the endpoints, base URL = the deployed host, pricing Free /
   City Heavy $29 / Full State Heavy $59 with overages, Freemium plan
   type, and a console test (one search, one lookup, one parishes call,
   one over-quota call) before publishing.
5. **Payout — JEREMY (one-time) + CLAUDE.** Muse links the PayPal email
   (Jeremy.nicks@icloud.com) at the RapidAPI payout step per Jeremy's
   Nov 25 authorization; any tax details remain Jeremy's step alone.
6. **Announce — CLAUDE** (after the listing is live). The comparable-solo-API
   pattern: a Show HN / Indie Hackers post showing the messy source text
   next to the clean API response, plus the Second Desk channels already
   running.

**Later, optional:** the x402 pay-per-call rail (design note in
`RAPIDAPI-LISTING.md`), the state's official Commercial API license
($500/yr, La. Admin. Code tit. 19, § V-902) as the checkpoint before ever
scaling beyond the published weekly newsletters, and pulling Florida's
full quarterly file (1.79 GB) to complete FL coverage.
