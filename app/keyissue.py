"""Self-serve free-tier API key issuance for the Second Desk API.

An agent POSTs an email address and receives a free-tier key in the
same response — no browser, no human wait. Design constraints:

* The raw key is shown exactly once, in the issuance response. At rest
  only its SHA-256 hash is stored; the raw key is never written to
  disk, never logged, and can never be shown again.
* One ACTIVE free key per email address. A second request for the same
  email answers 409 with the recovery path (email the operator to
  revoke the lost key, which frees the address for a fresh issue).
* Rate limited per email and per client IP over a rolling hour, so
  bulk key farming hits a wall long before it matters: every issued
  key is still capped by the free tier's 50 requests/day.
* The store is one JSON file with atomic replace writes, the same
  pattern as the work board. On hosts with an ephemeral filesystem it
  resets on restart (documented in AGENT-COMMERCE.md).

The issued-key entry shape matches the static key config entries
({"tier", "daily_limit", "scope"}) so the KeyStore quota logic treats
static and self-serve keys identically.
"""

import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

FREE_TIER = "free"
FREE_DAILY_LIMIT = 50
KEY_PREFIX = "sd_free_"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
EMAIL_MAX = 254

WINDOW_SECONDS = 3600
PER_EMAIL_ATTEMPTS_PER_HOUR = 5
PER_IP_ATTEMPTS_PER_HOUR = 10

CONTACT_EMAIL = "seconddesks@proton.me"


def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class KeyIssueError(Exception):
    def __init__(self, status, payload):
        super().__init__(payload.get("error", "key_issue_error"))
        self.status = status
        self.payload = payload


class IssuedKeyStore:
    """JSON-file store of self-serve issued free keys (hashed at rest)
    plus the sliding-window attempt counters used for rate limiting."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.state = {"keys": {}, "by_email": {}, "attempts": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for name in self.state:
                    if isinstance(data.get(name), dict):
                        self.state[name] = data[name]
        except (OSError, ValueError):
            pass  # missing/corrupt store starts empty; never crash boot

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state), encoding="utf-8")
        tmp.replace(self.path)

    # -- rate limiting ------------------------------------------------------
    def _attempts(self, bucket, now):
        stamps = [t for t in self.state["attempts"].get(bucket, [])
                  if now - t < WINDOW_SECONDS]
        self.state["attempts"][bucket] = stamps
        return stamps

    def _rate_check(self, email, ip, now):
        """Raise KeyIssueError(429) when either rolling-hour budget is
        spent. Returns seconds until the oldest attempt expires."""
        for bucket, cap in ((f"email:{email}", PER_EMAIL_ATTEMPTS_PER_HOUR),
                            (f"ip:{ip}", PER_IP_ATTEMPTS_PER_HOUR)):
            stamps = self._attempts(bucket, now)
            if len(stamps) >= cap:
                retry = int(WINDOW_SECONDS - (now - stamps[0])) + 1
                raise KeyIssueError(429, {
                    "error": "rate_limited",
                    "detail": "Too many key requests in the last hour.",
                    "retry_after_seconds": retry,
                    "next_step": f"Wait {retry} seconds and try again, or "
                                 f"email {CONTACT_EMAIL} for a paid plan."})

    def _record_attempt(self, email, ip, now):
        for bucket in (f"email:{email}", f"ip:{ip}"):
            self._attempts(bucket, now).append(now)

    # -- issuance -----------------------------------------------------------
    def issue(self, email, ip):
        """Validate, rate-limit, dedupe, mint. Returns the 201 payload
        (the only place the raw key ever appears)."""
        if not isinstance(email, str):
            raise KeyIssueError(400, {
                "error": "invalid_email",
                "detail": "email must be a string.",
                "next_step": "Send {\"email\": \"you@example.com\"}."})
        email = email.strip().lower()
        if (len(email) > EMAIL_MAX or not EMAIL_RE.match(email)
                or any(ord(c) < 32 for c in email)):
            raise KeyIssueError(400, {
                "error": "invalid_email",
                "detail": "That does not look like a valid email address.",
                "next_step": "Send a valid email address; the key is "
                             "bound to it (one active free key each)."})
        now = time.time()
        with self.lock:
            self._rate_check(email, ip, now)
            self._record_attempt(email, ip, now)
            existing = self.state["by_email"].get(email)
            if existing and self.state["keys"].get(existing, {}).get("active"):
                self._save()
                raise KeyIssueError(409, {
                    "error": "key_already_issued",
                    "detail": "This email already has an active free key. "
                              "Keys are shown once and stored hashed, so "
                              "the original key cannot be shown again.",
                    "next_step": f"If you lost the key, email "
                                 f"{CONTACT_EMAIL} from this address to "
                                 f"revoke it; a new key can then be "
                                 f"issued here."})
            raw = KEY_PREFIX + secrets.token_urlsafe(24)
            digest = hash_key(raw)
            self.state["keys"][digest] = {
                "email": email,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime(now)),
                "active": True,
            }
            self.state["by_email"][email] = digest
            self._save()
        return {
            "api_key": raw,
            "tier": FREE_TIER,
            "daily_limit": FREE_DAILY_LIMIT,
            "scope": {"type": "all"},
            "shown_once": True,
            "next_step": "Store this key now: it is shown once and "
                         "stored hashed, so it cannot be recovered "
                         "later. Send it as the X-API-Key header.",
        }

    # -- verification -------------------------------------------------------
    def lookup(self, raw_key):
        """Return the KeyStore entry for an active issued key, or None.
        Only the hash is ever compared or kept."""
        if not raw_key:
            return None
        record = self.state["keys"].get(hash_key(raw_key))
        if not record or not record.get("active"):
            return None
        return {"tier": FREE_TIER, "daily_limit": FREE_DAILY_LIMIT,
                "scope": {"type": "all"}}

    def usage_id(self, raw_key):
        """Stable quota identity for an issued key: its hash, never the
        raw key."""
        return "issued:" + hash_key(raw_key)
