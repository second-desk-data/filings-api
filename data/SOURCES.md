# Data Sources — Louisiana New Business Filings API

## Primary source (used for the seed dataset)

**Louisiana Secretary of State — "NewCorp" weekly newsletters**
- URL pattern: `https://corafiles.sos.la.gov/NewCorpNewsletters/NewCorp_YYYYMMDD.txt`
- Files used for the dataset (fetched 2026-11-25/26; history extended
  2026-11-26 by `backfill.py`):
  - **104 consecutive weekly files**, `NewCorp_20241203.txt` through
    `NewCorp_20261124.txt` — every week in that span was present; the
    backfill stopped at its 104-week cap, not at the start of the archive,
    so older files exist if more history is ever wanted.
  - **126,710 unique records** after de-duplication by charter number
    (only 1 cross-file duplicate was found in two years of files).
  - Filing dates covered: **2024-11-24 → 2026-11-21** (two full years).
- Publisher: Louisiana Department of State, Commercial Division.
- Contents: every new domestic charter and new foreign qualification filed that
  week — charter number, filing date, entity name, mailing address, registered
  agent(s), and officers/members/managers where applicable.
- Public status: business filings are public records in Louisiana; these
  newsletters are documents the Secretary of State itself publishes on a public
  web server for anyone to download. No login, no paywall, no personal data
  beyond what the filings themselves publish (business addresses and the names
  of registered agents/officers, which are part of the public filing).
- Update cadence: **weekly** (files are dated Tuesdays). The API dataset should
  be refreshed weekly with `refresh.py`.
- Access method: direct download of the published text files only, with a
  descriptive User-Agent, ~0.6 s between existence probes and ≥1.5 s between
  downloads, sequential requests only. The one-time backfill pulled the 104
  published weekly files; ongoing pulls are one file per week — no bulk
  crawling, and the CORAWeb search site is never touched.

## Second dataset — FMCSA new motor carriers (national, trucking segment)

**FMCSA Company Census File** — built 2026-11-26 in
`expansion/builds/FMCSA/` (`fetch_fmcsa.py`, `normalize_fmcsa.py`,
`build_packs.py`; merge design in `MERGE-PLAN.md` there).

- Dataset page: https://data.transportation.gov/d/az4n-8mr2 — API endpoint
  `https://data.transportation.gov/resource/az4n-8mr2.json` (Socrata).
  Catalog record (data.gov) lists publisher FMCSA, access level **public**,
  rights **public**. Federal public data; no reuse prohibition in the
  dataset metadata.
- Access method: server-side SoQL filter on `ADD_DATE` (last 24 months)
  with paging (50,000/page, ordered by USDOT number) — the full ~4.43M-row
  census file is deliberately **not** downloaded. Descriptive User-Agent,
  sequential requests, ≥1.2 s between pages. No app token used or needed
  at this volume; no API caps or errors were hit.
- Pull of 2026-11-26: **351,258 unique carriers** with ADD_DATE from
  2024-11-26 to 2026-11-24 (0 duplicates), of which 101,671 report ≥2 power
  units and 215,446 exactly 1. Louisiana: 2,737. Status mix: 308,748
  active, 41,871 inactive, 639 pending.
- Contents used: legal/DBA name, USDOT number, ADD_DATE, physical
  city/state/ZIP, **phone and email as filed**, power units, total drivers,
  carrier operation, for-hire classification, cargo classes.
- **As-filed contact labelling rule (binding):** phone/email are the
  business contact fields the carrier itself filed in a public federal
  record, and are stored and sold only under the labels `phone_as_filed` /
  `email_as_filed`. They are a public record, **not marketing consent** —
  for sole proprietors they are often personal numbers. Any outreach use
  is governed by the TCPA and CAN-SPAM (consent, opt-outs); the pack cover
  sheets state this plainly. Individual CDL-holder data is a different,
  DPPA-protected thing and is **not** in this dataset or this business.
- Derived segments (definitions fixed in `normalize_fmcsa.py` and used
  identically in the packs and the planned API): `likely_hiring` =
  power_units ≥ 2 (1-truck owner-operators are never labelled hiring);
  `owner_operator` = power_units == 1; `age_days` is measured from the
  newest ADD_DATE in the pull (2026-11-24).
- Update cadence: the census updates near-daily; this segment refreshes
  weekly with the main refresh job (sliding ADD_DATE window, re-normalize,
  rebuild packs).

## Sources evaluated and NOT used

- **Parish occupational-license / permit sites (the original idea):** genuinely
  public but too fragmented to pull reliably — 64 parishes with a mix of PDFs,
  third-party portals, and in-person-only records; no common format or cadence.
  The statewide Secretary of State filings cover the same "new business in a
  parish" need in one clean feed, so the API is built on those instead.
- **CORAWeb Commercial Search (coraweb.sos.la.gov):** the state's interactive
  entity lookup. It is designed for one-at-a-time human searches; Louisiana's
  own administrative rule for the Commercial API notes the Department has had
  to block automated harvesting of the website. We do **not** scrape it.
- **Louisiana Commercial API (official, paid):** the Secretary of State sells a
  subscription API for this same register — $500/year, non-refundable, per
  La. Admin. Code tit. 19, § V-902. This is the state's official bulk channel
  and the natural upgrade path (and licensing checkpoint) if this product ever
  needs full-population or real-time data instead of the weekly published
  newsletters.

## Derived fields — read this before trusting them

- **Parish** is *derived*, not printed in the source: the newsletter gives a
  mailing city, and `refresh.py` maps city → parish with a built-in table of
  Louisiana cities (principal parish where a city spans more than one).
  Coverage: **110,693 of 126,710 records (87.4%)** have a parish; 63 of
  Louisiana's 64 parishes appear in the two-year dataset. The one absence is
  Cameron Parish — no filing in the window carried a mappable Cameron Parish
  city (it is Louisiana's least populous parish and filings there are rare).
  Records whose city is out of state or not in the table have
  `parish: null` — they are never guessed.
- **Entity type** is a *heuristic* from the charter-number suffix, the
  Secretary of State's filing-type convention (K = LLC, D = domestic
  corporation, N = nonprofit corporation, E/EE = foreign entity). The
  newsletter does not print an explicit type field; treat `entity_type` as
  a strong hint, and `origin` (domestic vs. foreign, taken from the
  newsletter's own section headers) as authoritative.
- **Names/addresses** are reproduced from the filings as published, including
  the filers' own typos. `name` is title-cased for display; `name_raw` keeps
  the original ALL-CAPS form and is what text search matches against.

## Refresh method

`python3 refresh.py --weeks 2` — walks back from today to find the newest
published newsletter files (there is no directory index; the server returns
403 for listings), downloads them politely, re-parses, de-duplicates by
charter number (newest file wins), and rewrites `data/seed.json`.
Run weekly, after the Tuesday file lands.

`python3 backfill.py` — the one-time history builder: walks back week by
week from the newest file (up to `--max-weeks`, default 104), downloads
every published weekly file it finds, and rebuilds `data/seed.json` with
the full history. Used on 2026-11-26 to load the two-year dataset above.
Weekly `refresh.py` runs merge into the existing dataset by default
(fresh parses win, history is kept; `--no-merge` replaces instead), so
the backfilled history survives the weekly refresh.


---

# Additional state sources — 50-state expansion (built 2026-11-25/26)

Entries below were written per state during the expansion build (see expansion/builds/BUILD-REPORT.md for status and verification counts). States whose adapters are built but whose verification pull was blocked (AK, MS, OH) and Vermont (account-gated) are included so the source, automation basis, and caveats are on record; their data files do not exist yet.


---

## Florida (data/states/FL.json)

**Florida Division of Corporations — "Data Downloads" (daily + quarterly files)**
- URLs:
  - Download portal (public SFTP/browser): `https://sftp.floridados.gov`
    - Daily Corporate Filings: `https://sftp.floridados.gov/Public/doc/cor/yyyymmddc.txt`
    - Quarterly full Corporate Data file: `https://sftp.floridados.gov/Public/doc/quarterly/cor/cordata.zip`
  - Official pages: `http://dos.fl.gov/sunbiz/other-services/data-downloads`,
    `https://dos.fl.gov/sunbiz/other-services/data-downloads/daily-data/`,
    `https://dos.fl.gov/sunbiz/other-services/data-downloads/quarterly-data/`
  - Field definitions: `https://dos.sunbiz.org/data-definitions/cor.html`
  - Access uses the public credentials the Division itself publishes on the
    pages above (username "Public"; password published on those pages).
- Publisher: Florida Department of State, Division of Corporations.
- Contents: fixed-length ASCII Corporate Data File (record length 1440) —
  document number, entity name, filing type, principal address/city/state/ZIP,
  file (formation) date, registered agent name/type/address, officers.
- Update cadence: **daily** on work days (filings added that day; Daily
  Corporate Filings `yyyymmddc.txt` and Daily Corporate Events
  `yyyymmddce.txt`) and **quarterly** full snapshots (Jan/Apr/Jul/Oct;
  `cordata.zip` contains all data on record at generation time).
- Public status / automation basis: business filings are public records in
  Florida, and the Division itself publishes these files for download —
  "The Division of Corporations offers free data downloads for informational
  purposes" — via browser or SFTP client. No login beyond the published
  public credentials, no paywall, no personal data beyond what the filings
  themselves publish. Access method: direct download of the published files
  only, with a descriptive User-Agent, ≥1 s between downloads, sequential
  requests only. The Sunbiz search portal is never touched.
- Fields used (Corporate Data File, per the official definitions):
  #1 Corporation (document) Number → `id`; #2 Corporation Name → `name_raw`
  (and title-cased `name`); #4 Filing Type → `entity_type` (mapping below);
  #7 principal City → `city`; #9 principal Zip → `zip` (first 5 digits);
  #17 File Date (MMDDYYYY) → `filing_date` (YYYY-MM-DD);
  #31 Registered Agent Name → `registered_agent`.
- Entity-type mapping (#4 → common schema): FLAL → LLC; DOMP → Corporation;
  DOMNP / NPREG → Nonprofit; DOMLP → Partnership; FORP / FORNP / FORL /
  FORLP → Foreign Entity; TRUST / AGENT / anything else → Other.
- Derived fields — read this before trusting them:
  - **County is always `null`** (`county_or_parish: null`): the Corporate
    Data File has **no county field** (it carries principal City, Mail City,
    and Registered Agent City, but no county), and this codebase has no
    reliable Florida city→county mapping — many Florida cities span multiple
    counties — so county is never guessed.
  - **Names** are reproduced from the filings as published. `name` is
    title-cased for display (with LLC/LLP/LP suffixes restored to caps);
    `name_raw` keeps the original ALL-CAPS form. Registered-agent names are
    reproduced as published (whitespace collapsed; the source orders them
    surname-first).
  - **Filing date** is the file's own File Date (formation filing date),
    which is not always the day the record appeared in a daily file: the
    daily files occasionally carry much older formation dates and
    future-dated (pre-effective) filings.
- Refresh method: `python3 expansion/builds/FL/build.py` — lists the
  quarterly directory and, if `cordata.zip` is under ~1.5 GB, parses the
  full file; otherwise (as at first build, when it was 1,793,034,019 bytes)
  it pulls the most recent 30 daily Corporate Filings files and rewrites
  `data/states/FL.json`.


---

## New York (append to data/SOURCES.md)

## New York source (multi-state expansion)

**New York Department of State, Division of Corporations — published Socrata open datasets on data.ny.gov**

- Publisher: New York State, Department of State, Division of Corporations.
  Published free of charge as official open data on data.ny.gov (Socrata).
  No login, no paywall, no app token required.
- Primary dataset: **"Active Corporations: Beginning 1800"**
  - Dataset ID: `n9v6-gdp6`
  - API endpoint: `https://data.ny.gov/resource/n9v6-gdp6.json`
  - Contents: every active corporation, LLC, partnership and other entity
    on the DOS register, with DOS ID, current entity name, initial DOS
    filing date, county, entity type, DOS process (service of process)
    name/address, principal location name/address, and registered agent
    name/address where one is filed.
- Companion dataset: **"Daily Corporation and Other Entity Filing Data"**
  - Dataset ID: `k4vb-judh`
  - API endpoint: `https://data.ny.gov/resource/k4vb-judh.json`
  - Contents: corporation and other entity filings processed in the
    previous 30 days; the natural daily-refresh companion to the monthly
    Active Corporations snapshot.
- Update cadence: **monthly** for Active Corporations (full refresh of the
  recent-filings slice); the Daily Filings dataset updates daily and can
  drive more frequent incremental refreshes.
- Pull strategy (this product is new-business filings, not the full
  register): from Active Corporations, only records with
  `initial_dos_filing_date >= 2024-01-01`, fetched with Socrata
  `$where` filtering and `$limit`/`$offset` paging (50,000-row pages,
  `$order=dos_id` for stable paging). The full 1800s-to-now registry is
  deliberately NOT pulled. If the filtered query ever fails, `build.py`
  falls back to a full pull of the Daily Filings dataset (previous 30
  days only) and reports that it did so.
- Access method: published Socrata API endpoints only, sequential
  requests, ≥1 s between requests, descriptive User-Agent
  (`SecondDesk-FilingsAPI/1.0 (+https://deskside.gumroad.com)`), no app
  token. The NY DOS interactive entity-search portal is **never** touched
  — it is a one-at-a-time human lookup tool, not a bulk channel.
- Automation basis: these are official Socrata open datasets that the
  State of New York itself publishes for programmatic reuse; the Socrata
  API is the state's intended bulk-access channel for this register.
  Business filings are public records; the datasets contain only what the
  filings publish (business names/addresses and registered-agent names).

### Fields and derivations (New York)

Common-schema mapping (see `expansion/builds/NY/build.py`):

- `id` = `dos_id` (DOS identification number).
- `name` / `name_raw` = `current_entity_name`, exactly as published
  (NY publishes a single name form, so both fields carry it).
- `state` = `"NY"` (constant).
- `city` = `location_city` if populated, else `dos_process_city`.
  Note: in the ≥2024-01-01 slice the `location_*` columns are never
  populated, so in practice the city is the DOS process city.
- `zip` = the zip matching the city chosen (`location_zip` with a
  location city, otherwise `dos_process_zip`).
- `county_or_parish` = the dataset's `county` column, as published
  (the county in which the DOS process address / filing sits).
- `filing_date` = `initial_dos_filing_date`, truncated to YYYY-MM-DD.
- `registered_agent` = `registered_agent_name`, or `null` where blank.
  Many New York corporations list only a DOS process address for service
  of process; the DOS process name is **never** substituted as the
  registered agent.
- `source_url` = `https://data.ny.gov/resource/n9v6-gdp6.json`.

Entity-type normalization — NY `entity_type` → common schema, applied in
this priority order (verified against all 31 distinct values present in
the ≥2024-01-01 slice):

1. Contains `FOREIGN` (incl. `FOREIGN BUSINESS CORPORATION`,
   `FOREIGN LIMITED LIABILITY COMPANY`, `FOREIGN NOT-FOR-PROFIT
   CORPORATION`, `FOREIGN LIMITED PARTNERSHIP`, `FOREIGN PROFESSIONAL
   SERVICE ...`, `NEW YORK REGISTERED FOREIGN LIMITED LIABILITY
   PARTNERSHIP`, `UNAUTHORIZED FOREIGN BUSINESS CORPORATION`)
   → **Foreign Entity**
2. Contains `NOT-FOR-PROFIT` (incl. the Article XI housing, land bank,
   fire corporation, local development corporation, board of trade and
   public cemetery variants, and `DOMESTIC LIMITED-PROFIT HOUSING
   COMPANY`) → **Nonprofit**
3. Contains `LIMITED LIABILITY COMPANY` (incl. `DOMESTIC PROFESSIONAL
   SERVICE LIMITED LIABILITY COMPANY`) → **LLC**
4. Contains `PARTNERSHIP` (`DOMESTIC LIMITED PARTNERSHIP`,
   `DOMESTIC REGISTERED LIMITED LIABILITY PARTNERSHIP`) → **Partnership**
5. Contains `CORPORATION` (`DOMESTIC BUSINESS CORPORATION`,
   `DOMESTIC PROFESSIONAL SERVICE CORPORATION`, `DOMESTIC BENEFIT
   CORPORATION`, `DOMESTIC PUBLIC BENEFIT CORPORATION`, `DOMESTIC
   COOPERATIVE CORPORATION`, `DOMESTIC TRANSPORTATION CORPORATION`,
   `DOMESTIC DESIGN PROFESSIONAL SERVICE CORPORATION`, `DOMESTIC
   HOUSING DEVELOPMENT FUND COMPANY`) → **Corporation**
6. Anything else (e.g. `RESERVATION OF DOMESTIC LIMITED LIABILITY
   COMPANY`) → **Other**

### Refresh method

`python3 expansion/builds/NY/build.py` — re-pulls the
`initial_dos_filing_date >= 2024-01-01` slice of Active Corporations and
rewrites `data/states/NY.json`. Run monthly, after the dataset's monthly
refresh lands; optionally supplement with the Daily Filings dataset for
intra-month increments.


---

# Data Sources — Colorado (ready to append to data/SOURCES.md)

## Colorado

**Colorado Secretary of State — "Business Entities in Colorado" (Colorado Information Marketplace / data.colorado.gov)**
- URL / dataset ID: `https://data.colorado.gov/resource/4ykn-tg5h.json` — Socrata resource ID `4ykn-tg5h`, on the Colorado Information Marketplace (data.colorado.gov), published by the Colorado Department of State (Secretary of State).
- Contents: all business entities registered with the Colorado Department of State since 1864 — entity ID, entity name, entity type (Secretary of State abbreviation), entity status, formation date (`entityformdate`), jurisdiction of formation, principal address (city/state/ZIP), mailing address, and registered agent first/middle/last name, organisation name, and agent address including city. This dataset's pull is filtered server-side to formation dates on/after 2024-01-01 (new-business filings only); the full registry back to 1864 is not pulled.
- Update cadence: **daily**.
- Fields used: `entityid` → `id`, `entityname` → `name` / `name_raw` (as published), `principalcity` → `city`, `principalzipcode` → `zip`, `entityformdate` → `filing_date` (YYYY-MM-DD date part), `entitytype` → `entity_type` (normalized, see below), `agentorganizationname` / `agentfirstname` + `agentmiddlename` + `agentlastname` → `registered_agent`. `state` is set to `"CO"` for every record; `source_url` is the dataset URL above.
- Entity type mapping (`entitytype` is a Secretary of State abbreviation; D = domestic, F = foreign): any F-prefixed code (FLLC, FPC, FNC, FLP, FLLP, FLLLP, FLCA, FLPA, FCOOP, FO) → **Foreign Entity** (mirroring the Louisiana convention for foreign qualifications); DNC → **Nonprofit**; DLLC → **LLC**; DPC / DPC-PBC → **Corporation**; DLLP / DLP / DLLLP / DLPA / GP → **Partnership**; everything else (DLCA / DLCA-PBC limited cooperative associations, DC55 / DC56 cooperatives and -PBC variants, UNA, CS, unlisted codes) → **Other**.
- Derivations:
  - **County is `null` on every record (`county_or_parish: null`)** — the dataset publishes **no county field**, and there is no reliable Colorado city→county mapping in this codebase, so county is never guessed.
  - **Registered agent** is the agent organisation name (`agentorganizationname`) when present, otherwise the agent's first + middle + last name combined; `null` when neither is present.
- Automation basis: official Socrata open dataset published by the state for programmatic access — access is via the Socrata API only, with a descriptive User-Agent, ≥1 s between requests, and 50,000-row pages. The Colorado Secretary of State's interactive business-search portal is never touched.
- Refresh method: `python3 expansion/builds/CO/build.py` — re-pulls all entities with `entityformdate >= 2024-01-01` (server-side `$where`), de-duplicates by entity ID, and rewrites `data/states/CO.json`. Column names were confirmed against the Socrata metadata endpoint `https://data.colorado.gov/api/views/4ykn-tg5h` before querying.


---

# Ready-to-append entry for data/SOURCES.md — Pennsylvania

---

## Pennsylvania (data/states/PA.json)

**Pennsylvania Department of State — "Registered Businesses in PA —
Current by County" (Commonwealth open-data portal, Socrata)**
- Dataset page: `https://data.pa.gov/Licenses-Certificates/Registered-Businesses-in-PA-Current-by-County-Depa/xvd7-5r2c`
- API (resource ID `xvd7-5r2c`): `https://data.pa.gov/resource/xvd7-5r2c.json`
- Column metadata: `https://data.pa.gov/api/views/xvd7-5r2c`
- Related filtered view (resource ID `3urc-uaba`):
  `https://data.pa.gov/resource/3urc-uaba.json` — "Filtered View —
  Distinct Registered Businesses in PA". Verified 2026-11-25 to return
  exactly one row per entity (2,386,129 rows = the distinct
  filing-number count in the main dataset), but it is **not used as
  the pull source**: its published columns carry no creation date and
  no raw (source) business name, so `filing_date` and `name_raw`
  cannot be built from it. `build.py` pulls `xvd7-5r2c` and dedupes
  locally instead.
- Publisher: Pennsylvania Department of State, via the Commonwealth
  open-data portal (data.pa.gov).
- Contents: ALL active business registrations in Pennsylvania —
  business corporations, nonprofit corporations, LLCs, limited
  partnerships, LLPs/LLLPs, business trusts, land banks, municipal and
  other authorities — with business name (formatted and as-filed),
  filing number, registered-office city / state / ZIP, county,
  registration type, and creation date. Dissolved or cancelled
  entities are absent; this is a current/active snapshot only, not a
  new-filings feed and not a historical archive.
- Update cadence: **monthly**.
- Access method: Socrata API only, descriptive User-Agent
  (`SecondDesk-FilingsAPI/1.0 (+https://deskside.gumroad.com)`),
  ≥1 s between requests, sequential paginated requests. Automation
  basis: this is an official Socrata open dataset published by the
  Commonwealth for programmatic access. The PA DOS interactive
  entity-search portal is never touched.

## Pennsylvania-specific field notes — read before trusting them

- **Pennsylvania has no registered agents.** Entities file a
  *registered office address* instead of naming a registered agent,
  and this dataset carries officer / organizer / incorporator names
  (Governor/Principal Officer rows) where captured. `registered_agent`
  is therefore **null on every Pennsylvania record (0%)** — this is a
  fact about Pennsylvania law, not missing data. Officer names are
  deliberately **not** mapped into the agent field; the adapter does
  not even request the officer columns.
- **Officer-level duplication:** the source dataset has one row per
  officer position, so one entity can appear many times.
  `build.py` selects entity-level columns only and dedupes by filing
  number (`id`), keeping the first row per entity.
- **County** (`county_or_parish`) is the dataset's own County Name
  field, kept as published. Entities whose registered office is
  outside Pennsylvania carry the county value **"Out of State"** —
  kept as published, never remapped to a real county.
- **Entity type** is normalized from the dataset's explicit
  "Type of Business Registration" field (not a heuristic):
  any `Foreign …` registration → `Foreign Entity`;
  `Domestic Limited Liability Company` → `LLC`;
  `Domestic Business Corporation` → `Corporation`;
  `Domestic Nonprofit Corporation` → `Nonprofit`;
  domestic partnerships (`Domestic Limited Partnership (LP/LLLP)`,
  `Domestic General Partnership (GP/LLP)`) → `Partnership`;
  everything else (`Domestic Business Trust`, `Authority`,
  `Domestic Credit Union`, `Domestic Land Bank`,
  `Domestic Financial Institution`,
  `Domestic Professional Association`) → `Other`.
- **Names:** `name` is the dataset's formatted (title-cased) business
  name; `name_raw` is the "Business Name (Source)" value exactly as
  filed. Both are reproduced as published, including filers' typos.
- **ZIP:** the dataset carries a ZIP, kept when it is a well-formed
  5-digit or ZIP+4 value. Legacy placeholder values (e.g. `0    -0`
  on old registrations) are not well-formed ZIPs and become `null` —
  never guessed.
- **`state` is the filing jurisdiction (`"PA"`)** on every record,
  matching the Louisiana convention; a record's registered-office
  city/county can still be out of state (see County note above).
- **Filing date** (`filing_date`) is the dataset's Creation Date —
  the date the registration was created with the Department of State.
  The snapshot reaches back to the 1700s for still-active
  registrations; it is not limited to recent filings.

## Refresh method

`python3 expansion/builds/PA/build.py` — re-pulls the full current
dataset from the Socrata API, dedupes by filing number, and rewrites
`data/states/PA.json`. Run monthly, after the Commonwealth's monthly
update lands.


---

# Data Sources — Connecticut (ready to append to data/SOURCES.md)

## Connecticut

**Connecticut Secretary of the State — "Connecticut Business Registry" (Connecticut Open Data Portal / data.ct.gov)**
- URLs / dataset IDs: Business Master — `https://data.ct.gov/Business/Connecticut-Business-Registry-Business-Master/n7gp-d28j` (Socrata dataset ID `n7gp-d28j`); Agents ("Agent Details") — `https://data.ct.gov/resource/qh2m-n44y.json` (Socrata dataset ID `qh2m-n44y`, found via the Socrata catalog API at `https://api.us.socrata.com/api/catalog/v1?domains=data.ct.gov&q=Connecticut%20Business%20Registry%20Agent`). The registry is published as five linked datasets (Business Master, Agents, Principals, Filing History, Name Change History); this adapter uses Business Master + Agents. The Secretary of the State's Bulk Data page (`https://portal.ct.gov/SOTS/Business-Services/Bulk-Data-and-Images`) states the registry extract is free and complete.
- Contents: the complete Connecticut Business Registry — every business on the registry (~1.31M records), with business name as published, business type, citizenship (domestic/foreign), registration date, business address (street, city, state, ZIP), and statutory agent details in the linked Agents dataset, joinable to Business Master on the unique key in each dataset's first column (Agents `business_key` = Business Master `id`).
- Update cadence: **nightly** (portal metadata: daily).
- Fields used: Business Master `id` → `id`, `name` → `name` / `name_raw` (as published), `billingcity` (Business_City) → `city`, `billingpostalcode` (Business_ZIP) → `zip`, `date_registration` (Date_Registration) → `filing_date` (YYYY-MM-DD date part), `business_type` + `citizenship` → `entity_type` (normalized, see below); Agents `name__c` (fallback: first + middle + last name) → `registered_agent`. `state` is set to `"CT"` for every record; `source_url` is the Business Master dataset URL above.
- Entity type mapping: `citizenship` = Foreign → **Foreign Entity** regardless of underlying form (mirroring the Louisiana convention for foreign qualifications); domestic LLC → **LLC**; domestic Stock / B Corp / Bank Stock / Insurance Stock / Credit Union Stock / Stock Specially Chartered → **Corporation**; domestic Non-Stock / Non-Stock Specially Chartered / Religious → **Nonprofit** (Connecticut non-stock corporations are the nonprofit form; religious corporations are non-stock); domestic Limited Partnership / LLP / General Partnerships → **Partnership**; everything else (Statutory Trust, Special Chartered, Cemetery, Cooperative Association, Unauthorized, Bank Non-Stock, Insurance Non-Stock, Credit Union Non-Stock, Unknown, and records with no business type such as name reservations) → **Other**.
- Derivations:
  - **County is `null` on every record (`county_or_parish: null`)** — Business Master publishes **no county column**, and county is never guessed from city.
  - **Registered agent**: one agent per business. Agent rows are read in `business_key` order and the first row for a business wins (1,328,174 agent rows over 1,311,479 distinct businesses; duplicate rows observed for a key are repeats of the same agent). Businesses with no agent row get `registered_agent: null`.
  - **Filing date**: 19,720 records carry the source's placeholder date `0001-01-01` (no real registration date, e.g. name reservations); those are emitted as `filing_date: null` rather than a fake year-1 date.
- Automation basis: the state publishes the complete registry **free** on its official open-data portal (data.ct.gov) as Socrata datasets intended for programmatic access, and the Secretary of the State's Bulk Data page confirms the registry extract is free and complete. Access is via the Socrata API only, with a descriptive User-Agent, ≥1 s between requests, and 50,000-row pages. The business.ct.gov interactive search portal is never touched.
- Refresh method: `python3 expansion/builds/CT/build.py` — re-pulls the full Business Master and the full Agents dataset, joins agents by unique key, de-duplicates by business ID, and rewrites `data/states/CT.json`. (An optional `--since YYYY-MM-DD` flag applies a server-side `date_registration >=` filter instead.) Column names were confirmed against the Socrata metadata endpoints `https://data.ct.gov/api/views/n7gp-d28j` and `https://data.ct.gov/api/views/qh2m-n44y` before querying.
- **Enrichment (2026-11-26):** the feed also carries `business_email_address` (813,554 of 1,310,099 records) → stored as **`email_as_filed`** under the as-filed labelling rule above, and `naics_code` / `naics_sub_code` → decomposed to **`naics_code`** (625,411 records) + **`naics_title`** (612,190). `build.py` selects all three natively since 2026-11-26; see `expansion/builds/CT/SOURCES-ENTRY.md` for the full note. A separate `category_survey_email_address` field exists in the feed and is deliberately NOT captured (survey response, not a filed business contact).


---

## Ohio — New Business Entity Reports (added 2026-11-25, adapter built; first live pull pending, see RESULT.md)

**Ohio Secretary of State — "New Business Filing" reports**
- Page: `https://www.ohiosos.gov/business/business-reports`
- File URL pattern: `https://publicfiles.ohiosos.gov/free/OnlineBusinessReports/WI<nnnn>R.TXT`
  — the current file URLs are discovered from the reports page on every run
  (the table is refreshed in place); the November 2026 release links:
  - Foreign Licenses (For-Profit) — `WI0010R.TXT`
  - Foreign Licenses (Nonprofit) — `WI0030R.TXT`
  - Foreign Licenses (Professional) — `WI0040R.TXT`
  - Domestic Corporations (For-Profit) — `WI0050R.TXT`
  - Domestic Corporations (Nonprofit) — `WI0070R.TXT`
  - Domestic Corporations (Professional) — `WI0080R.TXT`
  - Foreign Limited Liability Companies — `WI0090R.TXT`
  - Domestic Limited Liability Companies — `WI0100R.TXT`
  - Foreign Limited Partnerships — `WI0110R.TXT`
  - Domestic Limited Partnerships — `WI0120R.TXT`
- Publisher: Ohio Secretary of State, Business Services Division.
- Contents: every new business entity filed in the release month, split by
  entity type — document number, charter number, effective (filing) date,
  business name, transaction code description, filer name/address, and
  statutory agent name/address. (The landing page prints a slightly
  different field list — "Corporation Name, Statutory Agent Name, Date of
  Incorporation, Statutory Agency Address, Type of Corporation" — the real
  files carry the same information under the structured column names
  above.)
- File format: **comma-separated text (CSV) in a `.TXT` file** — header
  row, double-quoted fields. The landing page does not label the format;
  this was verified from the files themselves. Column sets vary slightly
  by split (foreign-license files add LICENSE TYPE / PRINCIPAL ADDRESS /
  STATE NAME; domestic-corporation files add BUSINESS CITY / COUNTY;
  the LLC files add ACCESS ADDRESS).
- Public status: the reports are documents the Secretary of State itself
  publishes for free public download ("Free Ohio Business Reports …
  available for free download"). No login, no paywall. Phone numbers and
  email addresses do not appear (the SOS does not collect them at filing).
- Update cadence: **monthly** — new reports are generated on the second
  Saturday of each month and the downloadable report table is refreshed
  automatically with the latest data after each monthly update. **Only
  the latest monthly release is published; there is no archive**, so each
  refresh replaces the state dataset wholesale and history cannot be
  backfilled from this source.
- Access method: direct download of the published report files only, with
  a descriptive User-Agent (`SecondDesk-FilingsAPI/1.0
  (+https://deskside.gumroad.com)`), ≥1 s between downloads, sequential
  requests only. The Ohio Business Search portal is never touched.

## Derived fields — Ohio

- **City / ZIP** come from the statutory agent's address columns
  (`AGENT CITY` / `AGENT ZIP`) — the agent's Ohio address. When the agent
  city is blank, the filer's address columns (`FILING CITY` / `FILING
  ZIP`) are used instead. Both are structured columns in the published
  files, so no free-text address parsing is involved. ZIP is truncated to
  5 digits (the files carry ZIP+4 with no dash).
- **County** is `null` on every Ohio record: the published field list has
  no county field and county is never guessed. (The three
  domestic-corporation files do carry an extra `COUNTY` column for the
  business city; it is deliberately not used, to keep the derivation rule
  uniform across all ten splits and consistent with the audit spec.)
- **Entity type** is normalized from the report split, cross-checked
  against the file's TRANSACTION CODE DESCRIPTION: any foreign split →
  `Foreign Entity` (the Louisiana/Colorado convention for foreign
  qualifications); domestic LLC → `LLC`; domestic nonprofit corporation →
  `Nonprofit`; domestic for-profit / professional corporation →
  `Corporation`; domestic limited partnership → `Partnership`; anything
  else → `Other`.
- **Registered agent** is the file's `AGENT ADDRESS NAME` — Ohio's
  statutory agent. The placeholder `--` that appears in the source is
  normalized to `null`.
- **Filing date** is the file's `EFFECTIVE DATE` converted from
  MM/DD/YYYY to YYYY-MM-DD. Note it is the filer's chosen effective date,
  so a monthly release can contain a few dates just outside the release
  month (including near-future effective dates).
- **Names** are reproduced from the filings as published. `name` is
  title-cased for display when the source is ALL-CAPS (the Louisiana
  convention); `name_raw` keeps the original form.

## Refresh method

`python3 expansion/builds/OH/build.py` — discovers the current report
file URLs from the official reports page, downloads all ten New Business
Entity Report files politely, parses and normalizes them, de-duplicates
by charter number, and rewrites `data/states/OH.json`. Run monthly,
after the second-Saturday refresh lands. Because only the latest month
is published, a missed month is lost — do not skip refreshes.


---

# Data Sources — Oregon (ready to append to data/SOURCES.md)

## Oregon

**Oregon Secretary of State, Corporation Division — "Active Businesses - ALL" (data.oregon.gov) + "Active Businesses - County Data" companion**
- URLs / dataset IDs: `https://data.oregon.gov/resource/tckn-sxa6.json` — Socrata resource ID `tckn-sxa6` ("Active Businesses - ALL"), on the official Oregon open-data portal (data.oregon.gov), published by the Secretary of State, Corporation Division (provenance: official). County companion: `https://data.oregon.gov/resource/6g49-bcrm.json` — Socrata resource ID `6g49-bcrm` ("Active Businesses - County Data"), same publisher. Column names were confirmed against the Socrata metadata endpoints `https://data.oregon.gov/api/views/tckn-sxa6` and `https://data.oregon.gov/api/views/6g49-bcrm` before querying.
- Contents: every **active** business registered with the Oregon Secretary of State (dissolved/inactive entities are not in either dataset). `tckn-sxa6` carries **one row per associated name** — Principal Place of Business / Mailing Address / Registered Agent / Authorized Representative — with `registry_number`, `business_name`, `entity_type` (full Secretary of State label), `registry_date`, `associated_name_type`, the associated person's `first_name` / `middle_name` / `last_name` (plus `entity_of_record_name` for organisational agents), address, `city`, `state`, `zip`, and `jurisdiction` (~1.59M rows total; registry dates run back to 1850). This dataset's pull is filtered server-side to `registry_date >= 2024-01-01` (new-business filings only); the full register is not pulled. `6g49-bcrm` carries one row per business whose Principal Place of Business is in Oregon, with `registry_number`, `registry_date`, `business_name`, `city`, `zip`, and `county`; it supports the same `registry_date` filter, which is applied identically before the join.
- Update cadence: **monthly-ish** — the main dataset's metadata lists a weekly publishing/change frequency and its rows are refreshed regularly (rows updated November 2026 at build time); the county companion's metadata lists **monthly** publishing/change frequency, so county coverage can lag the main dataset for the newest filings. Both datasets cover **active businesses only**.
- Fields used: `registry_number` → `id`, `business_name` → `name` / `name_raw` (as published), `registry_date` → `filing_date` (YYYY-MM-DD date part), `entity_type` → `entity_type` (normalized, see below), `city` / `zip` per the precedence below, the REGISTERED AGENT row's name → `registered_agent`, and `county` from `6g49-bcrm` → `county_or_parish`. `state` is set to `"OR"` for every record; `source_url` is the `tckn-sxa6` dataset URL above.
- Entity type mapping (`entity_type` is the full Secretary of State label, applied in this order): any label starting with `FOREIGN` (Foreign LLC / Business Corporation / Nonprofit Corporation / Professional Corporation / Limited Partnership / Registered Limited Liability Partnership / Business Trust) → **Foreign Entity** (mirroring the Louisiana convention for foreign qualifications); labels containing `NONPROFIT` (Domestic Nonprofit Corporation, District Improvement Nonprofit) → **Nonprofit**; labels containing `LIMITED PARTNERSHIP` or `LIMITED LIABILITY PARTNERSHIP` (Domestic Limited Partnership, Domestic Registered Limited Liability Partnership) → **Partnership**; labels containing `LIMITED LIABILITY COMPANY` (Domestic Limited Liability Company) → **LLC**; labels containing `CORPORATION` (Domestic Business Corporation, Domestic Professional Corporation) → **Corporation**; everything else (Assumed Business Name, Cooperative, Domestic Business Trust, Reserved Name, Registered Name, Act of Government, District Improvement Profit, unlisted labels) → **Other**.
- Derivations:
  - **De-duplication by `registry_number`:** `tckn-sxa6` is one row per associated name, so the adapter collapses each business's rows into a single record. Business **city/ZIP precedence**: (1) the Principal Place of Business row, (2) else the Mailing Address row, (3) else any row for the business that carries a city (e.g. a Registered Agent or Authorized Representative row).
  - **Registered agent** is the name on the row where `associated_name_type = REGISTERED AGENT`: `first_name` + `middle_name` + `last_name` combined; when that row carries no person name (an organisational agent), the row's `entity_of_record_name` is used instead. `null` when the business has no Registered Agent row (or the row carries no name at all).
  - **County is state-published, not derived by this adapter:** `6g49-bcrm` is published by the Secretary of State itself, and per its own dataset description the county was **derived by the state from a City-Zip combination** ("The county was determined by a City-Zip combination", based on the Principal Place of Business residing in Oregon only). The adapter only joins it by `registry_number`; `county_or_parish` is `null` only where the join misses — the county dataset covers Oregon principal places of business only and refreshes monthly, so out-of-state principal addresses and the newest filings may have no county row.
- Automation basis: official Socrata open datasets published by the state for programmatic access — access is via the Socrata API only, with a descriptive User-Agent, ≥1 s between requests, and 50,000-row pages. The Oregon Business Registry interactive search portal is never touched.
- Refresh method: `python3 expansion/builds/OR/build.py` — re-pulls all associated-name rows with `registry_date >= 2024-01-01` from `tckn-sxa6` (server-side `$where`), pulls `6g49-bcrm` with the same filter, de-duplicates by registry number, joins county, and rewrites `data/states/OR.json`.


---

# Data Sources — Iowa (ready to append to data/SOURCES.md)

## Iowa

**Iowa Secretary of State — "Active Iowa Business Entities" (Iowa Data Hub / data.iowa.gov, dataset 554)**
- URLs: catalog `https://data.iowa.gov/catalog/dataset/554` — CSV `https://idh-be.iowa.gov/api/v1/datasets/554/rows.csv`, JSON `https://idh-be.iowa.gov/api/v1/datasets/554/rows.json` (the builder uses the CSV endpoint; note it currently returns a ZIP archive containing one CSV file, which the builder handles, as well as a plain-CSV payload). Published by the Iowa Secretary of State on the Iowa Data Hub (data.iowa.gov).
- Contents: all **active** domestic and foreign business entities registered in Iowa — corporation number, legal name, corporation type, effective date, name and address of the registered agent, and address of the principal office (published as the "home office" `ho_*` fields). Unlike the Louisiana seed, this is the full active registry (effective dates in the current pull run 1828-06-07 → 2027-01-01), not a new-filings-only window.
- Update cadence: **monthly**.
- Fields used (headers verified on the real download, 2026-11-25): `corp_number` → `id`, `legal_name` → `name` / `name_raw` (as published), `ho_city` → `city`, `ho_zip` → `zip`, `effective_date` → `filing_date` (published as YYYY-MM-DD), `corporation_type` → `entity_type` (normalized, see below), `registered_agent` → `registered_agent`. `state` is set to `"IA"` for every record; `source_url` is the CSV URL above.
- Entity type mapping (`corporation_type` is `"<code> - <label>"` and the label carries domestic vs foreign; rules applied in order): any label containing **FOREIGN** → **Foreign Entity** (mirroring the Louisiana/Colorado convention for foreign qualifications, regardless of underlying form); domestic labels containing "NON-PROFIT"/"NONPROFIT" → **Nonprofit**; domestic labels containing "PARTNERSHIP" → **Partnership** (checked before LLC); domestic labels containing "LIMITED LIABILITY COMPANY" → **LLC**; domestic "PROFIT" (non-nonprofit) labels and standalone "DOMESTIC PROFESSIONAL" → **Corporation**; everything else (cooperatives/co-ops, Multiple Housing Act, banks, insurance companies, savings & loan, farm corp, county/city authorities, federal filings, unlisted codes) → **Other**.
- Derivations:
  - **County is `null` on every record (`county_or_parish: null`)** — the dataset publishes **no county field**, and county is never guessed.
  - **City** is taken directly from the dataset's explicit principal-office ("home office") city column, `ho_city` — no address parsing is needed or performed; `null` when that column is blank.
  - **ZIP** is the principal-office ZIP, `ho_zip`, reproduced as published.
- Automation basis: official open dataset published by the state on its open-data hub for programmatic access — access is via the published CSV/JSON dataset endpoints only, with a descriptive User-Agent and ≥1 s between requests. The Iowa Secretary of State's interactive business-search portal is never touched, and the paid SOS API is not used.
- Refresh method: `python3 expansion/builds/IA/build.py` — downloads the full published CSV in a single request, validates the headers, de-duplicates by corporation number, and rewrites `data/states/IA.json`.


---

# Data Sources — Alaska (ready to append to data/SOURCES.md)

## Alaska

**Alaska Division of Corporations, Business and Professional Licensing (CBPL, Dept. of Commerce, Community and Economic Development) — "Search and Database Download" hub**
- Hub URL: `https://www.commerce.alaska.gov/cbp/main/`
- File URLs (link targets on the hub, Corporations confirmed 2026-11-25; Agents follows the same hub pattern):
  - Corporations Download: `https://www.commerce.alaska.gov/cbp/main/dbDownload/CorporationsDownload`
  - Agents Download: `https://www.commerce.alaska.gov/cbp/main/dbDownload/AgentsDownload`
  - (Also offered on the hub, not used here: Officials Download, Trademark Download, business/professional license downloads.)
- Contents: the full corporations database as published by the state (entity number, entity name, entity type, formation date, physical/mailing addresses) plus the separate Agents database for registered-agent names, joined on the Alaska entity number.
- Update cadence: **not published** — the hub page states no update frequency, so cadence is unpublished; refresh opportunistically (monthly is a reasonable default) and record the pull date in the envelope's `generated_at`.
- Fields used: entity number → `id`; entity name → `name` / `name_raw` (as published); entity's **physical** address city → `city` (mailing address city as fallback when no physical city is present), physical ZIP → `zip` (mailing ZIP as fallback, ZIP+4 truncated to 5 digits); formation/registration date → `filing_date` (YYYY-MM-DD); entity type → `entity_type` (normalized, see below); Agents file agent name → `registered_agent`. `state` is `"AK"` for every record; `source_url` is the Corporations download URL.
- Entity type mapping (CBPL publishes full type strings; matching is by substring, first match wins): foreign registrations / non-AK home state → **Foreign Entity**; nonprofit → **Nonprofit**; limited liability company / LLC → **LLC**; partnership forms (LP/LLP/LLLP, "partnership") → **Partnership**; corporation / corp / inc → **Corporation**; everything else → **Other**.
- Derivations:
  - **Borough/county:** `county_or_parish` is taken verbatim from a borough/county column **only if the downloaded file actually carries one**; otherwise it is `null` on every record. Alaska has no city→borough mapping in this codebase and boroughs are never guessed. (Whether the file carries such a column is confirmed from the real file headers on the first successful pull — see `expansion/builds/AK/RESULT.md`.)
  - **Registered agent join:** Agents Download joined to Corporations on entity number; if an entity has multiple agent rows, the **first row in file order** is used. If the Corporations file itself carries a registered-agent column, it is used only as a fallback for entities the Agents join misses.
  - **Filing date:** the file's formation/registration date field, parsed to YYYY-MM-DD; `null` if the column or value is absent/unparseable — never invented.
- Automation basis: the state itself publishes the full databases as free CSV downloads on the hub, no login — downloading those published files is the intended bulk route. The CBPL interactive entity-search portal is never touched. Downloads use a descriptive User-Agent (`SecondDesk-FilingsAPI/1.0 (+https://deskside.gumroad.com)`), sequential, ≥1 s apart.
- Access note (2026-11-25): commerce.alaska.gov is behind DataDome; scripted requests from the build VM receive HTTP 403 with a JavaScript device-check interstitial for the hub and both download endpoints. The adapter (`expansion/builds/AK/build.py`) therefore also accepts browser-downloaded copies placed in `expansion/builds/AK/raw/` (`CorporationsDownload.csv`, `AgentsDownload.csv`) and builds from them with no network access. No bot-check bypass is attempted.
- Refresh method: `python3 expansion/builds/AK/build.py` — downloads (or reuses `raw/`) the Corporations + Agents CSVs, joins agents (first row wins), normalizes, and rewrites `data/states/AK.json`.


---

# Data Sources — Mississippi (ready to append to data/SOURCES.md)

## Mississippi

**Mississippi Secretary of State — "Mississippi Business Reports" (corp.sos.ms.gov)**
- URL: `https://corp.sos.ms.gov/corpreporting/Corp/BusinessSearch3` — help document at `https://corp.sos.ms.gov/corpreporting/Corp/ViewHelpDocument`. Official free service, in the Secretary of State's own words: "This free service allows you to conduct a customized search of the Mississippi Secretary of State's corporations database and export your results as a printable Excel report. The database includes all current registered businesses in Mississippi."
- **Caveat (binding): this source is an interactive portal export with no stated automation terms; it was pulled once via the portal's own export function; terms must be re-checked before automating at scale.**
- Contents: all current registered businesses in Mississippi, exported via the portal's own "Export to Excel" function (portal cap: 300,000 results per export; the portal directs anyone needing a full export to Business Services, (601)-359-1633). Export columns: Business Name, Business Id, Other Names, Profile Type, Domicile Type, Status, NAICS Code1–3, Address Line1, City, State Code, County, Postal Code, Formation Date.
- Update cadence: the underlying corporations database is the live register; no refresh cadence is stated on the portal. Any refresh is a fresh manual export, subject to the caveat above — this source is **not** on an automated schedule.
- Fields used: `Business Id` → `id`, `Business Name` → `name` / `name_raw` (title-cased for display only when the filed name is ALL-CAPS; `name_raw` keeps the filed form), `City` → `city`, `County` → `county_or_parish` (Mississippi publishes a real county column — it is used as filed, never derived), `Postal Code` → `zip`, `Formation Date` → `filing_date` (YYYY-MM-DD), `Profile Type` + `Domicile Type` → `entity_type` (normalized, see below). `state` is set to `"MS"` for every record; `source_url` is the Business Reports URL above.
- Entity type mapping: Domicile Type indicating a foreign domicile → **Foreign Entity** (takes precedence, mirroring the Louisiana/Colorado convention for foreign qualifications); otherwise from Profile Type — "Limited Liability Company" / LLC → **LLC**; Nonprofit → **Nonprofit**; Partnership (incl. limited partnership) → **Partnership**; Corporation / Inc. → **Corporation**; anything else or blank → **Other**.
- Derivations and gaps:
  - **Registered agent is `null` on every record** — the Business Reports export has **no registered-agent column**. Registered-agent data exists only in the separate Business Search portal, which is a one-record-at-a-time human lookup; it is not harvested, so the gap is documented rather than filled.
  - County and city are as published in the export (the address is the business's principal address as filed, which can be outside Mississippi for foreign entities; `state: "MS"` still applies because every record is a Mississippi registration).
- Automation basis: **none documented** — see the bold caveat above. The single pull behind this dataset was made through the portal's own public Export to Excel function with a descriptive User-Agent (`SecondDesk-FilingsAPI/1.0 (+https://deskside.gumroad.com)`), no account, no CAPTCHA-solving, and no record-by-record scraping.
- Refresh method: manual only. Re-check the portal's terms first; then either re-run `python3 expansion/builds/MS/build.py` (one export attempt per run, hard-stops if the portal blocks it) or export in a browser session and normalize with `python3 expansion/builds/MS/build.py --normalize-only <export.xlsx>`. Output is the standalone per-state file `data/states/MS.json` in the seed.json envelope format.


---

# Data Sources — Vermont (ready to append to data/SOURCES.md)

## Vermont — NOT BUILT (bulk download is account-gated; documented 2026-11-25)

**Vermont Secretary of State — Business Services "bulk data download" (BizFilings system)**
- Automation basis (testimony): the Secretary of State's written testimony to the Vermont House Committee on Energy and Digital Infrastructure (Jan 13, 2025, hosted on legislature.vermont.gov: `https://legislature.vermont.gov/Documents/2026/Workgroups/House%20Energy%20and%20Digital/Data/W~Lauren%20Hibbert~Transparency%20and%20Accessibility%20of%20SOS%20Data~4-29-2025.pdf`) states Business Services data "is all available to the public, without cost. At this time there are no data feeds, subscriptions, or sales… In addition to individual records, users can download a full data set through a 'bulk data download,' which is updated on a weekly basis." That testimony is the basis for treating this as the sanctioned bulk route — there is no separate data feed, subscription, or sale.
- Where it lives: inside the SOS Business Services system, `https://bizfilings.vermont.gov/` — public entry points are the SOS Business Services page (`https://sos.vermont.gov/business-services/`) and the public business search (`https://bizfilings.vermont.gov/business/businesssearch`). The download itself is the authenticated app's "Data Download" screen (route `bulk-download`, API base `https://api.bizfilings.vermont.gov/api`, list `DataSales/GetBulkDownloadList`, file `POST DataSales/bulkdownloadfile/{recordType}`, saved by the app as `<recordType>.xlsx` per record-type category).
- **Gate (why nothing was built):** the `bulk-download` route sits inside the app's login-guarded SecureModule — unauthenticated visitors are redirected to login, and bulk features are tied to an account's `isBulkFiler` profile flag. The single anonymous pull attempt made on 2026-11-25 returned no file (HTTP 404, empty). No account was created and no login was attempted, per the build rules; see `expansion/builds/VT/RESULT.md` for the full evidence.
- **Caveat (binding):** the file format and fields were uninspected before this build and remain uninspected — the download lives inside the SOS Business Services system and no file was obtained. The `.xlsx`-per-category shape is known only from the app's own client code, not from a real file; whether the workbooks contain city, county, registered-agent, or formation-date columns is **unconfirmed**. Do not write a normalizer or quote field coverage for Vermont until a logged-in user downloads one file and its headers are inspected.
- Update cadence (per testimony): **weekly**.
- Access method (rules for any future build): published-file download only, inside an account the user themselves created/logged into; descriptive User-Agent `SecondDesk-FilingsAPI/1.0 (+https://deskside.gumroad.com)`, ≥1 s pacing, single pull per refresh. The BizFilings entity search must **never** be scraped record-by-record as a substitute for the bulk file.
- Refresh method: none yet — `python3 expansion/builds/VT/build.py` is a no-network stub that reports this gate. It becomes a real builder only after a sample file's headers have been inspected.
