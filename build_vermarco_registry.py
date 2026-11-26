#!/usr/bin/env python3
"""
Generate app/vermarco_listings.json — the listing -> pack registry the
Vermarco delivery endpoint (app/vermarco.py) uses to resolve a
delivery_request's listingId to the dataset/scope it was sold as.

Sources (all in the sibling 85-sellmydata-bulk kit):
  state.json            listing_id per manifest key / staged test pack
  work/slim-manifest.json   the 72 bulk-created listings (key -> title)
  work/verticals.json       manifest key -> Vermarco vertical slug

Routing model:
  filings listings   -> route "filings": defaults merged UNDER the
                        buyer query in the existing filings delivery
                        (full-state packs pin their state; the
                        national bundle pins nothing).
  census / WARN      -> route "packs", scope = the pack slug (the
                        packs DB ingests each pack file under its own
                        slug, so a listing resolves to its exact file).
  UCC                -> route "packs", scope = state code / null (the
                        national bundle is exactly CO+CT).
  trademark          -> route "packs", scope = null.
  $0 test packs      -> same routes with a default limit of 25 (their
                        titles promise a 25-record sample).

Run:  python3 build_vermarco_registry.py
Writes app/vermarco_listings.json and prints a per-vertical census.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KIT = ROOT.parent / "85-sellmydata-bulk"

# The six staged $0 test packs (state.json "adhoc:" entries). Their
# titles promise a 25-record sample of the named dataset.
TEST_PACKS = {
    "37cf116e-f3ed-4b5c-8471-5d5d843640eb":
        {"route": "filings", "vertical": "forms-filings",
         "defaults": {"state": "PA", "limit": 25}},
    "5bcb1558-9b55-47c7-937e-6987e6ef0bb3":
        {"route": "filings", "vertical": "forms-filings",
         "defaults": {"state": "LA", "limit": 25}},
    "39e756c5-b6d8-45e6-9ec6-28bfe5df95e9":
        {"route": "packs", "vertical": "demographics",
         "dataset": "census",
         "scope": "us-census-demographics-national-bundle", "limit": 25},
    "2af31097-93e5-419d-b058-614fc0021db7":
        {"route": "packs", "vertical": "lending-credit",
         "dataset": "ucc", "scope": "CO", "limit": 25},
    "92b14c5e-1bb3-48ea-ba6c-dd9174b25c1c":
        {"route": "packs", "vertical": "labor-market",
         "dataset": "warn",
         "scope": "us-warn-act-full-national-complete", "limit": 25},
    "adbaab23-e65a-4d60-9872-09f68b28ec48":
        {"route": "packs", "vertical": "patents-trademarks",
         "dataset": "trademark", "scope": None, "limit": 25},
}


def entry_for(key, vertical):
    """Registry entry (minus listing id) for one manifest listing key."""
    family = key.split(":")[0]
    slug = key.split(":")[-1]
    if family == "filings":
        state = key.split(":")[1]
        defaults = {} if state == "US" else {"state": state}
        return {"route": "filings", "vertical": vertical,
                "defaults": defaults}
    if family == "census":
        return {"route": "packs", "vertical": vertical,
                "dataset": "census", "scope": slug}
    if family == "ucc":
        scope = {"ucc-co-full-state-registry": "CO",
                 "ucc-ct-full-state-registry": "CT",
                 "ucc-national-bundle-all-states": None}[slug]
        return {"route": "packs", "vertical": vertical,
                "dataset": "ucc", "scope": scope}
    if family == "warn-act":
        return {"route": "packs", "vertical": vertical,
                "dataset": "warn", "scope": slug}
    if family == "trademark":
        return {"route": "packs", "vertical": vertical,
                "dataset": "trademark", "scope": None}
    raise ValueError(f"unhandled family in key {key}")


def main():
    state = json.loads((KIT / "state.json").read_text(encoding="utf-8"))
    slim = json.loads((KIT / "work" / "slim-manifest.json")
                      .read_text(encoding="utf-8"))
    verticals = json.loads((KIT / "work" / "verticals.json")
                           .read_text(encoding="utf-8"))
    titles = {e["listing_key"]: e["title"] for e in slim["listings"]}

    registry = {}
    for skey, rec in state.items():
        if not skey.startswith("manifest:") or not isinstance(rec, dict):
            continue
        lid = rec.get("listing_id")
        if not lid or rec.get("stage") != "created":
            continue
        key = skey[len("manifest:"):]
        vertical = verticals.get(key)
        if vertical is None:
            # The paid PA listing predates the slim manifest; it is a
            # forms-filings full-state pack like the other filings keys.
            if key == "filings:PA:pa-full-state":
                vertical = "forms-filings"
            else:
                raise SystemExit(f"no vertical known for {key}")
        entry = entry_for(key, vertical)
        entry["key"] = key
        if key in titles:
            entry["title"] = titles[key]
        registry[lid] = entry
    for lid, entry in TEST_PACKS.items():
        entry = dict(entry)
        entry["test_pack"] = True
        registry[lid] = entry

    out = ROOT / "app" / "vermarco_listings.json"
    out.write_text(json.dumps(registry, indent=1, sort_keys=True),
                   encoding="utf-8")
    by_vertical = {}
    for e in registry.values():
        by_vertical[e["vertical"]] = by_vertical.get(e["vertical"], 0) + 1
    print(f"wrote {out}: {len(registry)} listings")
    for v in sorted(by_vertical):
        print(f"  {v}: {by_vertical[v]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
