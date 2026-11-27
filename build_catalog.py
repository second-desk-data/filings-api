#!/usr/bin/env python3
"""
Build data/catalog.db — the sellable-catalog store behind
GET /v1/catalog and GET /v1/catalog/{family} (agent commerce layer).

Sources are the Second Desk catalog site lane's own register files
(53-catalog-site/), the same data the public storefront renders:

  site/catalog-feed.json   family totals: slug, name, description,
                           pack ("lists") count, record count, URL
  families.json            per-family blurb, field list, honesty note,
                           status (joined by slug)
  site/catalog.json        every sellable pack row (68,801): title,
                           family display name, state, kind, window,
                           record count, display price, buy URL

The store mirrors the filings/packs design: a disk-backed SQLite
database the server queries per request, so memory stays flat. Prices
are parsed out of the register's display strings ("$230/mo · $276
one-time", "$29/mo", "$440 one-time", "$0 — free test pack", "—" for
unpriced) into numeric monthly / one-time USD fields; unpriced rows
carry nulls rather than an invented number.

Pack ids are stable across rebuilds: "sd-" + the first 16 hex chars of
sha1(family_slug | title), so an agent can cite a pack id and get the
same pack after a catalog refresh (as long as its title is unchanged).

Run:  python3 build_catalog.py [--out data/catalog.db]
Rebuild whenever the catalog site lane regenerates its register, then
rebuild the image (the store is a snapshot, like filings.db).
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTS = ROOT.parent  # ~/workspace/niche-ai-products
CATALOG_LANE = PRODUCTS / "53-catalog-site"

PRICE_MONTHLY = re.compile(r"\$([\d,]+(?:\.\d+)?)\s*/mo")
PRICE_ONETIME = re.compile(r"\$([\d,]+(?:\.\d+)?)\s+one-time")


def _num(text):
    return float(text.replace(",", ""))


def parse_price(display):
    """(monthly, onetime, free) from a register display price string."""
    if not display:
        return None, None, False
    free = "free test pack" in display
    m = PRICE_MONTHLY.search(display)
    o = PRICE_ONETIME.search(display)
    return (_num(m.group(1)) if m else None,
            _num(o.group(1)) if o else None,
            free)


def pack_id(family_slug, title):
    digest = hashlib.sha1(f"{family_slug}|{title}".encode("utf-8"))
    return "sd-" + digest.hexdigest()[:16]


def load_sources(lane=CATALOG_LANE):
    feed = json.loads((lane / "site" / "catalog-feed.json")
                      .read_text(encoding="utf-8"))
    families_doc = json.loads((lane / "families.json")
                              .read_text(encoding="utf-8"))
    catalog = json.loads((lane / "site" / "catalog.json")
                         .read_text(encoding="utf-8"))
    return feed, families_doc, catalog


def build(lane, out_path):
    feed, families_doc, catalog = load_sources(lane)
    rich = {f["slug"]: f for f in families_doc["families"]}
    feed_families = feed["families"]
    slug_by_name = {f["name"]: f["slug"] for f in feed_families}

    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute(
        "CREATE TABLE families ("
        " slug TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " description TEXT, blurb TEXT, packs INTEGER NOT NULL,"
        " records INTEGER NOT NULL, url TEXT, fields TEXT,"
        " honesty TEXT, status TEXT)")
    con.execute(
        "CREATE TABLE packs ("
        " id TEXT PRIMARY KEY, family_slug TEXT NOT NULL,"
        " title TEXT NOT NULL, records INTEGER NOT NULL,"
        " price_monthly REAL, price_onetime REAL, free INTEGER NOT NULL,"
        " state_name TEXT, kind TEXT, window TEXT, buy_url TEXT)")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    for fam in feed_families:
        slug = fam["slug"]
        extra = rich.get(slug, {})
        con.execute(
            "INSERT INTO families VALUES (?,?,?,?,?,?,?,?,?,?)",
            (slug, fam["name"], fam.get("description"),
             extra.get("blurb"), int(fam.get("lists") or 0),
             int(fam.get("records") or 0), fam.get("url"),
             json.dumps(extra.get("fields") or []),
             extra.get("honesty"), extra.get("status")))

    n_packs = 0
    seen_ids = set()
    skipped = 0
    batch = []
    for row in catalog["rows"]:
        slug = slug_by_name.get(row.get("family"))
        if slug is None:
            skipped += 1
            continue
        pid = pack_id(slug, row.get("title") or "")
        if pid in seen_ids:  # same title twice in one family: keep first
            skipped += 1
            continue
        seen_ids.add(pid)
        monthly, onetime, free = parse_price(row.get("price"))
        batch.append((pid, slug, row.get("title") or "",
                      int(row.get("records") or 0), monthly, onetime,
                      1 if free else 0, row.get("state_name"),
                      row.get("kind"), row.get("window"), row.get("buy")))
        n_packs += 1
        if len(batch) >= 2000:
            con.executemany("INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            batch)
            batch = []
    if batch:
        con.executemany("INSERT INTO packs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        batch)
    con.execute("CREATE INDEX idx_packs_family ON packs(family_slug)")
    totals = {
        "families": len(feed_families),
        "packs": n_packs,
        "records": int(feed["totals"]["records"]),
        "lists": int(feed["totals"]["lists"]),
        "skipped_rows": skipped,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "53-catalog-site register (catalog-feed.json, "
                  "families.json, site/catalog.json)",
    }
    for k, v in totals.items():
        con.execute("INSERT INTO meta VALUES (?, ?)", (k, json.dumps(v)))
    con.commit()
    con.close()
    return {**totals, "db_bytes": out_path.stat().st_size}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "catalog.db"))
    ap.add_argument("--lane", default=str(CATALOG_LANE))
    args = ap.parse_args()
    print(f"building {args.out} from catalog lane {args.lane}...")
    stats = build(Path(args.lane), Path(args.out))
    stats_path = Path(str(args.out) + ".stats.json")
    stats_path.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"done: {stats['families']} families, {stats['packs']} packs, "
          f"{stats['records']} records, {stats['db_bytes']} bytes "
          f"-> {args.out} (stats: {stats_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
