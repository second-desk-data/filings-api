# New Business Filings API — dependency-free (Python stdlib only).
#
# Split-layer variant: the compressed deploy DB (6.63 GB) ships as
# 256 MB parts, ONE COPY PER PART, so every registry layer is a
# separate ~256 MB blob (a single monolithic blob push gets dropped
# by this network's proxy ~10 minutes into the PATCH).
# assemble-and-run.sh concatenates the parts into data/filings.db at
# container start; content is byte-identical to the monolithic build
# (sha256-verified against data/filings.deploy.db).
FROM python:3.12-slim

WORKDIR /app

COPY app/ ./app/
COPY openapi.yaml ./openapi.yaml
COPY data/db-parts/part-000 ./data/db-parts/part-000
COPY data/db-parts/part-001 ./data/db-parts/part-001
COPY data/db-parts/part-002 ./data/db-parts/part-002
COPY data/db-parts/part-003 ./data/db-parts/part-003
COPY data/db-parts/part-004 ./data/db-parts/part-004
COPY data/db-parts/part-005 ./data/db-parts/part-005
COPY data/db-parts/part-006 ./data/db-parts/part-006
COPY data/db-parts/part-007 ./data/db-parts/part-007
COPY data/db-parts/part-008 ./data/db-parts/part-008
COPY data/db-parts/part-009 ./data/db-parts/part-009
COPY data/db-parts/part-010 ./data/db-parts/part-010
COPY data/db-parts/part-011 ./data/db-parts/part-011
COPY data/db-parts/part-012 ./data/db-parts/part-012
COPY data/db-parts/part-013 ./data/db-parts/part-013
COPY data/db-parts/part-014 ./data/db-parts/part-014
COPY data/db-parts/part-015 ./data/db-parts/part-015
COPY data/db-parts/part-016 ./data/db-parts/part-016
COPY data/db-parts/part-017 ./data/db-parts/part-017
COPY data/db-parts/part-018 ./data/db-parts/part-018
COPY data/db-parts/part-019 ./data/db-parts/part-019
COPY data/db-parts/part-020 ./data/db-parts/part-020
COPY data/db-parts/part-021 ./data/db-parts/part-021
COPY data/db-parts/part-022 ./data/db-parts/part-022
COPY data/db-parts/part-023 ./data/db-parts/part-023
COPY data/db-parts/part-024 ./data/db-parts/part-024
COPY data/pack-parts/part-000 ./data/pack-parts/part-000
COPY data/pack-parts/part-001 ./data/pack-parts/part-001
COPY data/pack-parts/part-002 ./data/pack-parts/part-002
COPY data/pack-parts/part-003 ./data/pack-parts/part-003
COPY data/pack-parts/part-004 ./data/pack-parts/part-004
COPY data/pack-parts/part-005 ./data/pack-parts/part-005
COPY data/pack-parts/part-006 ./data/pack-parts/part-006
COPY data/pack-parts/part-007 ./data/pack-parts/part-007
COPY data/pack-parts/part-008 ./data/pack-parts/part-008
COPY data/pack-parts/part-009 ./data/pack-parts/part-009
COPY data/pack-parts/part-010 ./data/pack-parts/part-010
COPY packs/classify.py ./packs/
COPY api_keys.json ./api_keys.json
COPY assemble-and-run.sh ./assemble-and-run.sh
# The catalog store ships LAST, after the 256 MB DB part layers, so
# rebuilding the catalog never invalidates the parts' layer cache.
COPY data/catalog.db ./data/catalog.db

ENV HOST=0.0.0.0 \
    PORT=8791 \
    PYTHONUNBUFFERED=1

EXPOSE 8791

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8791') + '/health', timeout=4)"

ENTRYPOINT ["sh", "assemble-and-run.sh"]
