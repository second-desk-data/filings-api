#!/bin/sh
# The deploy DBs ship in the image as 256 MB parts (data/db-parts/ for
# the filings store, data/pack-parts/ for the Vermarco packs store) so
# no single registry layer exceeds what a proxy-friendly push can
# carry. Assemble each once at container start, then serve.
set -e
cd /app
if [ ! -f data/filings.db ]; then
  echo "Assembling data/filings.db from $(ls data/db-parts | wc -l) parts..."
  cat data/db-parts/part-* > data/filings.db
  echo "Assembled $(wc -c < data/filings.db) bytes."
fi
if [ -d data/pack-parts ] && [ ! -f data/packs.db ]; then
  echo "Assembling data/packs.db from $(ls data/pack-parts | wc -l) parts..."
  cat data/pack-parts/part-* > data/packs.db
  echo "Assembled $(wc -c < data/packs.db) bytes."
fi
exec python3 app/server.py
