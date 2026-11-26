# Indie Hackers Launch Post Draft — New Business Filings API

Prepared Nov 25, 2026. Post on launch day, after the RapidAPI listing is
live (runbook step g). Fill in the bracketed links before posting. Builder
voice, honest numbers, no income claims, no prices in the headline.

---

**Title:**

I built an API out of the messiest public data in America: state business filings

**Body:**

**The problem I kept running into**

I sell data products for a living on the side — CSV packs of new business
filings, sliced by industry and city, for people who sell to brand-new
businesses (web designers, insurers, bookkeepers, lenders). The data itself
is public. Every Secretary of State publishes new filings. But "publishes"
is doing a lot of work in that sentence: Louisiana puts out a plain-text
newsletter once a week with no structure and no parish field. Other states
offer multi-gigabyte registry downloads, or sell bulk access themselves —
one state charges $2,000/month for it. The existing private services are
mostly one state at a time, priced like enterprise software.

Buyers of my CSV packs kept asking the same question: "Can I just query
this? Fresher, and filtered my way?" So I built the API version of my own
product.

**What it is**

The New Business Filings API: 5,724,302 filings across 8 states (LA, PA,
CT, NY, CO, IA, OR, FL), cleaned, tagged with parish/county, an industry
category, and likely-need segments, and searchable by every filter at once
— state, region, city, category, need, filing age, date range. There's a
per-key scope system I'm quietly proud of: a subscriber who pays for one
city gets a key that *cannot* return anything outside that city. Not
"shouldn't" — the scope is enforced in search, in counts, and in
single-record lookups.

**How it's built**

Boring on purpose. Python standard library only, zero dependencies, and a
disk-backed SQLite store so the server holds nothing in memory — it runs
flat on a free-tier host. The classification rules are shared with the CSV
packs, so the API and the packs can never disagree about what counts as
"construction."

Total infrastructure spend so far: $0. That's a real constraint, not a
flex — it forced the architecture to stay simple.

**The honest parts**

- Filings data has no phone numbers or emails. They don't exist in the
  source, and I don't append or invent them. This API is for finding *which
  businesses just formed and where*, not for contact lists.
- Industry is inferred from business names, because filings carry no
  industry code. Roughly 64% of Louisiana names are too generic to
  classify, and I'd rather label those "Other" than fake precision.
- Florida is only the most recent ~30 days — that's all the state gives
  away daily for free. The full registry is a 1.79 GB quarterly file I'm
  still working through.
- Revenue so far: this is a launch post, so — none yet from the API. The
  CSV packs it grew out of are live on my store [STORE LINK]. I'll report
  back with real numbers, good or bad.

**Pricing, since IH always asks**

Scoped by geography, matched to the low end of what comparable services
charge: a free tier (50 requests/day), one city for $29/mo, one full state
for $59/mo, with per-request overage. The bet is that a web designer in
Baton Rouge will pay for Baton Rouge — not for a national firehose.

**What's next**

More states (only 12 of the 50 publish data in a form that's free and
legal to build on — I audited all 50), an OpenAPI spec for the directory
listings, and a weekly refresh that already runs for Louisiana.

API listing: [RAPIDAPI LISTING URL] · Live API: [LIVE API URL]

If you sell to new businesses, or you've ever wrestled a state website into
giving up its data, I'd love your feedback — especially on the pricing
scope idea.

---

**Posting notes (delete before posting):**
- Post after the Show HN post or on a different day — don't fire both into
  the same morning; IH rewards the story, HN rewards the tech.
- Add the product to our Indie Hackers product page (created in the
  distribution sweep) and link it from the post.
- Reply to every comment on day one; the pricing-scope question is the
  one most likely to get useful pushback.
