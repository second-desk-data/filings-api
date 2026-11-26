#!/usr/bin/env python3
"""
build_db.py — build the SQLite store the API serves from.

    python3 build_db.py [--data data/seed.json] [--out data/filings.db]

WHY
---
The server used to load data/seed.json plus every data/states/<ST>.json
into RAM and classify at startup. With eight states that corpus is
~2.0 GB of JSON / ~5.7M records — several GB of RAM and minutes of
startup, which does not fit a free host (Render free = 512 MB). This
script converts the JSON corpus into ONE SQLite database, once, at
build time:

  * records are normalized exactly as the old in-memory Store normalized
    them (same field contract — see normalize_record below),
  * industry category + likely-need segments are computed HERE, at build
    time, with the ONE canonical classifier (packs/classify.py via the
    app/classify.py adapter) — the same rule set the $9 packs use,
  * the full annotated record is stored as JSON in `record_json`, so API
    responses are byte-identical in shape to the old in-memory server,
  * filter columns (filing_state, city, region/parish/county, category,
    filing_date, name) are broken out and indexed for SQL queries,
  * dataset metadata (per-state counts, per-state min/max filing dates,
    global min/max) is precomputed into the `meta` table so /health and
    the age_days anchor never need a scan.

The server (app/server.py) then answers everything with indexed SQL
queries and holds no records in RAM: flat memory, instant startup.

The JSON inputs are parsed STREAMING (one record at a time via
json.JSONDecoder.raw_decode over a refilling buffer), so building the
744 MB PA file never holds more than a few MB of it in memory either.

A sidecar `<out>.stats.json` is written next to the database with the
per-state counts and date anchors — the test suite uses it as ground
truth, and LAUNCH-STATUS.md quotes it.

Stdlib only, like the rest of this project.
"""

import argparse
import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))  # for `import classify`

import classify as CLASSIFY  # noqa: E402 — the shared classifier adapter

CHUNK = 1 << 20
BATCH = 5000


# ---------------------------------------------------------------------------
# Streaming JSON corpus reader
# ---------------------------------------------------------------------------

def _read_header(f):
    """Read up to the top-level "records" key. Returns (meta_dict, buffer)
    where buffer holds the text right after the records array's '['."""
    buf = ""
    while '"records"' not in buf:
        chunk = f.read(CHUNK)
        if not chunk:
            raise ValueError("no top-level 'records' key found")
        buf += chunk
    idx = buf.index('"records"')
    header_text, rest = buf[:idx], buf[idx + len('"records"'):]
    meta = {}
    header_text = header_text.strip().rstrip(",").rstrip()
    if header_text.startswith("{"):
        try:
            meta = json.loads(header_text + "}")
        except ValueError:
            meta = {}
    while "[" not in rest:
        chunk = f.read(CHUNK)
        if not chunk:
            raise ValueError("records array never opens")
        rest += chunk
    return meta, rest[rest.index("[") + 1:]


def iter_records(path):
    """Yield (file_meta, record_dict) for each record in a corpus file of
    the shape {"dataset": ..., "records": [ {...}, ... ]}, streaming."""
    decoder = json.JSONDecoder()
    with open(path, encoding="utf-8") as f:
        meta, buf = _read_header(f)
        while True:
            buf = buf.lstrip(" \t\r\n,")
            if buf.startswith("]"):
                return
            if not buf:
                chunk = f.read(CHUNK)
                if not chunk:
                    return
                buf += chunk
                continue
            try:
                obj, end = decoder.raw_decode(buf)
            except ValueError:
                chunk = f.read(CHUNK)
                if not chunk:
                    raise
                buf += chunk
                continue
            yield meta, obj
            buf = buf[end:]
            if len(buf) < (CHUNK >> 4):
                chunk = f.read(CHUNK)
                if chunk:
                    buf += chunk


# ---------------------------------------------------------------------------
# Record normalization — the old Store._normalize + annotation sequence,
# verbatim in effect. Key insertion order is preserved in record_json so
# responses keep the old shape.
# ---------------------------------------------------------------------------

def normalize_record(r, filing_state):
    r["filing_state"] = filing_state
    r["charter_number"] = r.get("charter_number") or r.get("id") or ""
    r["id"] = r.get("id") or r["charter_number"]
    r.setdefault("name", r.get("name_raw") or "")
    r.setdefault("name_raw", r.get("name") or "")
    r.setdefault("entity_type", "Other")
    r.setdefault("origin", "domestic")
    r.setdefault("filing_date", None)
    r.setdefault("city", None)
    r.setdefault("zip", None)
    r.setdefault("address", None)
    r.setdefault("registered_agent", None)
    if "agents" not in r:
        r["agents"] = [r["registered_agent"]] if r.get("registered_agent") else []
    if filing_state != "LA":
        r["county"] = r.get("county") or r.get("county_or_parish")
    r["region"] = r.get("parish") or r.get("county") or r.get("county_or_parish")
    return r


def annotate(r):
    r["category"] = CLASSIFY.classify_industry(
        r.get("name") or r.get("name_raw") or "")
    r["category_label"] = CLASSIFY.INDUSTRY_LABELS[r["category"]]
    r["needs"] = CLASSIFY.needs_for(r)
    # Formation-mill flag (canonical matcher in packs/classify.py):
    # additive, name-matched only — see agent_is_commercial there.
    r["agent_is_commercial"] = CLASSIFY.record_agent_is_commercial(r)
    return r


def _lower(v):
    return v.lower() if isinstance(v, str) else None


SCHEMA = """
CREATE TABLE filings (
    filing_state      TEXT NOT NULL,
    charter_number    TEXT,
    city              TEXT,
    city_lower        TEXT,
    region            TEXT,
    region_lower      TEXT,
    parish            TEXT,
    parish_lower      TEXT,
    county            TEXT,
    county_lower      TEXT,
    category          TEXT,
    entity_type       TEXT,
    entity_type_lower TEXT,
    origin            TEXT,
    filing_date       TEXT,
    name_raw          TEXT,
    name_lower        TEXT,
    name_raw_lower    TEXT,
    record_json       TEXT NOT NULL
);
CREATE TABLE record_needs (
    record_id INTEGER NOT NULL,
    need_slug TEXT NOT NULL
);
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

INDEXES = [
    "CREATE INDEX idx_filings_state ON filings(filing_state)",
    "CREATE INDEX idx_filings_state_city ON filings(filing_state, city_lower)",
    "CREATE INDEX idx_filings_charter ON filings(charter_number)",
    "CREATE INDEX idx_filings_state_region ON filings(filing_state, region_lower)",
    "CREATE INDEX idx_filings_state_region_exact ON filings(filing_state, region)",
    "CREATE INDEX idx_filings_state_category ON filings(filing_state, category)",
    "CREATE INDEX idx_filings_date ON filings(filing_date)",
    "CREATE INDEX idx_filings_state_date ON filings(filing_state, filing_date)",
    "CREATE INDEX idx_filings_state_date_name ON filings(filing_state, filing_date DESC, name_raw DESC)",
    "CREATE INDEX idx_filings_state_city_date_name ON filings(filing_state, city_lower, filing_date DESC, name_raw DESC)",
    "CREATE INDEX idx_filings_name ON filings(name_raw_lower)",
    "CREATE INDEX idx_needs_slug ON record_needs(need_slug, record_id)",
    "CREATE INDEX idx_needs_record ON record_needs(record_id)",
]

INSERT_SQL = """
INSERT INTO filings (filing_state, charter_number, city, city_lower,
                     region, region_lower, parish, parish_lower,
                     county, county_lower, category, entity_type,
                     entity_type_lower, origin, filing_date, name_raw,
                     name_lower, name_raw_lower, record_json)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def row_for(r, compress=False):
    payload = json.dumps(r)
    if compress:
        # Deploy builds store record_json zlib-compressed (BLOB). The
        # server decompresses transparently on read (app/server.py
        # _loads_record handles both str and bytes), so API responses
        # are byte-identical to the uncompressed build. Measured on the
        # 8-state corpus: record_json 3,987 MB -> ~2,200 MB.
        payload = zlib.compress(payload.encode("utf-8"), 6)
    return (
        r["filing_state"],
        r["charter_number"] or None,
        r.get("city"),
        _lower(r.get("city")),
        r.get("region"),
        _lower(r.get("region")),
        r.get("parish"),
        _lower(r.get("parish")),
        r.get("county"),
        _lower(r.get("county")),
        r["category"],
        r.get("entity_type"),
        _lower(r.get("entity_type")),
        r.get("origin"),
        r.get("filing_date") or None,
        r.get("name_raw"),
        _lower(r.get("name")),
        _lower(r.get("name_raw")),
        payload,
    )


def build(data_path, out_path, compress=False):
    data_path = Path(data_path)
    out_path = Path(out_path)
    files = [(data_path, None)]
    states_dir = data_path.parent / "states"
    if states_dir.is_dir():
        for extra in sorted(states_dir.glob("*.json")):
            files.append((extra, extra.stem.upper()))

    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.executescript(SCHEMA)

    dataset_meta = {}
    counts = {}
    state_min, state_max = {}, {}
    total = 0
    t0 = time.time()
    rows, needs_rows = [], []

    def flush():
        if rows:
            con.executemany(INSERT_SQL, rows)
            rows.clear()
        if needs_rows:
            con.executemany(
                "INSERT INTO record_needs (record_id, need_slug) VALUES (?,?)",
                needs_rows)
            needs_rows.clear()

    for path, default_state in files:
        n_file = 0
        for meta, raw in iter_records(path):
            filing_state = (meta.get("filing_state") or default_state
                            or "LA").upper()
            if not dataset_meta and meta.get("dataset"):
                dataset_meta = {k: meta[k] for k in
                                ("dataset", "generated_at", "record_count",
                                 "sources") if k in meta}
            r = annotate(normalize_record(raw, filing_state))
            rows.append(row_for(r, compress=compress))
            rid = total + len(rows)  # rowid this row will get (1-based,
            # sequential: single inserts stream into one fresh table)
            for slug in r["needs"]:
                needs_rows.append((rid, slug))
            fd = r.get("filing_date")
            if fd:
                if filing_state not in state_min or fd < state_min[filing_state]:
                    state_min[filing_state] = fd
                if filing_state not in state_max or fd > state_max[filing_state]:
                    state_max[filing_state] = fd
            counts[filing_state] = counts.get(filing_state, 0) + 1
            n_file += 1
            if len(rows) >= BATCH:
                total += len(rows)
                flush()
        total += len(rows)
        flush()
        print(f"  {path.name}: {n_file} records "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)

    print(f"  building indexes over {total} records...", flush=True)
    for stmt in INDEXES:
        con.execute(stmt)
    con.execute("ANALYZE")

    # Precomputed per-state aggregates for /v1/categories and /v1/regions.
    # Counting needs/categories for a 2.4M-record state live means joining
    # ~8M record_needs rows per request (measured: >120 s) — these tables
    # make those endpoints O(rows returned). '*' is the all-states row.
    # City-scoped keys still count live (their subset is small).
    print("  precomputing per-state category/need/region counts...",
          flush=True)
    con.executescript("""
    CREATE TABLE state_category_counts (
        state TEXT, slug TEXT, records INTEGER, PRIMARY KEY (state, slug));
    CREATE TABLE state_need_counts (
        state TEXT, slug TEXT, records INTEGER, PRIMARY KEY (state, slug));
    CREATE TABLE state_region_counts (
        state TEXT, region TEXT, records INTEGER, PRIMARY KEY (state, region));
    """)
    con.execute("INSERT INTO state_category_counts "
                "SELECT filing_state, category, COUNT(*) FROM filings "
                "GROUP BY filing_state, category")
    con.execute("INSERT INTO state_category_counts "
                "SELECT '*', category, COUNT(*) FROM filings GROUP BY category")
    # Per-state need counts WITHOUT a join: records are inserted file by
    # file, so each state owns one contiguous rowid range, and a BETWEEN
    # scan of idx_needs_record per state is sequential. (The naive
    # record_needs⨝filings join for this was measured at >10 min of
    # random I/O on the 8 GB store; the range scans take seconds.)
    for st, lo, hi in con.execute(
            "SELECT filing_state, MIN(rowid), MAX(rowid) FROM filings "
            "GROUP BY filing_state").fetchall():
        con.execute("INSERT INTO state_need_counts "
                    "SELECT ?, need_slug, COUNT(*) FROM record_needs "
                    "WHERE record_id BETWEEN ? AND ? GROUP BY need_slug",
                    (st, lo, hi))
    con.execute("INSERT INTO state_need_counts "
                "SELECT '*', need_slug, COUNT(*) FROM record_needs "
                "GROUP BY need_slug")
    con.execute("INSERT INTO state_region_counts "
                "SELECT filing_state, region, COUNT(*) FROM filings "
                "WHERE region IS NOT NULL AND region <> '' "
                "GROUP BY filing_state, region")
    con.commit()

    all_dates = [d for d in state_min.values() if d]
    all_max = [d for d in state_max.values() if d]
    meta_rows = {
        "dataset_meta": json.dumps(dataset_meta),
        "total_records": str(total),
        "states": json.dumps(sorted(counts)),
        "records_by_state": json.dumps(counts),
        "min_date": min(all_dates) if all_dates else "",
        "max_date": max(all_max) if all_max else "",
        "state_min_dates": json.dumps(state_min),
        "state_max_dates": json.dumps(state_max),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "classifier": CLASSIFY.SOURCE,
    }
    con.executemany("INSERT INTO meta (key, value) VALUES (?,?)",
                    sorted(meta_rows.items()))
    con.commit()
    con.close()

    stats = {
        "db": str(out_path),
        "db_bytes": out_path.stat().st_size,
        "record_json_compressed": bool(compress),
        "total_records": total,
        "records_by_state": counts,
        "state_min_dates": state_min,
        "state_max_dates": state_max,
        "global_min_date": meta_rows["min_date"] or None,
        "global_max_date": meta_rows["max_date"] or None,
        "build_seconds": round(time.time() - t0, 1),
    }
    sidecar = out_path.with_suffix(out_path.suffix + ".stats.json")
    sidecar.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(f"done: {total} records, {len(counts)} states "
          f"({', '.join(sorted(counts))}), db={stats['db_bytes'] / 1e6:.0f} MB, "
          f"{stats['build_seconds']}s; stats -> {sidecar.name}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "seed.json"),
                    help="seed JSON; data/states/*.json beside it are merged")
    ap.add_argument("--out", default=str(ROOT / "data" / "filings.db"))
    ap.add_argument("--compress", action="store_true",
                    help="store record_json zlib-compressed (deploy "
                         "builds; the server decompresses on read, "
                         "responses unchanged). Default off so the "
                         "canonical store stays plain JSON text.")
    args = ap.parse_args()
    build(args.data, args.out, compress=args.compress)
    return 0


if __name__ == "__main__":
    sys.exit(main())
