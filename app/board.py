#!/usr/bin/env python3
"""
Work board store — jobs and self-listed resumes (agent commerce v1).

Design rules (see AGENT-COMMERCE.md for the audit):
  * Plain data only. No file uploads, no attachments, no HTML: every
    field is a length-capped string, validated and sanitized on the
    way in, and served back as JSON (json.dumps escaping makes stored
    text inert to any JSON consumer).
  * Strict whitelist validation: unknown fields, wrong JSON types,
    missing required fields, empty required fields and over-length
    fields are all rejected with a 400 before anything is stored.
  * Resumes are listed BY the candidate, for employers to find them.
    A resume post must carry "consent": true (the boolean) or it is
    rejected; nothing is scraped and nothing is stored beyond what
    the submitter sent.
  * Ownership without exposure: each entry records a SHA-256 hash of
    the posting API key (never the key itself, never serialized).
    DELETE removes an entry only when the caller's key hashes to the
    stored owner hash; a foreign or missing id answers the same 404,
    so the board leaks nothing about other posters' entries.
  * Persistence is a single JSON file written atomically (tmp file +
    os.replace) under a lock. On hosts with an ephemeral filesystem
    (Render free tier) the board resets when the service restarts —
    an honest v1 limit, documented in AGENT-COMMERCE.md.
"""

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES_PER_KIND = 10_000

JOB_FIELDS = {
    # field: (required, max length)
    "title": (True, 120),
    "company": (True, 120),
    "location": (True, 120),
    "description": (True, 4000),
    "contact": (True, 200),
}

RESUME_FIELDS = {
    "name": (True, 120),
    "role": (True, 120),
    "location": (True, 120),
    "experience": (True, 4000),
    "contact": (True, 200),
}

LONG_TEXT = {"description", "experience"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONTROL_SHORT = re.compile(r"[\x00-\x1f\x7f]")


class BoardError(Exception):
    """Validation failure: (error code, detail, extra fields)."""

    def __init__(self, error, detail, **extra):
        super().__init__(detail)
        self.payload = {"error": error, "detail": detail, **extra}


def _sanitize(value, field):
    """Strip control characters (keeping \\n/\\t in long text) and trim."""
    rx = _CONTROL if field in LONG_TEXT else _CONTROL_SHORT
    return rx.sub("", value).strip()


def validate(body, spec, kind):
    """Validate a POST body against a field spec. Returns the cleaned
    field dict or raises BoardError. `body` must already be a dict."""
    if not isinstance(body, dict):
        raise BoardError("bad_body", "Request body must be a JSON object.")
    allowed = set(spec) | ({"consent"} if kind == "resumes" else set())
    for key in body:
        if key not in allowed:
            raise BoardError("unexpected_field",
                             f"Unexpected field '{key}'.",
                             field=key)
    if kind == "resumes" and body.get("consent") is not True:
        raise BoardError(
            "consent_required",
            "A resume may only be listed by the candidate themselves: "
            "the request must carry \"consent\": true, confirming the "
            "candidate agrees to be listed for employers to find them.")
    out = {}
    for field, (required, maxlen) in spec.items():
        if field not in body:
            if required:
                raise BoardError("missing_field",
                                 f"Missing required field '{field}'.",
                                 field=field)
            continue
        value = body[field]
        if not isinstance(value, str):
            raise BoardError("invalid_field_type",
                             f"Field '{field}' must be a string.",
                             field=field)
        value = _sanitize(value, field)
        if required and not value:
            raise BoardError("empty_field",
                             f"Field '{field}' may not be empty.",
                             field=field)
        if len(value) > maxlen:
            raise BoardError("field_too_long",
                             f"Field '{field}' exceeds {maxlen} characters.",
                             field=field, max=maxlen)
        out[field] = value
    return out


def owner_hash(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def public_view(entry):
    """What GET responses show: the submitted fields only. The owner
    hash and any internal bookkeeping never leave the store."""
    return {k: v for k, v in entry.items() if k != "owner_hash"}


class BoardStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {"jobs": [], "resumes": []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for kind in self.data:
                        if isinstance(loaded.get(kind), list):
                            self.data[kind] = loaded[kind]
            except (ValueError, OSError):
                # A corrupt board file must not take the API down;
                # start empty (the file is rewritten on first post).
                self.data = {"jobs": [], "resumes": []}

    def _save_locked(self):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, kind, fields, api_key):
        with self.lock:
            entries = self.data[kind]
            if len(entries) >= MAX_ENTRIES_PER_KIND:
                raise BoardError(
                    "board_full",
                    f"The {kind} board is at its v1 capacity; "
                    "try again later.")
            entry = {
                "id": uuid.uuid4().hex,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **fields,
                "owner_hash": owner_hash(api_key),
            }
            entries.append(entry)
            self._save_locked()
            return public_view(entry)

    def list(self, kind, limit, offset, match=None):
        """Newest first. `match(entry)` filters before pagination."""
        with self.lock:
            entries = list(reversed(self.data[kind]))
        if match is not None:
            entries = [e for e in entries if match(e)]
        total = len(entries)
        page = entries[offset:offset + limit]
        return total, [public_view(e) for e in page]

    def delete(self, kind, entry_id, api_key):
        """True when removed. False for unknown AND for foreign ids —
        deliberately indistinguishable to the caller."""
        digest = owner_hash(api_key)
        with self.lock:
            entries = self.data[kind]
            for i, entry in enumerate(entries):
                if entry.get("id") == entry_id:
                    if entry.get("owner_hash") != digest:
                        return False
                    del entries[i]
                    self._save_locked()
                    return True
            return False


def keyword_match(entry, field, keyword):
    """Case-insensitive substring match of a keyword against a field."""
    if not keyword:
        return True
    return keyword.lower() in (entry.get(field) or "").lower()
