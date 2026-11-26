#!/usr/bin/env python3
"""
Slug-interface adapter over the ONE canonical classifier: packs/classify.py.

The category packs and this API must never disagree about what counts as
(e.g.) a trucking company, so there is exactly one rule set — the one in
packs/classify.py, documented in packs/CATEGORIES.md. This module loads it
by file path and adapts its interface to the slugs the API exposes:

  packs/classify.py speaks in display names and tuples:
    classify_industry(name) -> (industry_display_name, matched_keyword)
    NEED_SEGMENTS = [(name, slug, description, predicate), ...]
    predicates take a dict with keys: industry (display name), entity_type

  this adapter speaks in slugs:
    classify_industry(name) -> industry slug   (packs' own slugify())
    needs_for(record)       -> [need slugs]    (record needs 'category'
                                                slug and 'entity_type')
    INDUSTRY_SLUGS / INDUSTRY_LABELS / NEED_SLUGS / NEED_LABELS /
    NEED_DESCRIPTIONS

If packs/classify.py is missing, this module raises at import time ON
PURPOSE: a silent fallback taxonomy would make API counts quietly
disagree with the pack catalog, which is worse than a loud failure.
(The Dockerfile copies packs/classify.py into the image alongside app/.)
"""

import importlib.util
from pathlib import Path

_PACKS_FILE = Path(__file__).resolve().parent.parent / "packs" / "classify.py"

if not _PACKS_FILE.exists():
    raise ImportError(
        f"Canonical classifier not found at {_PACKS_FILE}. The API and the "
        "category packs share packs/classify.py by design; restore it "
        "instead of falling back to a divergent taxonomy.")

_spec = importlib.util.spec_from_file_location("packs_classify", _PACKS_FILE)
packs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packs)

SOURCE = "packs/classify.py (via app/classify.py adapter)"

INDUSTRY_LABELS = {packs.slugify(name): name for name in packs.INDUSTRY_NAMES}
INDUSTRY_SLUGS = list(INDUSTRY_LABELS)
NEED_LABELS = {slug: name for name, slug, _desc, _pred in packs.NEED_SEGMENTS}
NEED_DESCRIPTIONS = {slug: desc for _name, slug, desc, _pred in packs.NEED_SEGMENTS}
NEED_SLUGS = [slug for _name, slug, _desc, _pred in packs.NEED_SEGMENTS]
_PREDICATES = [(slug, pred) for _name, slug, _desc, pred in packs.NEED_SEGMENTS]


def classify_industry(name):
    """Industry slug for a business name, per the packs classifier."""
    label, _keyword = packs.classify_industry(name or "")
    return packs.slugify(label)


def industry_label(slug):
    return INDUSTRY_LABELS[slug]


def needs_for(record):
    """Likely-need slugs for a record.

    The record must already carry 'category' (an industry slug from
    classify_industry above) and 'entity_type'; the packs predicates are
    fed the display-name industry they expect.
    """
    label = INDUSTRY_LABELS[record["category"]]
    probe = {"industry": label, "entity_type": record.get("entity_type")}
    return [slug for slug, pred in _PREDICATES if pred(probe)]


def record_agent_is_commercial(record):
    """Formation-mill flag for a record (packs/classify.py is canonical):
    True when the registered agent's name matches a known commercial
    formation/registered-agent mill. Name-matched only, additive only —
    the underlying record is never altered or dropped."""
    return packs.record_agent_is_commercial(record)
