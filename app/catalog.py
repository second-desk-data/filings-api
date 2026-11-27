#!/usr/bin/env python3
"""
Sellable-catalog store for the agent commerce layer.

Read-only SQLite store built by build_catalog.py from the Second Desk
catalog site lane's register (families + every sellable pack with
prices and record counts). Disk-backed like the filings store: one
read-only connection per serving thread, no catalog data held in RAM
beyond the 18 family rows, so memory stays flat.

The catalog carries NO filings records — names, counts and prices
only — so it is deliberately not scope-restricted: any valid API key
(including a free key) may read the price list. Scope enforcement
lives on the data routes, unchanged.
"""

import json
import sqlite3
import threading


class CatalogStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._local = threading.local()
        con = self._con()
        self.meta = {k: json.loads(v) for k, v in
                     con.execute("SELECT key, value FROM meta")}
        self.families = [self._family_row(r) for r in con.execute(
            "SELECT slug, name, description, blurb, packs, records, url,"
            " fields, honesty, status FROM families ORDER BY records DESC")]
        self.slugs = [f["slug"] for f in self.families]

    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro",
                                  uri=True, check_same_thread=False)
            con.execute("PRAGMA cache_size = -1024")
            con.execute("PRAGMA temp_store = MEMORY")
            self._local.con = con
        return con

    @staticmethod
    def _family_row(r):
        slug, name, desc, blurb, packs, records, url, fields, honesty, \
            status = r
        return {
            "slug": slug,
            "name": name,
            "description": desc,
            "blurb": blurb,
            "packs": packs,
            "records": records,
            "site_url": url,
            "fields": json.loads(fields) if fields else [],
            "honesty": honesty,
            "status": status,
        }

    def family(self, slug):
        for fam in self.families:
            if fam["slug"] == slug:
                return fam
        return None

    def packs(self, slug, limit, offset):
        """(total, [pack, ...]) for one family, biggest packs first."""
        con = self._con()
        total = con.execute(
            "SELECT COUNT(*) FROM packs WHERE family_slug = ?",
            (slug,)).fetchone()[0]
        rows = con.execute(
            "SELECT id, title, records, price_monthly, price_onetime,"
            " free, state_name, kind, window, buy_url FROM packs"
            " WHERE family_slug = ?"
            " ORDER BY records DESC, title LIMIT ? OFFSET ?",
            (slug, limit, offset)).fetchall()
        return total, [{
            "id": pid,
            "title": title,
            "records": records,
            "price_monthly_usd": monthly,
            "price_onetime_usd": onetime,
            "free": bool(free),
            "state": state_name,
            "kind": kind,
            "window": window,
            "buy_url": buy_url,
        } for (pid, title, records, monthly, onetime, free,
               state_name, kind, window, buy_url) in rows]

    def document(self, api_base):
        """The single-JSON catalog document for GET /v1/catalog."""
        return {
            "business": "Second Desk",
            "site": "https://seconddesk.onrender.com/",
            "api_base": api_base,
            "generated_at": self.meta.get("generated_at"),
            "totals": {
                "families": self.meta.get("families"),
                "packs": self.meta.get("packs"),
                "records": self.meta.get("records"),
            },
            "families": [{
                "slug": f["slug"],
                "name": f["name"],
                "description": f["description"],
                "packs": f["packs"],
                "records": f["records"],
                "site_url": f["site_url"],
                "packs_endpoint": f"/v1/catalog/{f['slug']}",
            } for f in self.families],
            "how_to_buy": (
                "Read this catalog, pick a family, then read its packs "
                "at the family's packs_endpoint. Live data is queried "
                "with your API key on the data routes (see "
                "/openapi.yaml); pack files are sold through each "
                "pack's buy_url channel. Keys and plans: see "
                "/.well-known/agent.json."),
        }
