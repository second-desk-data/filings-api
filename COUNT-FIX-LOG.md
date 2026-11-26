# Count Fix Log — Filings Pack Catalog Reconciliation

Date: 2026-11-26. Trigger: the DataBazaar bulk-listing kit (`49-databazaar-bulk/build-report.json`) found metadata counts disagreeing with actual CSV rows. This log records the complete reconciliation pass.

## Method

- Ground truth = the pack CSV files (data rows, header excluded), counted with the Python `csv` parser for every pack in the three affected files and for all full-state packs; newline counts cross-checked everywhere else.
- Every recorded count was compared: per-state index JSONs (`packs/states/<ST>/packs.json`, `full-packs.json`), `packs/PACK-CATALOG.md` (Louisiana originals), `packs/states/STATE-PACK-CATALOG.md`, and every pack folder's `README.md` cover sheet.
- Root cause: city-name casing variants in the source data (e.g. "Brooklyn" / "BROOKLYN" / "brooklyn") were built as separate index rows before the city merge; the merged pack folder kept one product, but the filtered-pack indexes (`packs.json` in CO, FL, NY) retained the stale variant rows, each carrying its own partial count. All 58 wrong counts sat on those duplicate rows; the surviving row of every group already carried the true count.
- Fix: removed the 58 stale duplicate rows from the indexes (survivor = the row whose title matches the pack folder's README title — verified in all 56 groups to be uniquely the row with the true count), then regenerated `STATE-PACK-CATALOG.md` with `write_catalog.py` from the reconciled indexes. No pack CSV was altered. No price was altered.

## Totals before → after

| Scope | Before | After |
|---|---|---|
| Index entries (8 state indexes + LA originals) | 4,699 | 4,641 |
| Colorado `packs.json` | 167 | 163 |
| Florida `packs.json` | 294 | 290 |
| New York `packs.json` | 185 | 135 |
| Catalog total SKUs | 4,699 (+ 1 national bundle) | **4,641 (+ 1 national bundle)** |
| Catalog per-state totals | FL 403 · NY 737 · CO 440 | FL 399 · NY 687 · CO 436 (CT 589, IA 523, LA 362, OR 304, PA 1,341 unchanged) |
| Publish plan | 4,650 SKUs · 930 batches · last batch 2029-06-15 | 4,592 SKUs · 919 batches · last batch 2029-06-04 |
| Publish tiers | A 48 · B 4,003 · C 598 | A 48 · B 3,979 · C 564 |

Verified clean with zero changes needed: all `full-packs.json` indexes (full-state and full-city packs), the CT/IA/LA/OR/PA filtered indexes, all 50 Louisiana-original counts in `PACK-CATALOG.md`, and all 4,699 pack README cover-sheet counts.

Note on earlier figures: the kit's "46 mismatches" counted only the first-listed variant per slug after its own dedup; the complete pass found 58 wrong rows over 56 slugs. The kit/runbook's "4,642 distinct products + bundle" was an off-by-one (it subtracted 58 from the 4,700 generator entries, which already included the synthetic bundle entry): the reconciled figure is **4,641 distinct products + 1 national bundle = 4,642 SKUs total**.

## Every correction (58 stale duplicate rows removed)

Each row below is a duplicate index row that was removed. "Old count" is what that row claimed; "true count" is the pack CSV's actual row count (the surviving index row and the pack README both carry the true count).

| # | State | Pack | Old count (removed row) | True count | Error |
|---|---|---|---|---|---|
| 1 | CO | `denver-all-new-businesses-last-90-days` | 10,878 | 1,407 | +9,471 |
| 2 | CO | `denver-needs-bookkeeping-last-90-days` | 10,246 | 1,346 | +8,900 |
| 3 | NY | `albany-all-new-businesses-last-90-days` | 8,972 | 961 | +8,011 |
| 4 | NY | `albany-needs-bookkeeping-last-90-days` | 7,389 | 961 | +6,428 |
| 5 | NY | `brooklyn-needs-bookkeeping-last-90-days` | 4,479 | 197 | +4,282 |
| 6 | NY | `brooklyn-all-new-businesses-last-90-days` | 4,479 | 198 | +4,281 |
| 7 | NY | `brooklyn-all-new-businesses-last-90-days` | 4,075 | 198 | +3,877 |
| 8 | NY | `brooklyn-needs-bookkeeping-last-90-days` | 3,661 | 197 | +3,464 |
| 9 | FL | `st-petersburg-all-new-businesses-last-90-days` | 3,481 | 1,091 | +2,390 |
| 10 | CO | `aurora-all-new-businesses-last-90-days` | 2,450 | 112 | +2,338 |
| 11 | FL | `st-petersburg-needs-bookkeeping-last-90-days` | 3,278 | 948 | +2,330 |
| 12 | CO | `aurora-needs-bookkeeping-last-90-days` | 2,345 | 101 | +2,244 |
| 13 | NY | `flushing-all-new-businesses-last-90-days` | 817 | 393 | +424 |
| 14 | NY | `flushing-needs-bookkeeping-last-90-days` | 787 | 393 | +394 |
| 15 | NY | `rochester-needs-bookkeeping-last-90-days` | 568 | 194 | +374 |
| 16 | NY | `rochester-all-new-businesses-last-90-days` | 568 | 256 | +312 |
| 17 | NY | `syracuse-all-new-businesses-last-90-days` | 272 | 103 | +169 |
| 18 | NY | `monsey-all-new-businesses-last-90-days` | 295 | 129 | +166 |
| 19 | NY | `buffalo-needs-bookkeeping-last-90-days` | 410 | 263 | +147 |
| 20 | NY | `spring-valley-needs-bookkeeping-last-90-days` | 249 | 114 | +135 |
| 21 | NY | `spring-valley-all-new-businesses-last-90-days` | 249 | 126 | +123 |
| 22 | NY | `monroe-needs-bookkeeping-last-90-days` | 241 | 118 | +123 |
| 23 | NY | `monroe-all-new-businesses-last-90-days` | 241 | 128 | +113 |
| 24 | NY | `buffalo-all-new-businesses-last-90-days` | 410 | 332 | +78 |
| 25 | NY | `astoria-all-new-businesses-last-90-days` | 225 | 151 | +74 |
| 26 | NY | `brooklyn-professional-services-last-90-days` | 199 | 135 | +64 |
| 27 | NY | `astoria-needs-bookkeeping-last-90-days` | 214 | 151 | +63 |
| 28 | NY | `yonkers-all-new-businesses-last-90-days` | 256 | 204 | +52 |
| 29 | NY | `great-neck-needs-bookkeeping-last-90-days` | 226 | 176 | +50 |
| 30 | NY | `new-york-needs-hiring-help-last-90-days` | 187 | 139 | +48 |
| 31 | NY | `new-york-all-new-businesses-last-90-days` | 3,652 | 3,607 | +45 |
| 32 | NY | `staten-island-needs-bookkeeping-last-90-days` | 579 | 537 | +42 |
| 33 | NY | `great-neck-all-new-businesses-last-90-days` | 226 | 190 | +36 |
| 34 | NY | `long-island-city-all-new-businesses-last-90-days` | 175 | 141 | +34 |
| 35 | NY | `jamaica-all-new-businesses-last-90-days` | 321 | 288 | +33 |
| 36 | NY | `new-york-needs-signage-last-90-days` | 232 | 201 | +31 |
| 37 | NY | `bronx-needs-signage-last-90-days` | 129 | 101 | +28 |
| 38 | NY | `yonkers-needs-bookkeeping-last-90-days` | 231 | 204 | +27 |
| 39 | NY | `new-york-needs-commercial-insurance-last-90-days` | 146 | 126 | +20 |
| 40 | NY | `valley-stream-all-new-businesses-last-90-days` | 155 | 135 | +20 |
| 41 | NY | `new-york-needs-pos-system-last-90-days` | 160 | 143 | +17 |
| 42 | NY | `bronx-all-new-businesses-last-90-days` | 1,117 | 1,101 | +16 |
| 43 | NY | `jamaica-needs-bookkeeping-last-90-days` | 300 | 288 | +12 |
| 44 | NY | `valley-stream-needs-bookkeeping-last-90-days` | 147 | 135 | +12 |
| 45 | FL | `st-augustine-all-new-businesses-last-90-days` | 116 | 105 | +11 |
| 46 | NY | `white-plains-all-new-businesses-last-90-days` | 134 | 125 | +9 |
| 47 | FL | `st-augustine-needs-bookkeeping-last-90-days` | 110 | 103 | +7 |
| 48 | NY | `staten-island-all-new-businesses-last-90-days` | 579 | 578 | +1 |
| 49 | NY | `white-plains-needs-bookkeeping-last-90-days` | 112 | 125 | -13 |
| 50 | NY | `long-island-city-needs-bookkeeping-last-90-days` | 118 | 141 | -23 |
| 51 | NY | `new-york-professional-services-last-90-days` | 161 | 190 | -29 |
| 52 | NY | `brooklyn-construction-and-trades-last-90-days` | 105 | 155 | -50 |
| 53 | NY | `bronx-needs-bookkeeping-last-90-days` | 1,022 | 1,101 | -79 |
| 54 | NY | `brooklyn-needs-commercial-insurance-last-90-days` | 217 | 297 | -80 |
| 55 | NY | `brooklyn-needs-pos-system-last-90-days` | 165 | 248 | -83 |
| 56 | NY | `brooklyn-needs-hiring-help-last-90-days` | 203 | 292 | -89 |
| 57 | NY | `brooklyn-needs-signage-last-90-days` | 226 | 357 | -131 |
| 58 | NY | `new-york-needs-bookkeeping-last-90-days` | 2,125 | 3,607 | -1,482 |

56 distinct packs affected: 48 in New York, 4 in Colorado, 4 in Florida. Two packs (`brooklyn-all-new-businesses-last-90-days`, `brooklyn-needs-bookkeeping-last-90-days`) had two stale rows each (three casing variants); all others had one.
