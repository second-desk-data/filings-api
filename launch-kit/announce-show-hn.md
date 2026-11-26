# Show HN Draft — New Business Filings API

Prepared Nov 25, 2026. Post on launch day, after the RapidAPI listing is
live (runbook step g). Fill in the two bracketed links before posting.
Builder voice, honest numbers, no income claims, no prices in the headline.

---

**Title:**

Show HN: I turned 8 states' raw business-filing dumps into a searchable API

**URL:** [LIVE API / RAPIDAPI LISTING URL — from runbook step c]

**Text:**

Every US state publishes its new business filings. Almost none of them
publish them in a form a computer can use. Louisiana, where I live, releases
a plain-text newsletter once a week — thousands of lines, no structure, no
search, and no parish field even though "which parish is this business in"
is the first question anyone local asks. Other states offer bulk registry
downloads measured in gigabytes, or charge for the privilege: one Secretary
of State's bulk-records product runs $2,000 a month, and the private
services built on top of this data mostly sell one state at a time.

I wanted this data for my own lead lists, so I built the unglamorous middle
layer myself:

- Ingestors for 8 states' published filings (weekly text dumps, daily
  files, and full registry downloads), normalized into one schema
- Every record tagged with its parish/county, an industry category, and
  "likely need" segments (needs a website, needs bookkeeping, likely hiring
  drivers)
- A REST API over the whole thing: search by state, region, city, entity
  type, category, need, filing age, and date range, with filters composing
- Per-key geographic scope: a key bound to one city or one state physically
  cannot return records outside it — scope is enforced in counts and
  lookups too, not just in search results

The corpus is 5,724,302 filings: Pennsylvania's full registry (2.39M),
Connecticut (1.31M), New York and Colorado back to 2024, Iowa's active
registry, Oregon, two full years of Louisiana (126,710), and Florida's most
recent ~30 days.

The stack is deliberately boring: Python standard library only — no
dependencies, nothing to pip — with the corpus in a disk-backed SQLite
store, so the server holds no records in RAM and runs flat at a few tens of
MB whether it's answering for one city or all eight states.

Honest limits, because HN will find them anyway: filings data contains no
phone numbers or emails — the source filings simply don't have them, and I
don't append or invent any. Industry categories are inferred from business
names (filings carry no industry code), and about 64% of Louisiana filings
have names too generic to classify; those are served as "Other /
Unclassified" rather than force-fitted. Florida coverage is thin so far
(~30 days) because that's what the state makes freely available on a daily
cadence. The API is hosted on a free tier that sleeps when idle, so the
first request after a quiet spell takes a few seconds.

Pricing is scoped to geography (a free taste tier, then per-city and
per-state plans) — details on the listing page: [RAPIDAPI LISTING URL]

I'm a customer-service specialist by trade, not a data company; this grew
out of selling CSV extracts of the same data and having buyers ask for
something fresher and searchable. Happy to answer questions about the
ingestion mess — every state publishes in a different format, and two of
them were interesting fights.

---

**Posting notes (delete before posting):**
- Post Tue–Thu morning US time for the best HN window.
- First comment from us: a concrete before/after — one raw line from a
  Louisiana NewCorp newsletter next to the JSON the API returns for the
  same filing.
- Stay in the thread for the first 3–4 hours; answer the data-honesty
  questions head-on (they will come).
- Do not post until the live URL in runbook step b passes its verification
  checks — a sleeping free-tier host plus HN traffic is a bad first
  impression; warm the service right before posting.
