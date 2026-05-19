"""One-off historical backfill via monthly arXiv queries.

The plain `cat:cs.RO` query has to paginate from newest backwards, which means
deep history requires many pages and reliably trips arXiv's 429 limiter. By
splitting into monthly `submittedDate:[YYYYMMDDHHMM TO YYYYMMDDHHMM]` queries
we keep each query well under arXiv's per-page-budget patience, and we can
sleep generously between chunks.

This script is **idempotent**: it feeds into the same pipeline (tagger/ranker/
merge-on-save) main.py uses, so dupes vs. existing data are handled.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import arxiv  # type: ignore
import yaml

# Make the project root importable when run as `python scripts/backfill_by_month.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.arxiv_collector import _to_item  # noqa: E402
from processors.deduplicator import deduplicate  # noqa: E402
from processors.ranker import rank_items  # noqa: E402
from processors.tagger import tag_items  # noqa: E402
from utils.io import Item, ensure_dir, load_json, save_json  # noqa: E402
from utils.state import SeenStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("backfill")


def month_windows(start_months_ago: int, end_months_ago: int) -> list[tuple[datetime, datetime]]:
    """Yield (start, end) UTC pairs from old to recent."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    windows: list[tuple[datetime, datetime]] = []
    for n in range(start_months_ago, end_months_ago - 1, -1):
        start = today - timedelta(days=30 * n)
        end = today - timedelta(days=30 * (n - 1)) if n > 0 else today
        windows.append((start, end))
    return windows


def arxiv_query(cat: str, start: datetime, end: datetime) -> str:
    return (
        f"cat:{cat} AND submittedDate:"
        f"[{start.strftime('%Y%m%d%H%M')} TO {end.strftime('%Y%m%d%H%M')}]"
    )


def main() -> int:
    cfg = yaml.safe_load(open("config.yaml")) or {}
    categories = ["cs.RO"]  # robotics-dense — cleanest signal for the budget
    delay = 12.0             # generous arXiv delay
    page_size = 100
    pause_between_months = 6.0

    # 4 -> 12 months ago = covers the gap left by the recent 90 days we already have.
    windows = month_windows(start_months_ago=12, end_months_ago=4)
    log.info("Will pull %d monthly windows: %s -> %s",
             len(windows), windows[0][0].date(), windows[-1][1].date())

    client = arxiv.Client(page_size=page_size, delay_seconds=delay, num_retries=5)
    raw_items: list[Item] = []
    for cat in categories:
        for start, end in windows:
            q = arxiv_query(cat, start, end)
            log.info("query %s %s..%s", cat, start.date(), end.date())
            search = arxiv.Search(
                query=q,
                max_results=2000,  # well above any single-month density
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )
            try:
                count = 0
                for result in client.results(search):
                    raw_items.append(_to_item(result))
                    count += 1
                log.info("  -> %d items", count)
            except Exception as exc:
                log.warning("  failed: %s", exc)
            time.sleep(pause_between_months)

    log.info("collected %d raw items total", len(raw_items))
    if not raw_items:
        log.error("nothing collected; aborting")
        return 1

    items = deduplicate(raw_items)
    log.info("after dedup: %d", len(items))
    items = tag_items(items, cfg)
    items = rank_items(items, cfg, filter_unrelated=True)
    log.info("after filter+rank: %d", len(items))

    # State + first_seen — fold these in like a normal run.
    seen = SeenStore(cfg.get("state", {}).get("seen_path", "data/state/seen.json"))
    for it in items:
        fs = seen.mark_seen(it.id)
        it.extra["first_seen"] = fs
    seen.save()

    # Merge into today's processed file the same way main.py does.
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    processed_dir = ensure_dir(cfg.get("paths", {}).get("processed", "data/processed"))
    processed_path = processed_dir / f"{today_str}_processed.json"
    merged: dict[str, dict] = {}
    prior = load_json(processed_path)
    if isinstance(prior, list):
        for it in prior:
            iid = it.get("id")
            if iid:
                merged[iid] = it
    for it in items:
        merged[it.id] = it.to_dict()
    save_json(processed_path, list(merged.values()))
    log.info("wrote %s — %d items total in file", processed_path, len(merged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
