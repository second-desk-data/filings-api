#!/usr/bin/env python3
"""
v7 test battery — the Render conditions behind the Nov 28 stage one
failure (Nov 28, 2026):

The stage one deploy (commit d101787) booted on Render and silently
loaded the bundled demo store even though VAULT_KEYS_TOKEN had been
set in the dashboard: the running process's environment did not
carry the variable, and the boot log could not prove what the
process had actually seen. The branch logic itself was already
covered by run_tests_v6; this battery covers the deployment-shaped
conditions and the new boot diagnostics:

  (d1) ENTRYPOINT-form boot (no --keys flag, exactly as the Docker
       image starts the server) with VAULT_KEYS_TOKEN in the
       process environment -> the vault store wins, and the log
       proves it: an inputs line naming the variable present, and
       a KEY STORE SOURCE line naming the vault.
  (d2) VAULT_KEYS_TOKEN present but EMPTY (a dashboard variable
       saved with no value) -> the inputs line says
       present-but-empty, the process refuses to start (stage
       two: no demo fallback), and the boot is distinguishable
       from (d1) by its log alone.
  (d3) VAULT_KEYS_TOKEN wrapped in whitespace and a trailing
       newline (a dashboard paste artifact) -> the value is
       stripped and the vault fetch still succeeds.
  (d4) VAULT_KEYS_TOKEN wrong (the stub answers 401) -> the
       process refuses to start (stage two: no demo fallback),
       and the ERROR line names the error class (HTTPError 401)
       while the token itself never reaches the log.

Boots app/server.py against the tiny v2 fixture store and the v6
local GitHub stub. No real token, no real asset, no network beyond
127.0.0.1.

Run:  python3 tests/run_tests_v7.py
Exit code 0 = all checks passed.
"""

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import run_tests_v2 as v2  # fixture builder
import run_tests_v6 as v6  # stub GitHub API + boot/probe helpers

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"  ({detail})" if detail else ""))


DATA = "/v1/businesses?state=LA&limit=1"
VAULT_KEY = v6.VAULT_KEYS["keys"][0]["key"]


def main():
    stub = ThreadingHTTPServer(("127.0.0.1", 0), v6.StubHandler)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_url = f"http://127.0.0.1:{stub.server_port}/release"

    db = v2.build_fixture()

    # (d1) ENTRYPOINT-form boot, token in the process environment ----
    proc = v6.boot(8991, db, {"VAULT_KEYS_TOKEN": v6.FAKE_TOKEN,
                              "VAULT_KEYS_RELEASE_URL": stub_url})
    try:
        s, _ = v6.probe(8991, DATA, VAULT_KEY)
        check("(d1) entrypoint boot: vault-fetched key works",
              s == 200, f"HTTP {s}")
        s, _ = v6.probe(8991, DATA, v6.DEMO_PRO)
        check("(d1) entrypoint boot: demo pro key refused",
              s == 401, f"HTTP {s}")
    finally:
        log = v6.stop(proc)
    check("(d1) inputs line records the token as present",
          "Key store inputs: API_KEYS_JSON absent, "
          "VAULT_KEYS_TOKEN present." in log)
    check("(d1) source line names the vault store",
          "KEY STORE SOURCE: private vault key store" in log)
    check("(d1) fake token never reaches the log",
          v6.FAKE_TOKEN not in log)

    # (d2) token present but empty ------------------------------------
    code, log = v6.boot_fails(8992, db, {"VAULT_KEYS_TOKEN": "",
                                         "VAULT_KEYS_RELEASE_URL": stub_url})
    check("(d2) empty token: server refuses to start", code != 0,
          f"exit {code}")
    check("(d2) inputs line records the token as present-but-empty",
          "VAULT_KEYS_TOKEN present-but-empty." in log)
    check("(d2) log names the missing store",
          "ERROR: no key store configured" in log)
    check("(d2) no demo fallback in the log",
          "bundled demo file" not in log)
    check("(d2) /health is down with the server", not v6._up(8992))

    # (d3) token wrapped in whitespace --------------------------------
    proc = v6.boot(8993, db, {
        "VAULT_KEYS_TOKEN": f"  {v6.FAKE_TOKEN}\n",
        "VAULT_KEYS_RELEASE_URL": stub_url})
    try:
        s, _ = v6.probe(8993, DATA, VAULT_KEY)
        check("(d3) whitespace-wrapped token: vault key works",
              s == 200, f"HTTP {s}")
    finally:
        log = v6.stop(proc)
    check("(d3) source line names the vault store",
          "KEY STORE SOURCE: private vault key store" in log)

    # (d4) wrong token: fetch fails with an HTTP error -----------------
    code, log = v6.boot_fails(8994, db, {"VAULT_KEYS_TOKEN": "wrong-token",
                                         "VAULT_KEYS_RELEASE_URL": stub_url})
    check("(d4) failed fetch: server refuses to start", code != 0,
          f"exit {code}")
    check("(d4) error line names the error class",
          "ERROR: vault key store fetch failed (HTTPError 401)" in log)
    check("(d4) no demo fallback in the log",
          "bundled demo file" not in log and "Falling back" not in log)
    check("(d4) the token never reaches the log",
          "wrong-token" not in log)
    check("(d4) /health is down with the server", not v6._up(8994))

    stub.shutdown()
    passed = sum(CHECKS)
    print(f"\n{passed}/{len(CHECKS)} v7 checks passed")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
