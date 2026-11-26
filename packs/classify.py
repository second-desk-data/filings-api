#!/usr/bin/env python3
"""
classify.py — industry classifier + need-segment builder + CSV pack generator
for the Louisiana New Business Filings dataset (../data/seed.json).

WHAT THIS DOES
--------------
The Secretary of State filings carry NO industry code. This module infers an
industry category from keywords in the business NAME, maps industries to
"likely need" marketing segments, and slices filings into age windows — then
builds the pre-made CSV packs sold alongside the API. Filtered packs are
MONTHLY SUBSCRIPTIONS (buyer receives the refreshed list every month):
$75/mo full-state filtered segment, $29/mo city/parish-level, $15/mo mini
lists — see pack_price().

HONESTY RULES (do not weaken these)
-----------------------------------
* Industry is INFERRED FROM THE NAME ONLY. It is a keyword guess, never a
  verified fact about what the business does. Every pack cover sheet and the
  catalog say so.
* Need segments are "likely needs" inferred from business type (a new
  trucking company almost always needs drivers and insurance; a new
  restaurant almost always needs a POS). They are NEVER described as
  verified needs.
* Anything whose name matches no keyword set is "Other / Unclassified" —
  it is never force-fitted into a category.

AGE WINDOWS
-----------
Windows are computed against the dataset's most recent filing date
(the "as of" date), because the dataset is a weekly-refreshed snapshot:
    age_days(record) = (max_filing_date - record.filing_date).days
    "last N days"    = age_days < N
Supported slices: last 30 / 60 / 90 / 180 days, last 12 months (365),
and calendar years (filing_date.year == YYYY).

USAGE
-----
    python3 classify.py stats     # print category / segment / window counts
    python3 classify.py build     # generate packs/, PACK-CATALOG.md, BUILD-LOG.md

Stdlib only, like the rest of this project.
"""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED_PATH = HERE.parent / "data" / "seed.json"

MIN_PACK_RECORDS = 100      # slices under this are skipped (and logged)
MAX_PACKS = 50              # batch cap ("~40 packs" per the product brief;
                            # the brief's own slice list yields 62 qualifying
                            # candidates, so the cap is set at 50 to honor
                            # every slice type — all cuts are logged)
# Filtered-pack prices are MONTHLY SUBSCRIPTION prices (Jeremy,
# Nov 25, 22:14): the buyer receives the refreshed list every month.
# As-is packs (states/build_full_packs.py) and FMCSA packs keep their
# one-time prices and do NOT use these constants.
PACK_PRICE_STATEWIDE = 75   # $/month — full-state filtered segment
PACK_PRICE_CITY = 29        # $/month — city/parish-level filtered pack
PACK_PRICE_MINI = 15        # $/month — mini lists
PACK_PRICE = PACK_PRICE_MINI  # legacy alias: smallest filtered tier


def pack_price(scope_text, window_text, count, kind=None):
    """Monthly subscription price for one FILTERED filings pack.

    Jeremy, Nov 25, 22:14 — filtered packs are monthly memberships
    (the refreshed list is delivered every month), priced by scope:
      * $75/mo — full-state filtered segment (one industry or
        likely-need filter across the whole state)
      * $29/mo — city/parish-level filtered pack (one city or
        parish, any window)
      * $15/mo — mini lists (city x industry, city x need, parish x
        industry/need, 30-day slices, and any filtered slice smaller
        than a full city list)
    Comp basis: full-state filtered subscriptions in the market run
    $49-$99/mo (New Business Alerts $99/mo state all-industries,
    $49/mo state one-industry; FirstFiling $59/mo state), and the
    city/county tier matches FirstFiling's $29/mo per county.
    """
    if kind and (kind.startswith("Mini")
                 or "city-industry" in kind.lower()
                 or "parish-industry" in kind.lower()):
        return PACK_PRICE_MINI
    if window_text == "Last 30 Days":
        return PACK_PRICE_MINI
    if scope_text.startswith("Statewide"):
        return PACK_PRICE_STATEWIDE
    if scope_text.startswith(("City", "Parish")):
        # A city/parish-scope 12-month pack is a geo x industry slice
        # (a mini list); the city/parish packs themselves run 90 days.
        if window_text == "Last 12 Months":
            return PACK_PRICE_MINI
        return PACK_PRICE_CITY
    return PACK_PRICE_MINI


def onetime_price(monthly):
    """One-time snapshot price for a pack: monthly x 1.20, rounded to
    the nearest dollar (Jeremy, Nov 25, 22:17-22:18). EVERY pack is
    sold both ways — a monthly membership (refreshed list every
    month) and a one-time frozen snapshot that is never updated —
    and the one-time edition costs 20% more than the monthly price.
    """
    return int(monthly * 1.2 + 0.5)


def price_line(monthly):
    """The **Price:** cover-sheet line carrying both editions."""
    return ("**Price:** $%d/mo (monthly membership — refreshed list "
            "every month, cancel anytime) · $%d one-time (frozen "
            "snapshot as of the build date, never updated)"
            % (monthly, onetime_price(monthly)))

OTHER = "Other / Unclassified"

# --------------------------------------------------------------------------
# Industry keyword rules.
#
# HOW MATCHING WORKS
#   * The business name is normalized: lowercased, "&" -> " and ",
#     punctuation stripped, and trailing entity suffixes (LLC, Inc, Corp,
#     ...) removed so suffix words never trigger a rule.
#   * Each rule is a regex matched on WORD BOUNDARIES against the normalized
#     name (so "market" does not match "marketing", "car" does not match
#     "carpet").
#   * Categories are tried in the PRIORITY ORDER below — first match wins.
#     Specific beats generic: e.g. "child care" is tested (Childcare) before
#     any bare "care" phrase in Health; "car care" hits Auto before Health
#     could see it; Trucking is first because transport names often contain
#     words other categories also use ("Delta Food Transport" is a trucking
#     company, not a restaurant).
# --------------------------------------------------------------------------

INDUSTRY_RULES = [
    ("Trucking & Transport", [
        r"trucking", r"trucks?", r"transport(ation|s|ing)?", r"logistics",
        r"hauling", r"haulers?", r"freight", r"hot ?shot", r"expedit\w*",
        r"couriers?", r"towing", r"tow truck", r"moving", r"movers",
        r"dispatch", r"delivery", r"carriers?", r"drayage", r"intermodal",
    ]),
    ("Food & Restaurants", [
        r"restaurants?", r"cafe", r"grills?", r"bbq", r"barbecue", r"barbeque",
        r"kitchens?", r"pizza", r"pizzeria", r"tacos?", r"burritos?",
        r"burgers?", r"seafood", r"baker(y|ies)", r"baking", r"coffee",
        r"espresso", r"diner", r"eatery", r"catering", r"caterers?",
        r"bistro", r"smokehouse", r"crawfish", r"po ?boys?", r"snowballs?",
        r"sno ?balls?", r"daiquiris?", r"brewer(y|ies|ing)", r"distill\w*",
        r"winer(y|ies)", r"vineyards?", r"foods?", r"food ?trucks?",
        r"cuisine", r"chicken", r"wings", r"sushi", r"ramen", r"donuts?",
        r"doughnuts?", r"ice cream", r"creamer(y|ies)", r"juice",
        r"smoothies?", r"taqueria", r"cantina", r"steakhouse",
    ]),
    ("Construction & Trades", [
        r"construction", r"contractors?", r"contracting", r"builders?",
        r"roofing", r"roofs?", r"plumb\w*", r"electric\w*", r"hvac",
        r"air conditioning", r"heating", r"cooling", r"concrete",
        r"remodel\w*", r"renovations?", r"flooring", r"floors",
        r"painting", r"painters?", r"drywall", r"excavat\w*", r"grading",
        r"welding", r"carpentry", r"handyman", r"insulation", r"siding",
        r"fencing", r"paving", r"demolition", r"foundations?", r"framing",
        r"masonry", r"tile", r"granite", r"cabinets?", r"restoration",
        r"sheet metal", r"pipeline", r"cranes?", r"earthworks",
        r"land clearing", r"improvements",
    ]),
    ("Auto Services", [
        r"auto", r"automotive", r"tires?", r"mechanics?", r"motors?",
        r"collision", r"detailing", r"detailers?", r"lube", r"transmissions?",
        r"mufflers?", r"car wash", r"vehicles?", r"garage", r"diesel",
        r"autos", r"car care", r"body shop", r"paint and body",
    ]),
    ("Beauty & Personal Care", [
        r"salons?", r"barbers?", r"barbershop", r"nails?", r"spas?",
        r"beauty", r"hair", r"lash(es)?", r"brows?", r"esthetics?",
        r"aesthetics?", r"massage", r"tanning", r"makeup", r"glam",
        r"braids?", r"waxing", r"skincare", r"skin care",
    ]),
    ("Pet Services", [
        r"pets?", r"dogs?", r"doggy", r"pupp(y|ies)", r"canine",
        r"kennels?", r"paws?", r"veterinary", r"animal hospital",
        r"pet care", r"dog training", r"bark", r"animals?",
    ]),
    ("Childcare & Education", [
        r"child ?care", r"day ?care", r"learning", r"academ(y|ies)",
        r"tutoring", r"tutors?", r"schools?", r"pre ?school", r"kids",
        r"kidz", r"children", r"childrens", r"education\w*", r"montessori",
    ]),
    ("Health, Wellness & Fitness", [
        r"health ?care", r"health", r"wellness", r"fitness", r"gyms?",
        r"yoga", r"pilates", r"crossfit", r"therap(y|ies|eutic)",
        r"counsel(l)?ing", r"medical", r"dental", r"dentistry",
        r"clinics?", r"pharmac(y|ies)", r"chiropract\w*", r"hospice",
        r"home care", r"homecare", r"home health", r"rehab\w*",
        r"nutrition", r"behavioral", r"mental health", r"psychiatr\w*",
        r"dermatology", r"optical", r"vision", r"hearing", r"senior care",
        r"assisted living", r"nursing", r"personal training", r"athletic\w*",
    ]),
    ("Cleaning Services", [
        r"cleaning", r"janitorial", r"maids?", r"pressure washing",
        r"power washing", r"housekeeping", r"spotless", r"tidy",
    ]),
    ("Lawn & Landscaping", [
        r"lawns?", r"lawn care", r"landscap\w*", r"turf", r"trees?",
        r"tree service", r"irrigation", r"yards?", r"mowing", r"grass",
        r"grounds",
    ]),
    ("Real Estate & Property", [
        r"realty", r"real estate", r"propert(y|ies)", r"homes",
        r"home ?buyers", r"house buyers", r"buy houses", r"housing",
        r"apartments?", r"rentals?", r"estates?", r"flips?", r"flipping",
    ]),
    ("Retail & Shopping", [
        r"boutiques?", r"shops?", r"shoppe", r"stores?", r"retail",
        r"gifts?", r"clothing", r"apparel", r"fashion", r"jewelry",
        r"jewelers?", r"furniture", r"mattress(es)?", r"vape", r"smoke",
        r"tobacco", r"liquor", r"spirits", r"suppl(y|ies)", r"markets?",
        r"mart", r"outlet", r"emporium",
    ]),
    ("Professional Services", [
        r"law", r"legal", r"attorneys?", r"accounting", r"accountants?",
        r"cpa", r"bookkeeping", r"tax", r"consult\w*", r"advisors?",
        r"advisers?", r"advisory", r"financial", r"finance", r"insurance",
        r"mortgages?", r"lending", r"loans?", r"title", r"notary",
        r"marketing", r"advertising", r"staffing", r"recruiting",
        r"payroll", r"wealth", r"strategy", r"strategies",
    ]),
    ("Technology & IT", [
        r"technolog(y|ies)", r"tech", r"software", r"digital", r"cyber\w*",
        r"computers?", r"data", r"ai", r"artificial intelligence",
        r"systems", r"cloud", r"web design", r"innovations?",
    ]),
    ("Events & Entertainment", [
        r"events?", r"entertainment", r"part(y|ies)", r"djs?",
        r"photograph(y|ers?)", r"videograph(y|ers?)", r"productions?",
        r"photo booth", r"inflatables?", r"weddings?", r"music",
    ]),
    ("Agriculture & Outdoors", [
        r"farms?", r"farming", r"ranch\w*", r"agricultur\w*", r"timber",
        r"forestry", r"logging", r"cattle", r"livestock", r"equine",
        r"horses?", r"fishing", r"fisheries", r"hunting", r"outdoors?",
    ]),
]

INDUSTRY_NAMES = [name for name, _ in INDUSTRY_RULES] + [OTHER]

_COMPILED_RULES = [
    (name, [re.compile(r"\b(?:%s)\b" % pat) for pat in patterns])
    for name, patterns in INDUSTRY_RULES
]

_ENTITY_SUFFIXES = {
    "llc", "inc", "corp", "corporation", "ltd", "co", "company", "llp",
    "lp", "lllp", "pc", "pllc", "inc.", "llc.",
}


def normalize_name(name):
    """Lowercase, strip punctuation and trailing entity suffixes."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = s.split()
    while tokens and tokens[-1] in _ENTITY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def classify_industry(name):
    """Return (industry, matched_keyword). First matching category wins."""
    norm = normalize_name(name)
    if not norm:
        return OTHER, None
    padded = " %s " % norm
    for cat, patterns in _COMPILED_RULES:
        for pat in patterns:
            m = pat.search(padded)
            if m:
                return cat, m.group(0).strip()
    return OTHER, None


# --------------------------------------------------------------------------
# Need segments — "likely needs" inferred from business type.
# Each segment: (segment name, slug label, rule description, predicate).
# Predicates receive a classified record dict with keys:
#   industry, entity_type
# --------------------------------------------------------------------------

def _ind(*cats):
    return lambda r: r["industry"] in cats


NEED_SEGMENTS = [
    (
        "Needs a Website",
        "needs-website",
        "Every new business in the dataset — a business filing almost "
        "always precedes (or coincides with) launching a web presence.",
        lambda r: True,
    ),
    (
        "Needs a Phone & Internet Setup",
        "needs-phone-internet",
        "Every new business in the dataset — new entities need business "
        "phone and internet service at formation.",
        lambda r: True,
    ),
    (
        "Needs Bookkeeping",
        "needs-bookkeeping",
        "All LLCs and Corporations — entity types with real bookkeeping "
        "and tax-filing obligations from day one.",
        lambda r: r["entity_type"] in ("LLC", "Corporation"),
    ),
    (
        "Needs Commercial Insurance",
        "needs-commercial-insurance",
        "Trucking & Transport, Construction & Trades, and Auto Services — "
        "industries where commercial cover is typically required to operate.",
        _ind("Trucking & Transport", "Construction & Trades", "Auto Services"),
    ),
    (
        "Needs Signage",
        "needs-signage",
        "Brick-and-mortar types — Food & Restaurants, Retail & Shopping, "
        "Beauty & Personal Care, Auto Services — that typically open a "
        "physical location needing signs.",
        _ind("Food & Restaurants", "Retail & Shopping",
             "Beauty & Personal Care", "Auto Services"),
    ),
    (
        "Needs a POS System",
        "needs-pos-system",
        "Food & Restaurants and Retail & Shopping — businesses that take "
        "in-person payments and typically need a point-of-sale system.",
        _ind("Food & Restaurants", "Retail & Shopping"),
    ),
    (
        "Needs Hiring Help",
        "needs-hiring-help",
        "Food & Restaurants, Construction & Trades, and Cleaning Services "
        "— labor-intensive industries that typically hire early and often.",
        _ind("Food & Restaurants", "Construction & Trades", "Cleaning Services"),
    ),
    (
        "Likely Hiring CDL Drivers",
        "likely-hiring-cdl-drivers",
        "Every business classified Trucking & Transport — a newly filed "
        "trucking company almost always needs CDL drivers. 'Likely' is "
        "in the name on purpose: hiring intent is inferred from the "
        "business type, never verified.",
        _ind("Trucking & Transport"),
    ),
]

NEED_NAMES = [name for name, _, _, _ in NEED_SEGMENTS]

# --------------------------------------------------------------------------
# Formation-mill registered agents — the agent_is_commercial flag.
#
# A large share of new filings are created through commercial formation
# services / registered-agent mills: the "business" is real, but the
# registered agent is a factory, the address is the mill's, and the
# filing is noise for most lead buyers (competitors advertise removing
# exactly this). Records whose agent name matches a known mill get
# `agent_is_commercial: true`. The flag is ADDITIVE and NAME-MATCHED
# ONLY: it never drops, blanks, or alters the underlying record, and it
# says nothing verified about the business itself — only that its
# agent's name matches the list below.
#
# Matching is conservative: the agent name is uppercased and stripped
# of punctuation, then tested against the mill patterns (substrings,
# plus "CSC" as a standalone token — CSC is Corporation Service
# Company's short name and appears bare on many filings).
# --------------------------------------------------------------------------

MILL_AGENT_PATTERNS = [
    ("Registered Agents Inc", ["REGISTERED AGENTS INC"]),
    ("Northwest Registered Agent", ["NORTHWEST REGISTERED AGENT"]),
    ("CSC / Corporation Service Company",
     ["CORPORATION SERVICE COMPANY", "CSC GLOBAL"]),
    ("LegalZoom", ["LEGALZOOM"]),
    ("ZenBusiness", ["ZENBUSINESS"]),
    ("Incfile / Bizee", ["INCFILE", "BIZEE"]),
    ("Cogency Global", ["COGENCY GLOBAL"]),
    ("CT Corporation", ["CT CORPORATION", "C T CORPORATION"]),
    ("Paracorp", ["PARACORP"]),
]


def _normalize_agent_name(name):
    return re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()).strip()


def agent_is_commercial(agent_name):
    """True when a registered-agent name matches a known formation mill
    (see MILL_AGENT_PATTERNS). Name-matched only, never verified."""
    flat = _normalize_agent_name(agent_name)
    if not flat:
        return False
    if " CSC " in (" %s " % flat):
        return True
    return any(pat in flat for _label, pats in MILL_AGENT_PATTERNS
               for pat in pats)


def record_agent_is_commercial(record):
    """agent_is_commercial over a record's registered_agent + agents."""
    names = [record.get("registered_agent")]
    names += list(record.get("agents") or [])
    return any(agent_is_commercial(n) for n in names if n)


# --------------------------------------------------------------------------
# Age windows
# --------------------------------------------------------------------------

AGE_WINDOWS = [30, 60, 90, 180, 365]  # supported "last N days" slices


def load_records():
    with open(SEED_PATH) as f:
        seed = json.load(f)
    records = seed["records"]
    for r in records:
        r["_date"] = date.fromisoformat(r["filing_date"])
    as_of = max(r["_date"] for r in records)
    for r in records:
        industry, keyword = classify_industry(r["name"])
        r["industry"] = industry
        r["_keyword"] = keyword
        r["_age_days"] = (as_of - r["_date"]).days
    return records, as_of, seed.get("generated_at")


def in_window(record, days):
    return record["_age_days"] < days


def in_year(record, year):
    return record["_date"].year == year


# --------------------------------------------------------------------------
# Pack building
# --------------------------------------------------------------------------

CSV_FIELDS = ["name", "city", "parish", "type", "filing_date",
              "registered_agent", "industry_category"]


def slugify(text):
    s = text.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def window_label(days):
    if days == 365:
        return "Last 12 Months"
    return "Last %d Days" % days


def window_slug(days):
    if days == 365:
        return "last-12-months"
    return "last-%d-days" % days


# Buyer-facing display names for catalog titles.
CATALOG_CATEGORY_LABEL = {
    "Food & Restaurants": "Restaurants & Food Businesses",
    "Trucking & Transport": "Trucking & Transport Businesses",
    "Construction & Trades": "Construction & Trades Businesses",
    "Auto Services": "Auto Service Businesses",
    "Beauty & Personal Care": "Beauty & Personal Care Businesses",
    "Pet Services": "Pet Service Businesses",
    "Childcare & Education": "Childcare & Education Businesses",
    "Health, Wellness & Fitness": "Health, Wellness & Fitness Businesses",
    "Cleaning Services": "Cleaning Service Businesses",
    "Lawn & Landscaping": "Lawn & Landscaping Businesses",
    "Real Estate & Property": "Real Estate & Property Businesses",
    "Retail & Shopping": "Retail & Shopping Businesses",
    "Professional Services": "Professional Service Businesses",
    "Technology & IT": "Technology & IT Businesses",
    "Events & Entertainment": "Events & Entertainment Businesses",
    "Agriculture & Outdoors": "Agriculture & Outdoors Businesses",
    OTHER: "New Businesses (All Types)",
}


def write_pack(folder, slug, title, rows, window_text, date_span, scope_text,
               segment_note=None, kind=None):
    """Write one pack folder: <slug>.csv + README.md cover sheet."""
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / ("%s.csv" % slug)
    rows_sorted = sorted(rows, key=lambda r: r["_date"], reverse=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows_sorted:
            w.writerow({
                "name": r["name"],
                "city": r.get("city") or "",
                "parish": r.get("parish") or "",
                "type": r.get("entity_type") or "",
                "filing_date": r["filing_date"],
                "registered_agent": r.get("registered_agent") or "",
                "industry_category": r["industry"],
            })
    lines = [
        "# %s" % title,
        "",
        price_line(pack_price(scope_text, window_text,
                              len(rows_sorted), kind)),
        "**Records:** %d businesses" % len(rows_sorted),
        "**Coverage:** %s" % scope_text,
        "**Filing window:** %s (%s)" % (window_text, date_span),
        "",
        "## What's in the CSV",
        "",
        "One row per business, newest filing first:",
        "",
        "- `name` — business name as filed (display-cased)",
        "- `city` / `parish` — mailing city from the filing; parish is "
        "derived from the city (see honesty notes)",
        "- `type` — entity type (LLC, Corporation, Nonprofit, Foreign, Other)",
        "- `filing_date` — the date the filing was recorded",
        "- `registered_agent` — registered agent named in the public filing",
        "- `industry_category` — the category this pack was sliced by",
        "",
        "## How this pack was built",
        "",
        "Source: Louisiana Secretary of State \"NewCorp\" weekly newsletters "
        "— the state's own published list of new business filings (public "
        "records). Records were cleaned, de-duplicated by charter number, "
        "and sliced to the coverage and window above.",
    ]
    if segment_note:
        lines += ["", segment_note]
    lines += [
        "",
        "## Honesty notes — please read",
        "",
        "- **Industry is inferred from the business name** using keyword "
        "rules (e.g. a name containing \"trucking\" or \"hauling\" is "
        "classified Trucking & Transport). It is a well-tested guess, not a "
        "verified industry code — the underlying filings carry no industry "
        "field. Expect most rows to be right and some to be wrong.",
        "- **Need segments are \"likely needs.\"** They map business types "
        "to what a new business of that type typically needs next. No "
        "business in this file has been contacted or verified to need "
        "anything.",
        "- **Parish is derived from the mailing city**, not printed in the "
        "source; a small share of records has no parish and shows blank.",
        "- Names and addresses are reproduced from the public filings as "
        "published, including the filers' own typos.",
        "- This edition is built from filings recorded in the window "
        "above, from the dataset as of its most recent filing date. "
        "Your membership refreshes it monthly with the newest "
        "filings; the API subscription is the always-current version "
        "of this data.",
        "",
    ]
    (folder / "README.md").write_text("\n".join(lines))
    return len(rows_sorted)


def date_span(rows, as_of, days):
    if not rows:
        return "no filings"
    lo = min(r["_date"] for r in rows)
    hi = max(r["_date"] for r in rows)
    return "filed %s to %s" % (lo.isoformat(), hi.isoformat())


def build():
    records, as_of, generated_at = load_records()
    packs_dir = HERE
    generated = []   # dicts: slug, title, count, kind, window
    skipped = []     # (description, reason)

    candidates = []  # (tier, -count, slug, title, rows, window_text, scope, note)

    # --- Tier 1: statewide industry packs (last 90 days + last 12 months) --
    by_industry = defaultdict(list)
    for r in records:
        by_industry[r["industry"]].append(r)
    for cat in INDUSTRY_NAMES:
        rows_all = by_industry.get(cat, [])
        for days in (90, 365):
            rows = [r for r in rows_all if in_window(r, days)]
            label = CATALOG_CATEGORY_LABEL[cat]
            title = "New %s in Louisiana — Filed in the %s" % (
                label, window_label(days))
            if cat == OTHER:
                title = ("New Louisiana Businesses (All Types) — Filed in "
                         "the %s" % window_label(days))
            slug = "la-%s-%s" % (slugify(cat), window_slug(days))
            if len(rows) >= MIN_PACK_RECORDS:
                candidates.append((1, -len(rows), slug, title, rows,
                                   window_label(days), "Statewide — Louisiana",
                                   None))
            else:
                skipped.append(("%s (%s)" % (title, len(rows)),
                                "only %d records (< %d minimum)"
                                % (len(rows), MIN_PACK_RECORDS)))

    # --- Tier 2: need-segment packs (statewide, last 90 days; CDL + 12mo) --
    for seg_name, seg_slug, seg_desc, pred in NEED_SEGMENTS:
        rows_all = [r for r in records if pred(r)]
        windows = [90, 365] if seg_name == "Likely Hiring CDL Drivers" else [90]
        for days in windows:
            rows = [r for r in rows_all if in_window(r, days)]
            title = ("Louisiana New Businesses %s — Filed in the %s"
                     % (seg_name, window_label(days)))
            slug = "la-%s-%s" % (seg_slug, window_slug(days))
            note = ("**Segment definition:** %s This is a *likely-need* "
                    "segment inferred from business type — see the honesty "
                    "notes below." % seg_desc)
            if len(rows) >= MIN_PACK_RECORDS:
                candidates.append((2, -len(rows), slug, title, rows,
                                   window_label(days), "Statewide — Louisiana",
                                   note))
            else:
                skipped.append(("%s (%s)" % (title, len(rows)),
                                "only %d records (< %d minimum)"
                                % (len(rows), MIN_PACK_RECORDS)))

    # --- Tier 3: top-5 city packs -------------------------------------------
    city_counts = Counter((r.get("city") or "").strip() for r in records
                          if (r.get("city") or "").strip())
    top_cities = [c for c, _ in city_counts.most_common(5)]
    for city in top_cities:
        rows_city = [r for r in records
                     if (r.get("city") or "").strip() == city]
        rows90 = [r for r in rows_city if in_window(r, 90)]
        title = ("New Businesses in %s, Louisiana — Filed in the Last "
                 "90 Days" % city)
        slug = "%s-all-new-businesses-last-90-days" % slugify(city)
        if len(rows90) >= MIN_PACK_RECORDS:
            candidates.append((3, -len(rows90), slug, title, rows90,
                               "Last 90 Days", "City — %s, Louisiana" % city,
                               None))
        else:
            skipped.append(("%s (%s)" % (title, len(rows90)),
                            "only %d records (< %d minimum)"
                            % (len(rows90), MIN_PACK_RECORDS)))
        # City's 3 biggest real industries, last 12 months.
        # (Other / Unclassified is excluded here — it is not an industry.)
        rows12 = [r for r in rows_city if in_window(r, 365)]
        cat_counts = Counter(r["industry"] for r in rows12
                             if r["industry"] != OTHER)
        for cat, _cnt in cat_counts.most_common(3):
            rows = [r for r in rows12 if r["industry"] == cat]
            label = CATALOG_CATEGORY_LABEL[cat]
            title = ("New %s in %s, Louisiana — Filed in the Last 12 Months"
                     % (label, city))
            slug = "%s-%s-last-12-months" % (slugify(city), slugify(cat))
            if len(rows) >= MIN_PACK_RECORDS:
                candidates.append((3, -len(rows), slug, title, rows,
                                   "Last 12 Months",
                                   "City — %s, Louisiana" % city, None))
            else:
                skipped.append(("%s (%s)" % (title, len(rows)),
                                "only %d records (< %d minimum)"
                                % (len(rows), MIN_PACK_RECORDS)))

    # --- Apply the batch cap, biggest packs first within each tier ---------
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    chosen = candidates[:MAX_PACKS]
    for c in candidates[MAX_PACKS:]:
        skipped.append((c[3], "cut by the ~%d-pack batch cap (%d records)"
                        % (MAX_PACKS, -c[1])))

    for tier, negcount, slug, title, rows, wtext, scope, note in chosen:
        folder = packs_dir / slug
        span = date_span(rows, as_of, None)
        kind = {1: "Industry", 2: "Need segment", 3: "City"}[tier]
        n = write_pack(folder, slug, title, rows, wtext, span, scope, note,
                       kind=kind)
        assert n == -negcount, (slug, n, -negcount)
        price = pack_price(scope, wtext, n, kind)
        generated.append({
            "slug": slug, "title": title, "count": n, "window": wtext,
            "scope": scope, "price": price,
            "price_monthly": price, "price_onetime": onetime_price(price),
            "billing": "monthly membership + one-time snapshot",
            "price_label": "$%d/mo · $%d one-time"
                           % (price, onetime_price(price)),
            "kind": kind,
        })

    # --- PACK-CATALOG.md ----------------------------------------------------
    cat_lines = [
        "# Pack Catalog — Louisiana New Business Filings, Sliced Your Way",
        "",
        "Pre-made CSV lists of new Louisiana business filings, sliced "
        "the way lead buyers actually think: by **age**, by "
        "**industry**, by **city**, and by **likely need** — sold as "
        "**monthly memberships**: you receive the refreshed list every "
        "month. **$%d/mo** for a full-state filtered segment, "
        "**$%d/mo** for a city-level list, **$%d/mo** for mini lists. "
        "Cancel anytime. Prefer a single purchase? Every pack also "
        "has a **one-time edition at 20%% more** — a frozen snapshot "
        "of the data as of its build date, never updated."
        % (PACK_PRICE_STATEWIDE, PACK_PRICE_CITY, PACK_PRICE_MINI),
        "",
        "**Why priced this way:** full-state filtered subscriptions "
        "in this market run $49–$99/mo (New Business Alerts charges "
        "$99/mo for one state, all industries, and $49/mo for one "
        "state, one industry; FirstFiling charges $59/mo per state "
        "and $29/mo per county), so the tiers sit inside that range. "
        "These filings lists carry no phone or email — business name, "
        "city, parish, entity type, filing date, and registered agent "
        "only. (The FMCSA carrier packs, which do include as-filed "
        "phone/email, are priced against the market instead: NewDOT "
        "Leads charges $79/mo per state and raw leads trade at "
        "$0.40–$0.99 each, so their sticker prices are the monthly "
        "prices — $19–$79/mo as memberships, $23–$95 as one-time "
        "snapshots.)",
        "",
        "Every pack contains: business name, city, parish, entity type, "
        "filing date, registered agent, and industry category — one row "
        "per business, newest first. Data: Louisiana Secretary of State "
        "published new-filing lists (public records), %d filings on file, "
        "most recent filing %s." % (len(records), as_of.isoformat()),
        "",
        "**Honesty, up front:** industry categories are *inferred from the "
        "business name* with keyword rules (the filings carry no industry "
        "code), and need packs are *likely-need* segments inferred from "
        "business type — never verified needs. See `CATEGORIES.md` for the "
        "full method.",
        "",
        "## The packs (%d)" % len(generated),
        "",
        "| # | Pack | Records | Window | Price |",
        "|---|---|---|---|---|",
    ]
    for i, g in enumerate(generated, 1):
        cat_lines.append("| %d | %s (`%s/`) | %d | %s | $%d/mo · "
                         "$%d one-time |"
                         % (i, g["title"], g["slug"], g["count"],
                            g["window"], g["price"],
                            g["price_onetime"]))
    cat_lines += [
        "",
        "## Gumroad listing template — membership edition (one per pack)",
        "",
        "Every pack publishes as a PAIR of Gumroad products: this "
        "membership edition first, then the one-time snapshot edition "
        "(template below).",
        "",
        "```",
        "TITLE: {pack title}",
        "",
        "PRICE: {this pack's monthly price from the table above — "
        "$75/mo full-state filtered segment; $29/mo city-level; $15/mo "
        "mini list} — set up as a monthly membership on Gumroad",
        "",
        "DESCRIPTION:",
        "A monthly membership for this exact slice of new Louisiana "
        "business filings. Every month you receive the refreshed CSV "
        "for the newest filing window — {count} businesses in the "
        "current edition, filed {date span} — already sliced to "
        "exactly the list you need, no database, no cleanup, no "
        "digging through the state's raw weekly lists.",
        "",
        "You get a CSV with: business name, city, parish, entity type, "
        "filing date, registered agent, and industry category. One row per "
        "business, newest filing first. Open it in Excel, Google Sheets, "
        "or any CRM importer.",
        "",
        "WHO THIS IS FOR: anyone who sells to new businesses in Louisiana "
        "— web designers, insurers, lenders, sign shops, bookkeepers, "
        "staffing and driver recruiters — who wants fresh filings for "
        "their segment every month without doing the slicing "
        "themselves.",
        "",
        "THE HONEST PART: filings carry no phone or email — name, "
        "city, parish, entity type, filing date, and registered agent "
        "only. Industry is inferred from the business name "
        "using keyword rules (the state filings have no industry code), "
        "and any \"needs\" in the title are likely needs inferred from the "
        "business type — not verified. Parish is derived from the mailing "
        "city. Names are reproduced from the public filings as filed. "
        "Each monthly edition is a snapshot of its window, refreshed "
        "with the next edition; for always-current data see the "
        "Louisiana New Business Filings API.",
        "",
        "Cancel anytime — you keep every edition you have received.",
        "",
        "SOURCE: Louisiana Secretary of State NewCorp weekly newsletters "
        "(public records).",
        "```",
        "",
        "## Gumroad listing template — one-time snapshot edition "
        "(one per pack)",
        "",
        "```",
        "TITLE: {pack title} — One-Time Snapshot",
        "",
        "PRICE: {this pack's one-time price from the table above — "
        "the monthly price + 20%} — a single one-time purchase on "
        "Gumroad",
        "",
        "DESCRIPTION:",
        "This is the one-time edition of this list: a single frozen "
        "snapshot of new Louisiana business filings for this slice — "
        "{count} businesses, filed {date span} — downloaded once and "
        "done. It is never updated: when you have run through it, it "
        "is dead done. If you want this same list refreshed with the "
        "newest filings every month, the monthly membership edition "
        "is the version that refreshes (and it costs less).",
        "",
        "You get a CSV with: business name, city, parish, entity type, "
        "filing date, registered agent, and industry category. One row per "
        "business, newest filing first. Open it in Excel, Google Sheets, "
        "or any CRM importer.",
        "",
        "THE HONEST PART: this snapshot is the data as of its build "
        "date and it will not change — no refresh, no new filings "
        "added. Filings carry no phone or email — name, city, parish, "
        "entity type, filing date, and registered agent only. "
        "Industry is inferred from the business name using keyword "
        "rules (the state filings have no industry code), and any "
        "\"needs\" in the title are likely needs inferred from the "
        "business type — not verified. Parish is derived from the "
        "mailing city. Names are reproduced from the public filings "
        "as filed. For always-current data see the Louisiana New "
        "Business Filings API.",
        "",
        "SOURCE: Louisiana Secretary of State NewCorp weekly newsletters "
        "(public records).",
        "```",
        "",
        "## Skipped slices",
        "",
        "Slices under %d records are not sold — a list that small is "
        "not worth $%d/mo of anyone's money. The full skip log "
        "(including any packs cut by the batch cap) is in "
        "`BUILD-LOG.md`."
        % (MIN_PACK_RECORDS, PACK_PRICE_MINI),
        "",
    ]
    (packs_dir / "PACK-CATALOG.md").write_text("\n".join(cat_lines))

    # --- BUILD-LOG.md -------------------------------------------------------
    log_lines = [
        "# Build Log — category pack batch",
        "",
        "Generated by `classify.py build` from `../data/seed.json` "
        "(%d records; dataset generated %s; most recent filing %s)."
        % (len(records), generated_at, as_of.isoformat()),
        "",
        "Rules: minimum %d records per pack; batch cap ~%d packs "
        "(tiers: statewide industry, then need segments, then city packs; "
        "biggest first within a tier)." % (MIN_PACK_RECORDS, MAX_PACKS),
        "",
        "## Generated (%d packs)" % len(generated),
        "",
    ]
    for g in generated:
        log_lines.append("- `%s/` — %s — %d records — %s — %s"
                         % (g["slug"], g["title"], g["count"], g["kind"],
                            g["window"]))
    log_lines += ["", "## Skipped (%d)" % len(skipped), ""]
    for desc, reason in skipped:
        log_lines.append("- %s — %s" % (desc, reason))
    log_lines.append("")
    (packs_dir / "BUILD-LOG.md").write_text("\n".join(log_lines))

    print("as_of=%s records=%d packs=%d skipped=%d"
          % (as_of.isoformat(), len(records), len(generated), len(skipped)))
    for g in generated:
        print("  PACK %-58s %6d  %s" % (g["slug"], g["count"], g["kind"]))
    for desc, reason in skipped:
        print("  SKIP %s — %s" % (desc, reason))
    return generated, skipped


def stats():
    records, as_of, _gen = load_records()
    print("records=%d as_of=%s" % (len(records), as_of.isoformat()))
    print("\n== Industry totals (all time / last 90d / last 12mo) ==")
    by_ind = defaultdict(list)
    for r in records:
        by_ind[r["industry"]].append(r)
    for cat in INDUSTRY_NAMES:
        rows = by_ind.get(cat, [])
        print("  %-28s %7d %7d %7d" % (
            cat, len(rows),
            sum(1 for r in rows if in_window(r, 90)),
            sum(1 for r in rows if in_window(r, 365))))
    print("\n== Need segments (all time / last 90d / last 12mo) ==")
    for name, _slug, _desc, pred in NEED_SEGMENTS:
        rows = [r for r in records if pred(r)]
        print("  %-28s %7d %7d %7d" % (
            name, len(rows),
            sum(1 for r in rows if in_window(r, 90)),
            sum(1 for r in rows if in_window(r, 365))))
    print("\n== Top cities ==")
    for city, n in Counter((r.get("city") or "").strip()
                           for r in records).most_common(8):
        print("  %-28s %7d" % (city, n))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        stats()
    elif cmd == "build":
        build()
    else:
        print("usage: classify.py [stats|build]")
        sys.exit(2)
