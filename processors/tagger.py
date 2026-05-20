"""Attach tags to items based on substring matches defined in config.yaml."""
from __future__ import annotations

from typing import Any

from utils.io import Item


def _haystack(item: Item) -> str:
    parts = [item.title, item.summary]
    topics = item.extra.get("topics") if isinstance(item.extra, dict) else None
    if isinstance(topics, list):
        parts.extend(topics)
    return " ".join(p for p in parts if p).lower()


OTHER_TAG = "#Other"


def tag_items(items: list[Item], cfg: dict[str, Any]) -> list[Item]:
    tag_map: dict[str, list[str]] = cfg.get("tags") or {}
    # Pre-lowercase patterns for cheaper repeated matching.
    compiled = {tag: [p.lower() for p in patterns] for tag, patterns in tag_map.items()}

    # Tags that count as "categorized" = everything declared in tag_groups
    # except the catch-all itself. An item matching none of these gets #Other,
    # so the user can filter for work that fits neither Method nor Platform.
    categorized: set[str] = set()
    for g in cfg.get("tag_groups") or []:
        for t in g.get("tags") or []:
            if t != OTHER_TAG:
                categorized.add(t)

    for it in items:
        text = _haystack(it)
        tags: list[str] = []
        for tag, patterns in compiled.items():
            if any(p in text for p in patterns):
                tags.append(tag)
        if categorized and not (set(tags) & categorized):
            tags.append(OTHER_TAG)
        it.tags = tags
    return items
