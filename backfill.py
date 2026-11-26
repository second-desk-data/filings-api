#!/usr/bin/env python3
"""
backfill.py — Extend data/seed.json backwards through the Louisiana
Secretary of State's published "NewCorp" weekly newsletter archive.

refresh.py only pulls the newest couple of weekly files. This script walks
back week by week from the newest published file, downloading every older
weekly newsletter the archive still serves, then rebuilds seed.json with
the full history (de-duplicated by charter number, newest file wins).

Source: https://corafiles.sos.la.gov/NewCorpNewsletters/NewCorp_YYYYMMDD.txt
Published newsletter files ONLY — the CORAWeb search site is never touched
(Louisiana's admin rule blocks automated harvesting there; see
data/SOURCES.md).

Politeness: descriptive User-Agent (shared with refresh.py), ~0.6 s between
existence probes, >=1.5 s between downloads, sequential requests only.

Usage:
    python3 backfill.py [--max-weeks 104] [--stop-after-missing 6]
                        [--out data/seed.json]
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import refresh  # reuse UA, BASE, fetch, parse_newsletter, find_recent_files

PROBE_DELAY = 0.6   # seconds between HEAD probes
FETCH_DELAY = 1.5   # seconds between full downloads (matches refresh.py)


def probe(day):
    """HEAD-check whether NewCorp_YYYYMMDD.txt exists for `day`."""
    url = refresh.BASE.format(day.strftime("%Y%m%d"))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": refresh.UA},
                                     method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError):
        return False


def enumerate_archive(max_weeks, stop_after_missing):
    """Return [(date, url), ...] newest-first for every weekly file found."""
    anchor = refresh.find_recent_files(1)
    if not anchor:
        print("ERROR: could not find the newest newsletter file", file=sys.stderr)
        return []
    anchor_day = anchor[0][0]
    print(f"Newest file: NewCorp_{anchor_day.strftime('%Y%m%d')}.txt")

    found = []
    missing_streak = 0
    for week in range(max_weeks):
        target = anchor_day - timedelta(weeks=week)
        hit = None
        # The file is dated on a consistent weekday (Tuesdays); if that exact
        # date misses (holiday shifts), try the surrounding days in the week.
        for offset in (0, 1, -1, 2, -2, 3, -3):
            day = target + timedelta(days=offset)
            if probe(day):
                hit = day
                break
            time.sleep(PROBE_DELAY)
        if hit is None:
            missing_streak += 1
            print(f"  week of {target.isoformat()}: no file "
                  f"({missing_streak} missing in a row)")
            if missing_streak >= stop_after_missing:
                print("  reached the start of the archive; stopping.")
                break
        else:
            missing_streak = 0
            url = refresh.BASE.format(hit.strftime("%Y%m%d"))
            found.append((hit, url))
            print(f"  week of {target.isoformat()}: found "
                  f"NewCorp_{hit.strftime('%Y%m%d')}.txt")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-weeks", type=int, default=104,
                    help="how many weeks back to try (default 104 = 2 years)")
    ap.add_argument("--stop-after-missing", type=int, default=6,
                    help="stop after this many consecutive missing weeks")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "seed.json"))
    args = ap.parse_args()

    files = enumerate_archive(args.max_weeks, args.stop_after_missing)
    if not files:
        print("ERROR: no newsletter files found", file=sys.stderr)
        return 1
    print(f"\nDownloading {len(files)} newsletter file(s)...")

    by_charter = {}
    per_file = []
    for day, url in files:  # newest first -> setdefault keeps newest record
        text = refresh.fetch(url)
        fname = f"NewCorp_{day.strftime('%Y%m%d')}.txt"
        recs = refresh.parse_newsletter(text, url, fname)
        new = 0
        for rec in recs:
            if rec["charter_number"] not in by_charter:
                by_charter[rec["charter_number"]] = rec
                new += 1
        per_file.append((fname, len(recs), new))
        print(f"  {fname}: {len(recs)} parsed, {new} new after dedup")
        time.sleep(FETCH_DELAY)

    records = sorted(by_charter.values(),
                     key=lambda r: (r["filing_date"], r["name_raw"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "Louisiana New Business Filings (Secretary of State NewCorp newsletters)",
        "generated_at": date.today().isoformat(),
        "record_count": len(records),
        "sources": [url for _, url in files],
        "records": records,
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    with_parish = sum(1 for r in records if r["parish"])
    parishes = {r["parish"] for r in records if r["parish"]}
    dates = [r["filing_date"] for r in records]
    print(f"\nWrote {len(records)} unique records to {out}")
    print(f"  filing dates: {min(dates)} -> {max(dates)}")
    print(f"  parishes covered: {len(parishes)} of 64; "
          f"{with_parish} records ({100 * with_parish / len(records):.1f}%) parish-tagged")
    print(f"  source files: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
