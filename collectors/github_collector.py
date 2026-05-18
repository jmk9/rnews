"""Collect GitHub repositories via the public search API.

Unauthenticated requests are rate-limited to ~10 req/min for the search endpoint.
Set `GITHUB_TOKEN` in the environment to raise that to ~30 req/min.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from utils.io import Item

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.github.com/search/repositories"


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "robot-ai-monitor"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _to_item(repo: dict[str, Any]) -> Item:
    return Item(
        id=f"github:{repo['full_name']}",
        source="github",
        title=repo["full_name"],
        url=repo["html_url"],
        summary=(repo.get("description") or "").strip(),
        published=repo.get("created_at", ""),
        updated=repo.get("pushed_at") or repo.get("updated_at", ""),
        authors=[repo.get("owner", {}).get("login", "")],
        extra={
            "stars": int(repo.get("stargazers_count", 0)),
            "forks": int(repo.get("forks_count", 0)),
            "language": repo.get("language"),
            "topics": list(repo.get("topics") or []),
            "open_issues": repo.get("open_issues_count"),
            "license": (repo.get("license") or {}).get("spdx_id"),
        },
    )


def collect(cfg: dict[str, Any]) -> list[Item]:
    src = cfg.get("sources", {}).get("github", {})
    if not src.get("enabled", True):
        return []

    queries: list[str] = src.get("queries") or []
    days_back: int = int(src.get("days_back", 90))
    per_page: int = int(src.get("per_page", 30))
    min_stars: int = int(src.get("min_stars", 0))
    pushed_since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    items: list[Item] = []
    for q in queries:
        full_q = f"{q} pushed:>={pushed_since} stars:>={min_stars}"
        params = {"q": full_q, "sort": "stars", "order": "desc", "per_page": per_page}
        log.info("github: searching %r", full_q)
        try:
            resp = requests.get(SEARCH_URL, headers=_headers(), params=params, timeout=20)
        except requests.RequestException as exc:
            log.warning("github: request failed for %r: %s", q, exc)
            continue

        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            log.warning("github: rate limited; sleeping 30s and skipping %r", q)
            time.sleep(30)
            continue
        if resp.status_code != 200:
            log.warning("github: %s for %r — %s", resp.status_code, q, resp.text[:200])
            continue

        data = resp.json()
        for repo in data.get("items", []):
            items.append(_to_item(repo))

        # Respect the per-minute search rate limit (10/min unauth, 30/min auth).
        time.sleep(6 if "Authorization" not in _headers() else 2)

    log.info("github: collected %d repos across %d queries", len(items), len(queries))
    return items
