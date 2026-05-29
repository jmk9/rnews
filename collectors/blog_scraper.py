"""Scrape new posts from robotics-company blogs that don't expose RSS.

The pipeline already handles RSS via ``news_collector``. For sites built on
Webflow / Squarespace / Next.js etc. that don't ship a feed, this scraper
takes a blog index URL, finds new article links, then fetches each article
for its ``og:*`` metadata and emits one news ``Item`` per post.

Designed to be cheap and respectful: a single index fetch + one fetch per
article URL, with a small sleep between requests.

Config (``config.yaml`` under ``sources.blog_scraper``)::

    blog_scraper:
      enabled: true
      max_per_site: 8     # cap fresh articles considered per index (newest-on-top)
      sites:
        - name: "Genesis AI"
          index_url: "https://www.genesis.ai/blog"
          link_pattern: "^/blog/[^/?#]+$"
          trusted: true
"""
from __future__ import annotations

import hashlib
import html as html_lib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from utils.io import Item

log = logging.getLogger("rnews.blog")

# Meta tag regexes — accept (property|name) on either side of (content).
def _meta_rx(prop: str) -> re.Pattern[str]:
    p = re.escape(prop)
    return re.compile(
        r'<meta[^>]+(?:property|name)=["\'](?:' + p + r')["\'][^>]+content=["\']([^"\']+)["\']'
        r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:' + p + r')["\']',
        re.IGNORECASE,
    )


_OG_TITLE = _meta_rx("og:title")
# Fall back from og:description to plain meta description if absent.
_OG_DESC = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    re.IGNORECASE,
)
_OG_IMG = _meta_rx("og:image")
_PUB_TIME = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|datePublished)["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|datePublished)["\']',
    re.IGNORECASE,
)
_TITLE_TAG = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_HREF = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)


def _fetch(url: str, timeout: int = 20) -> str | None:
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; rnews/1.0)"},
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        log.warning("blog: fetch failed %s: %s", url, exc)
        return None
    if r.status_code != 200:
        log.warning("blog: %s -> HTTP %d", url, r.status_code)
        return None
    return r.text


def _first_group(m: re.Match[str] | None) -> str:
    if not m:
        return ""
    # Our alternation regexes capture into either group 1 or 2 — return whichever matched.
    for g in m.groups():
        if g:
            return html_lib.unescape(g.strip())
    return ""


def _stable_id(url: str, title: str) -> str:
    seed = (url or "") + "|" + (title or "")
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _find_article_urls(
    index_html: str, index_url: str, pattern: re.Pattern[str]
) -> list[str]:
    """Return unique same-host article URLs from the index page matching pattern."""
    index_parsed = urlparse(index_url)
    index_path = index_parsed.path.rstrip("/")
    seen: set[str] = set()
    out: list[str] = []
    for m in _HREF.finditer(index_html):
        href = m.group(1).strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = urljoin(index_url, href)
        parsed = urlparse(full)
        if parsed.netloc != index_parsed.netloc:
            continue
        if not pattern.search(parsed.path):
            continue
        if parsed.path.rstrip("/") == index_path:
            continue
        # Strip query/fragment so /blog/foo and /blog/foo?utm=... collapse.
        canon = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return out


def _scrape_site(site: dict[str, Any], max_per_site: int) -> list[Item]:
    name = str(site.get("name") or "unknown")
    index_url = str(site.get("index_url") or "")
    pattern_str = str(site.get("link_pattern") or r"/blog/[^/?#]+$")
    trusted = bool(site.get("trusted", True))
    if not index_url:
        log.warning("blog: %s missing index_url; skipping", name)
        return []
    pattern = re.compile(pattern_str)

    log.info("blog: %s -> fetching index %s", name, index_url)
    index_html = _fetch(index_url)
    if not index_html:
        return []
    urls = _find_article_urls(index_html, index_url, pattern)
    if not urls:
        log.warning("blog: %s -> no article links matched pattern %s", name, pattern_str)
        return []
    urls = urls[:max_per_site]
    log.info("blog: %s -> %d article URLs (after pattern + cap)", name, len(urls))

    items: list[Item] = []
    for url in urls:
        time.sleep(0.6)  # polite pacing between same-site fetches
        body = _fetch(url)
        if not body:
            continue
        title = _first_group(_OG_TITLE.search(body))
        if not title:
            # Last-resort fallback: <title>
            t = _TITLE_TAG.search(body)
            title = html_lib.unescape(t.group(1).strip()) if t else ""
        if not title:
            log.warning("blog: %s -> no title at %s", name, url)
            continue
        desc = _first_group(_OG_DESC.search(body))
        thumb = _first_group(_OG_IMG.search(body))
        if thumb and thumb.startswith("//"):
            thumb = "https:" + thumb
        published = _first_group(_PUB_TIME.search(body))
        # Most Next.js/Webflow blog templates don't ship article:published_time
        # meta. Without a date the site builder's news section sort puts these
        # at the bottom and the `news_min_slots` cap drops them entirely. Fall
        # back to discovery time — merge-on-save in main.py preserves the
        # original timestamp on subsequent runs, so the date is stable.
        if not published:
            published = datetime.now(timezone.utc).isoformat()

        extra: dict[str, Any] = {"feed": name, "blog_scraper": True}
        if trusted:
            extra["trusted"] = True
        if thumb:
            extra["thumbnail"] = thumb
        if desc:
            # Treat og:description as the source text the summarizer will work on.
            extra["full_text"] = desc

        items.append(
            Item(
                id=f"news:{_stable_id(url, title)}",
                source="news",
                title=title,
                url=url,
                summary=desc,
                published=published or "",
                updated=published or "",
                authors=[],
                extra=extra,
            )
        )
    log.info("blog: %s -> %d items emitted", name, len(items))
    return items


def collect(cfg: dict[str, Any]) -> list[Item]:
    src = cfg.get("sources", {}).get("blog_scraper", {})
    if not src.get("enabled", True):
        return []
    sites = src.get("sites") or []
    if not sites:
        log.info("blog: no sites configured; skipping")
        return []
    max_per_site = int(src.get("max_per_site", 8))
    items: list[Item] = []
    for site in sites:
        items.extend(_scrape_site(site, max_per_site))
    log.info("blog: collected %d items across %d sites", len(items), len(sites))
    return items
