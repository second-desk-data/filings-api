#!/usr/bin/env python3
"""
v6 test battery — key store resolution order (Nov 28, 2026):

  (a) API_KEYS_JSON set            -> the env store wins, even when
                                      VAULT_KEYS_TOKEN is also set.
  (b) VAULT_KEYS_TOKEN set alone   -> the store is fetched at startup
                                      from the private vault release
                                      (a local stub stands in for the
                                      GitHub API; a fake token only).
  (b2) vault fetch fails           -> STAGE ONE: fall back to the
                                      bundled store (demo keys work,
                                      vault keys 401), /health 200.
  (c) neither variable set         -> STAGE ONE: the bundled DEMO
                                      store in api_keys.json is the
                                      fallback and answers 200.
  Stage two flips (b2) and (c) to fail closed (every key 401); the
  checks below are written for the stage being deployed and are
  updated in the stage two commit.

Boots app/server.py against the tiny v2 fixture store. No real
token, no real asset, no network beyond 127.0.0.1.

Run:  python3 tests/run_tests_v6.py
Exit code 0 = all checks passed.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import run_tests_v2 as v2  # fixture builder

DEMO = json.loads((ROOT / "api_keys.json").read_text(encoding="utf-8"))
DEMO_PRO = next(k["key"] for k in DEMO["keys"] if k["tier"] == "pro")

ENV_KEYS = {"keys": [
    {"key": "v6-env-key", "tier": "basic", "daily_limit": 100000,
     "scope": {"type": "all"}},
]}
VAULT_KEYS = {"keys": [
    {"key": "v6-vault-key", "tier": "basic", "daily_limit": 100000,
     "scope": {"type": "all"}},
    {"key": "v6-vault-city", "tier": "city", "daily_limit": 5000,
     "scope": {"type": "city", "state": "LA", "city": "Ville Platte"}},
]}
FAKE_TOKEN = "fake-vault-token"

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"  ({detail})" if detail else ""))


# ---------------------------------------------------------------------------
# Local stub for the GitHub release API
# ---------------------------------------------------------------------------

class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"

    def do_GET(self):
        if not self._auth_ok():
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/release":
            body = json.dumps({"assets": [{
                "name": "api-keys.json",
                "url": f"http://127.0.0.1:{self.server.server_port}/asset",
            }]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/asset":
            body = json.dumps(VAULT_KEYS).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


# ---------------------------------------------------------------------------
# Server boot / probe helpers
# ---------------------------------------------------------------------------

def probe(port, path, key=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 headers={"X-API-Key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def boot(port, db, extra_env):
    env = dict(os.environ)
    for name in ("API_KEYS_JSON", "VAULT_KEYS_TOKEN",
                 "VAULT_KEYS_RELEASE_URL"):
        env.pop(name, None)
    env.update(extra_env)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app" / "server.py"),
         "--db", str(db), "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            status, _ = probe(port, "/health")
            if status == 200:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    out = proc.stdout.read() if proc.stdout else ""
    raise SystemExit(f"server on {port} did not come up:\n{out}")


def stop(proc):
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out or ""


DATA = "/v1/businesses?state=LA&limit=1"


def main():
    stub = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_url = f"http://127.0.0.1:{stub.server_port}/release"

    db = v2.build_fixture()

    # (a) env store wins over the vault route -------------------------------
    proc = boot(8981, db, {"API_KEYS_JSON": json.dumps(ENV_KEYS),
                           "VAULT_KEYS_TOKEN": FAKE_TOKEN,
                           "VAULT_KEYS_RELEASE_URL": stub_url})
    try:
        s, _ = probe(8981, DATA, "v6-env-key")
        check("(a) env key works when both routes configured", s == 200,
              f"HTTP {s}")
        s, _ = probe(8981, DATA, "v6-vault-key")
        check("(a) vault key refused: env store takes precedence",
              s == 401, f"HTTP {s}")
        s, _ = probe(8981, DATA, DEMO_PRO)
        check("(a) demo pro key refused", s == 401, f"HTTP {s}")
    finally:
        stop(proc)

    # (b) vault route --------------------------------------------------------
    proc = boot(8982, db, {"VAULT_KEYS_TOKEN": FAKE_TOKEN,
                           "VAULT_KEYS_RELEASE_URL": stub_url})
    try:
        s, _ = probe(8982, DATA, "v6-vault-key")
        check("(b) vault-fetched key works", s == 200, f"HTTP {s}")
        s, body = probe(8982, "/v1/businesses?state=TX&limit=5",
                        "v6-vault-city")
        rows = body.get("results") or []
        check("(b) vault city-scoped key leaks nothing out of scope",
              s == 200 and len(rows) == 0, f"HTTP {s}, rows={len(rows)}")
        s, _ = probe(8982, DATA, "v6-env-key")
        check("(b) env key refused: not in the vault store", s == 401,
              f"HTTP {s}")
        s, _ = probe(8982, DATA, DEMO_PRO)
        check("(b) demo pro key refused", s == 401, f"HTTP {s}")
    finally:
        log = stop(proc)
    check("(b) boot log names the vault key store",
          "private vault key store" in log)

    # (b2) vault fetch failure: stage one falls back -------------------------
    proc = boot(8983, db, {"VAULT_KEYS_TOKEN": "wrong-token",
                           "VAULT_KEYS_RELEASE_URL": stub_url})
    try:
        s, _ = probe(8983, DATA, "v6-vault-key")
        check("(b2) failed fetch: vault key refused", s == 401,
              f"HTTP {s}")
        s, _ = probe(8983, DATA, DEMO_PRO)
        check("(b2) failed fetch: stage one fallback serves demo keys",
              s == 200, f"HTTP {s}")
        s, _ = probe(8983, "/health")
        check("(b2) failed fetch: /health still 200", s == 200,
              f"HTTP {s}")
    finally:
        log = stop(proc)
    check("(b2) boot log records the failure without content",
          "vault key store fetch failed" in log
          and "Falling back" in log
          and "v6-vault-key" not in log)

    # (c) neither variable: stage one bundled fallback ------------------------
    proc = boot(8984, db, {})
    try:
        s, _ = probe(8984, DATA, DEMO_PRO)
        check("(c) no store configured: stage one fallback serves "
              "demo keys", s == 200, f"HTTP {s}")
        s, _ = probe(8984, DATA)
        check("(c) no store configured: missing key refused",
              s == 401, f"HTTP {s}")
        s, _ = probe(8984, "/health")
        check("(c) no store configured: /health still 200", s == 200,
              f"HTTP {s}")
    finally:
        stop(proc)

    stub.shutdown()
    passed = sum(CHECKS)
    print(f"\n{passed}/{len(CHECKS)} v6 checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
