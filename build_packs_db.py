#!/usr/bin/env python3
"""
Build data/packs.db — the Vermarco delivery store for the non-filings
families (85-sellmydata-bulk/RUNBOOK.md Gate 2 extension, Nov 26, 2026).

The filings endpoint serves per-query slices from data/filings.db; the
other live sellmydata verticals (census demographics, UCC, WARN,
trademark) resolve to pack CSVs that live OUTSIDE this repo. This
builder mirrors the filings design exactly — a disk-backed SQLite
store, record payloads stored as zlib-compressed JSON, only narrow
filter columns held in the table — so app/vermarco.py can serve the
same per-query slices for those families with flat memory:

    pack_records(dataset, scope, state_key, city_key, name_key,
                 date_key, record_json BLOB)

  dataset  census | ucc | trademark | warn
  scope    the pack the row belongs to: census/WARN use the pack slug
           (a state pack's rows exist ONLY under its own slug — the
           exact pack file is ingested per listing), UCC uses the
           state code (the national bundle is exactly CO+CT: the
           bundle CSV's byte size equals the two state files minus
           one header line, and the row counts add up), trademark ''.
  *_key    lowercased filter columns (state / city / primary name /
           ISO date) extracted per family; everything else rides in
           record_json.

  record_json payload: a zlib-compressed JSON ARRAY of the row's
  values in header order (the header itself is stored once per
  dataset+scope in pack_meta as "fields:<dataset>:<scope>").
  Storing per-row JSON objects would repeat ~150 bytes of key names
  in every one of the 5.6M rows and — because each row compresses
  independently — roughly triple the store's size; the array form
  reconstructs the identical dict at delivery time.

Sources are the pack files the live listings were created from
(49-databazaar-bulk/manifest.json data_file fields +
91-layoffs-global phase-2B registration for the refreshed WARN
editions). Rebuild after any pack refresh, then rebuild the image
(the store is a snapshot, like filings.db).

Run:  python3 build_packs_db.py [--out data/packs.db]
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTS = ROOT.parent  # ~/workspace/niche-ai-products

csv.field_size_limit(sys.maxsize)


def _row_get(row, *names):
    for n in names:
        v = row.get(n)
        if v:
            return v
    return ""


# Each source: (dataset, scope, path, state_of, city_of, name_of, date_of)
# The *_of callables pull the filter columns out of a CSV row dict.
def census_sources():
    cat = PRODUCTS / "55-census-demographics" / "catalog"
    out = []
    bundle = cat / "us-census-demographics-national-bundle" / \
        "us-census-demographics-national-bundle.csv"
    out.append(("census", "us-census-demographics-national-bundle", bundle))
    for d in sorted(cat.glob("*-full-state-demographics")):
        f = d / (d.name + ".csv")
        if f.exists():
            out.append(("census", d.name, f))
    return out


def ucc_sources():
    packs = PRODUCTS / "54-ucc-leads" / "packs"
    return [
        ("ucc", "CO", packs / "ucc-co-full-state-registry" / "data.csv"),
        ("ucc", "CT", packs / "ucc-ct-full-state-registry" / "data.csv"),
    ]


def trademark_sources():
    f = PRODUCTS / "61-trademark-filings" / "packs" / \
        "trademark-filings-us-national-bundle-2026-ytd" / "pack.csv"
    return [("trademark", "", f)]


def warn_sources():
    out = []
    p2 = PRODUCTS / "91-layoffs-global" / "packs"
    for slug in ("us-warn-act-full-national-complete",
                 "ca-full-state-complete", "fl-full-state-complete",
                 "nc-full-state-complete"):
        out.append(("warn", slug, p2 / slug / (slug + ".csv")))
    old = PRODUCTS / "81-warn-act" / "packs"
    for slug in ("al-full-state", "in-full-state", "va-full-state",
                 "tx-full-state"):
        out.append(("warn", slug, old / slug / (slug + ".csv")))
    return out


def all_sources():
    return (census_sources() + ucc_sources() + trademark_sources()
            + warn_sources())


def filter_keys(dataset, row):
    """(state_key, city_key, name_key, date_key) for one CSV row."""
    if dataset == "census":
        return ((_row_get(row, "state_name")).lower(),
                (_row_get(row, "geography_name")).lower(),
                (_row_get(row, "geography_name")).lower(),
                "")
    if dataset == "ucc":
        return ((_row_get(row, "debtor_state", "state")).lower(),
                (_row_get(row, "debtor_city")).lower(),
                (_row_get(row, "debtor_name")).lower(),
                _row_get(row, "filing_date"))
    if dataset == "trademark":
        return ((_row_get(row, "owner_state")).lower(),
                (_row_get(row, "owner_city")).lower(),
                (_row_get(row, "mark_identification", "owner_name")).lower(),
                _row_get(row, "filing_date"))
    if dataset == "warn":
        return ((_row_get(row, "site_state", "source_state")).lower(),
                (_row_get(row, "site_city")).lower(),
                (_row_get(row, "employer")).lower(),
                _row_get(row, "notice_date", "effective_date"))
    raise ValueError(dataset)


def build(sources, out_path):
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("PRAGMA cache_size = -64000")
    con.execute(
        "CREATE TABLE pack_records ("
        " dataset TEXT NOT NULL, scope TEXT NOT NULL,"
        " state_key TEXT, city_key TEXT, name_key TEXT, date_key TEXT,"
        " record_json BLOB NOT NULL)")
    con.execute("CREATE TABLE pack_meta (key TEXT PRIMARY KEY, value TEXT)")
    stats = {}
    total = 0
    t0 = time.time()
    for dataset, scope, path in sources:
        if not Path(path).exists():
            raise SystemExit(f"missing source file: {path}")
        n = 0
        batch = []
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            con.execute(
                "INSERT OR REPLACE INTO pack_meta VALUES (?, ?)",
                (f"fields:{dataset}:{scope}", json.dumps(fields)))
            for row in reader:
                row.pop(None, None)  # ragged-row overflow column
                state_key, city_key, name_key, date_key = \
                    filter_keys(dataset, row)
                values = [row.get(f) if row.get(f) is not None else ""
                          for f in fields]
                payload = zlib.compress(
                    json.dumps(values, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8"), 6)
                batch.append((dataset, scope, state_key, city_key,
                              name_key, date_key, payload))
                n += 1
                if len(batch) >= 2000:
                    con.executemany(
                        "INSERT INTO pack_records VALUES (?,?,?,?,?,?,?)",
                        batch)
                    batch = []
        if batch:
            con.executemany(
                "INSERT INTO pack_records VALUES (?,?,?,?,?,?,?)", batch)
        con.commit()
        stats[f"{dataset}:{scope}"] = n
        total += n
        print(f"  {dataset}:{scope}: {n} rows "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)
    print("creating indexes...", flush=True)
    con.execute("CREATE INDEX idx_pack_scope ON pack_records(dataset, scope)")
    con.execute("CREATE INDEX idx_pack_state ON pack_records(dataset, state_key)")
    con.execute("INSERT INTO pack_meta VALUES ('total_records', ?)",
                (str(total),))
    con.execute("INSERT INTO pack_meta VALUES ('sources', ?)",
                (json.dumps(stats),))
    con.execute("INSERT INTO pack_meta VALUES ('generated_at', ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
    con.commit()
    con.close()
    return {"total_records": total, "sources": stats,
            "db_bytes": out_path.stat().st_size}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "packs.db"))
    args = ap.parse_args()
    print(f"building {args.out} from {len(all_sources())} pack files...")
    stats = build(all_sources(), args.out)
    stats_path = Path(args.out).with_suffix(".db.stats.json") \
        if str(args.out).endswith(".db") else Path(str(args.out) + ".stats.json")
    stats_path.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"done: {stats['total_records']} records, "
          f"{stats['db_bytes']} bytes -> {args.out} (stats: {stats_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
