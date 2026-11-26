# New Business Filings API — dependency-free (Python stdlib only).
# Build:  docker build -t filings-api .
# Run:    docker run -p 8791:8791 -e API_KEYS_JSON='{"keys":[...]}' filings-api
#
# BEFORE building, build the SQLite stores (the image ships the
# compressed deploy DB, NOT the ~2 GB of source JSON — the server is
# disk-backed and reads only the database):
#     python3 build_db.py                              # canonical store
#     python3 build_db.py --compress --out data/filings.deploy.db
# The canonical data/filings.db stays plain-JSON (the test suite reads
# it directly); the deploy build stores record_json zlib-compressed and
# app/server.py decompresses on read — responses are byte-identical,
# the file is ~2.2 GB smaller. Rebuild both after every data refresh
# (refresh.py / new state files) and rebuild the image; the store is a
# snapshot, exactly like the JSON was.
FROM python:3.12-slim

WORKDIR /app

# App code + SQLite store + demo key store (real keys come from
# API_KEYS_JSON). packs/classify.py is the canonical industry/need
# classifier shared with the category packs — the API loads it by path
# for labels/slug validation, so it ships in the image.
COPY app/ ./app/
COPY data/filings.deploy.db ./data/filings.db
COPY packs/classify.py ./packs/
COPY api_keys.json ./api_keys.json

# HOST/PORT are read by app/server.py. The host platform may override PORT.
ENV HOST=0.0.0.0 \
    PORT=8791 \
    PYTHONUNBUFFERED=1

EXPOSE 8791

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8791') + '/health', timeout=4)"

CMD ["python3", "app/server.py"]
