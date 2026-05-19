"""Collect robotics / AI industry news from RSS feeds.

Different from arxiv/github: news is mostly about *what people are talking
about right now* rather than what's been published or shipped. So we:

- Pull a configurable list of RSS feeds via `feedparser`.
- Keep only entries newer than `days_back` to avoid flooding the index with
  ancient articles when a feed reaches deep into history.
- Stamp `source='news'` so site_builder can route them into their own section
  and the ranker doesn't try to use github_stars / has_code (irrelevant).
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.io import Item

log = logging.getLogger(__name__)

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """RSS summaries often carry HTML. Strip it for our plain-text pipeline."""
    if not text:
        return ""
    return _WS.sub(" ", html.unescape(_HTML_TAG.sub(" ", text))).strip()


def _parse_published(entry: Any) -> str:
    """Return ISO-8601 UTC string. RSS dates vary wildly across feeds."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None) or entry.get(attr) if hasattr(entry, "get") else None
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue
    return ""


def _stable_id(url: str, title: str) -> str:
    """Some RSS feeds change their `guid` on each republish — a hash of
    URL + title is steadier across pulls and avoids surprise duplicates."""
    seed = (url or "") + "|" + (title or "")
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _to_item(entry: Any, feed_name: str) -> Item | None:
    title = _strip_html(getattr(entry, "title", "") or entry.get("title", ""))
    url = getattr(entry, "link", "") or entry.get("link", "")
    if not title or not url:
        return None

    summary = (
        _strip_html(getattr(entry, "summary", "") or entry.get("summary", ""))
        or _strip_html(getattr(entry, "description", "") or entry.get("description", ""))
    )
    published = _parse_published(entry)
    author = getattr(entry, "author", "") or entry.get("author", "")

    return Item(
        id=f"news:{_stable_id(url, title)}",
        source="news",
        title=title,
        url=url,
        summary=summary,
        published=published,
        updated=published,
        authors=[author] if author else [],
        extra={"feed": feed_name},
    )


def collect(cfg: dict[str, Any]) -> list[Item]:
    try:
        import feedparser  # type: ignore
    except ImportError:
        log.error("`feedparser` not installed. Run: pip install -r requirements.txt")
        return []

    src = cfg.get("sources", {}).get("news", {})
    if not src.get("enabled", True):
        return []

    feeds: list[dict[str, Any]] = src.get("feeds") or []
    if not feeds:
        log.info("news: no feeds configured; skipping")
        return []
    days_back: int = int(src.get("days_back", 14))
    max_per_feed: int = int(src.get("max_per_feed", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    items: list[Item] = []
    for feed in feeds:
        name = str(feed.get("name") or feed.get("url") or "unknown")
        url = str(feed.get("url") or "")
        if not url:
            continue
        log.info("news: fetching %s (%s)", name, url)
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "rnews/1.0"})
        except Exception as exc:
            log.warning("news: failed to parse %s: %s", name, exc)
            continue
        if parsed.bozo and not parsed.entries:
            log.warning("news: %s returned no entries (bozo=%s)", name, parsed.bozo_exception)
            continue

        per_feed_kept = 0
        for entry in parsed.entries[: max_per_feed * 2]:  # over-fetch then filter by date
            item = _to_item(entry, name)
            if item is None:
                continue
            if item.published:
                try:
                    dt = datetime.fromisoformat(item.published.replace("Z", "+00:00"))
                    if dt < cutoff:
                        continue
                except ValueError:
                    pass
            items.append(item)
            per_feed_kept += 1
            if per_feed_kept >= max_per_feed:
                break
        log.info("news: %s -> %d items", name, per_feed_kept)

    log.info("news: collected %d items across %d feeds", len(items), len(feeds))
    return items
