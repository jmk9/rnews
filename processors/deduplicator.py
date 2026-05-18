"""Remove duplicate items.

Strategy:
1. Exact dedup by item.id (stable across runs).
2. Then a normalized-title dedup so the same paper appearing as cs.RO + cs.LG
   doesn't get counted twice. Within a duplicate group, keep the higher-scored
   item if scores are set, otherwise the first seen.
"""
from __future__ import annotations

import re

from utils.io import Item

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def _normalize_title(title: str) -> str:
    return _PUNCT_RE.sub(" ", title.lower()).strip()


def deduplicate(items: list[Item]) -> list[Item]:
    by_id: dict[str, Item] = {}
    for it in items:
        if it.id not in by_id:
            by_id[it.id] = it

    by_title: dict[str, Item] = {}
    for it in by_id.values():
        key = f"{it.source}|{_normalize_title(it.title)}"
        existing = by_title.get(key)
        if existing is None or it.score > existing.score:
            by_title[key] = it

    return list(by_title.values())
