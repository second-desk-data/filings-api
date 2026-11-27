# New Business Filings API — dependency-free (Python stdlib only).
#
# Store route: the dataset never lives in git (see .gitignore and the
# lane record STORE-ROUTE.md). The compressed deploy store ships as
# 256 MB GitHub release assets on this repo; fetch_store.py downloads
# them during the build, verifies every part and the assembled file
# against store-manifest.json, and leaves data/filings.db in place.
# The catalog store is small enough to commit and ships directly.
# The packs store rides the same manifest when a rebuilt packs.db is
# released; until then its manifest entry is marked absent and those
# routes answer their designed absent file state.
FROM python:3.12-slim

WORKDIR /app

COPY app/ ./app/
COPY openapi.yaml ./openapi.yaml
COPY packs/classify.py ./packs/
COPY api_keys.json ./api_keys.json
COPY data/catalog.db ./data/catalog.db
COPY fetch_store.py store-manifest.json ./
RUN python3 fetch_store.py

ENV HOST=0.0.0.0 \
    PORT=8791 \
    PYTHONUNBUFFERED=1

EXPOSE 8791

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8791') + '/health', timeout=4)"

ENTRYPOINT ["python3", "app/server.py"]
