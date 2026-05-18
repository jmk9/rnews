"""Collect recent arXiv preprints in the configured categories.

Uses the official `arxiv` Python wrapper which respects arXiv's rate limit
(1 request / 3 s) and handles pagination for us.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.io import Item

log = logging.getLogger(__name__)


def _to_item(result: Any) -> Item:
    arxiv_id = result.get_short_id().split("v")[0]
    return Item(
        id=f"arxiv:{arxiv_id}",
        source="arxiv",
        title=(result.title or "").strip().replace("\n", " "),
        url=result.entry_id,
        summary=(result.summary or "").strip().replace("\n", " "),
        published=result.published.isoformat() if result.published else "",
        updated=result.updated.isoformat() if result.updated else "",
        authors=[a.name for a in (result.authors or [])],
        extra={
            "categories": list(result.categories or []),
            "primary_category": result.primary_category,
            "pdf_url": result.pdf_url,
            "comment": result.comment,
        },
    )


def collect(cfg: dict[str, Any]) -> list[Item]:
    """Fetch papers per category. Items merged into a single list (dedup happens later)."""
    try:
        import arxiv  # type: ignore
    except ImportError:
        log.error("`arxiv` package not installed. Run: pip install -r requirements.txt")
        return []

    src = cfg.get("sources", {}).get("arxiv", {})
    if not src.get("enabled", True):
        return []

    categories: list[str] = src.get("categories") or ["cs.RO"]
    days_back: int = int(src.get("days_back", 7))
    max_results: int = int(src.get("max_results_per_category", 100))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    items: list[Item] = []

    for cat in categories:
        log.info("arxiv: querying %s (last %d days, up to %d results)", cat, days_back, max_results)
        search = arxiv.Search(
            query=f"cat:{cat}",
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            for result in client.results(search):
                pub = result.published
                if pub and pub < cutoff:
                    # arxiv returns newest-first, so once we cross the cutoff we can stop
                    break
                items.append(_to_item(result))
        except Exception as exc:  # arxiv lib raises a variety of network/parse errors
            log.warning("arxiv: query for %s failed: %s", cat, exc)
            continue

    log.info("arxiv: collected %d items across %d categories", len(items), len(categories))
    return items
