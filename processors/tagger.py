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

    # #Other is the catch-all for whichever group lists it (Platform): an item
    # gets #Other when it matches none of that group's *other* tags. So #Other
    # = "platform doesn't fit the named ones", independent of Method.
    fallback_siblings: set[str] = set()
    for g in cfg.get("tag_groups") or []:
        gtags = g.get("tags") or []
        if OTHER_TAG in gtags:
            fallback_siblings = {t for t in gtags if t != OTHER_TAG}
            break

    for it in items:
        text = _haystack(it)
        tags: list[str] = []
        for tag, patterns in compiled.items():
            if any(p in text for p in patterns):
                tags.append(tag)
        if fallback_siblings and not (set(tags) & fallback_siblings):
            tags.append(OTHER_TAG)
        it.tags = tags
    return items
