#!/usr/bin/env python3
"""Download and assemble this image's data stores at build time.

The dataset never lives in git (see .gitignore). Each store ships as
256 MB parts attached to a GitHub release of this repo, and the
committed store-manifest.json names the release tag and the sha256 of
every part and of each assembled file. This script downloads the
parts, verifies every checksum, assembles each store at its final
path, and deletes the parts, so the image layer carries only the
assembled stores. Any mismatch aborts the build.

A store whose manifest entry says "absent": true is skipped; the app
answers those routes with its designed absent file state. A required
store that fails to download or verify fails the build.
"""
import hashlib
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "store-manifest.json")
PART_BYTES = 256 * 1024 * 1024


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, want_bytes, want_sha, tries=4):
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "second-desk-store-fetch"})
            with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            if os.path.getsize(dest) == want_bytes and sha256_of(dest) == want_sha:
                return
            print(f"  part mismatch on attempt {attempt}, retrying",
                  file=sys.stderr)
        except Exception as exc:  # network or IO: retry, then give up
            print(f"  download attempt {attempt} failed: {exc}",
                  file=sys.stderr)
    raise SystemExit(f"could not fetch a verified copy of {url}")


def build_store(base, store):
    name = store["name"]
    if store.get("absent"):
        print(f"store {name}: marked absent, skipping")
        return
    out = os.path.join(HERE, store["out"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmpdir = os.path.join(HERE, f".parts-{name}")
    os.makedirs(tmpdir, exist_ok=True)
    part_paths = []
    for part in store["parts"]:
        dest = os.path.join(tmpdir, part["name"])
        url = f"{base}/{store['tag']}/{part['name']}"
        print(f"store {name}: fetching {part['name']} ({part['bytes']} bytes)")
        download(url, dest, part["bytes"], part["sha256"])
        part_paths.append(dest)
    print(f"store {name}: assembling {store['out']}")
    with open(out, "wb") as fh:
        for path in part_paths:
            with open(path, "rb") as pf:
                while True:
                    chunk = pf.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            os.remove(path)
    os.rmdir(tmpdir)
    size = os.path.getsize(out)
    if size != store["assembled_bytes"]:
        raise SystemExit(
            f"store {name}: assembled size {size} != {store['assembled_bytes']}")
    digest = sha256_of(out)
    if digest != store["assembled_sha256"]:
        raise SystemExit(f"store {name}: assembled sha256 mismatch")
    print(f"store {name}: verified {size} bytes, sha256 {digest[:16]}...")


def main():
    with open(MANIFEST) as fh:
        manifest = json.load(fh)
    base = manifest["release_base"].rstrip("/")
    for store in manifest["stores"]:
        try:
            build_store(base, store)
        except SystemExit:
            raise
        except Exception as exc:
            if store.get("required", True):
                raise SystemExit(f"store {store['name']}: failed: {exc}")
            print(f"store {store['name']}: optional, skipped after error: {exc}",
                  file=sys.stderr)
    print("all stores in place")


if __name__ == "__main__":
    main()
