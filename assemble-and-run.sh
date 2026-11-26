#!/bin/sh
# The deploy DB ships in the image as 256 MB parts (data/db-parts/) so no
# single registry layer exceeds what a proxy-friendly push can carry.
# Assemble it once at container start, then serve.
set -e
cd /app
if [ ! -f data/filings.db ]; then
  echo "Assembling data/filings.db from $(ls data/db-parts | wc -l) parts..."
  cat data/db-parts/part-* > data/filings.db
  echo "Assembled $(wc -c < data/filings.db) bytes."
fi
exec python3 app/server.py
