"""Render a static HTML site from accumulated processed JSON snapshots.

Reads every `*_processed.json` under `paths.processed`, dedupes by item id,
and writes:
  - <site_dir>/index.html       latest items, with client-side tag/source/priority filters
  - <site_dir>/daily/<date>.html one page per day (sourced from items' first-seen date)
  - <site_dir>/feed.xml         RSS feed of the top items
  - <site_dir>/styles.css       copied verbatim from templates/
  - <site_dir>/filters.js       copied verbatim from templates/
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger("rnews.site")

PRIORITY_LABELS = {
    "must_read": "High",
    "save_for_later": "Mid",
    "low_priority": "Low",
}


def _first_seen(it: dict[str, Any], fallback_date: str = "") -> str:
    extra = it.get("extra") or {}
    fs = extra.get("first_seen")
    if fs:
        return str(fs)
    return fallback_date or (it.get("updated") or "")[:10] or (it.get("published") or "")[:10] or ""


def _annotate_is_new(items: list[dict[str, Any]], days: int = 30) -> None:
    """Stamp extra.is_new=True on items whose `published` (= creation /
    first-submission date) falls within the last N days. Drives the NEW badge
    on the card — useful for github specifically, where a 1-year-old repo with
    yesterday's typo fix would otherwise look just as fresh as a brand-new one.
    """
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - days * 86400
    for it in items:
        created = it.get("published") or it.get("updated") or ""
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.timestamp() >= cutoff:
                extra = it.setdefault("extra", {})
                extra["is_new"] = True
        except (ValueError, TypeError):
            continue


def _has_code(it: dict[str, Any]) -> bool:
    """An item is 'has-code' if it's a repo, or its ranker stamped it with the has_code bonus.

    Used by the site to push papers-without-code to the bottom of the page —
    they're still indexed and searchable, just deprioritized in the layout.
    """
    if it.get("source") == "github":
        return True
    bd = it.get("score_breakdown") or {}
    return bool(bd.get("has_code", 0))


def _apply_news_policy(
    news_items: list[dict[str, Any]],
    news_cfg: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """For news only: reassign priority by score-quota tiers, then drop items
    older than their tier's retention window.

    The user's product call: news should not be retained at equal priority
    proportions. With quota 60/30/10 (high/mid/low) and retention 60/30/10
    days respectively, the visible News section naturally skews toward High
    over time — Low ages out fast, High stays exposed.
    """
    if not news_items:
        return []
    quota = news_cfg.get("priority_quota") or {"high": 0.6, "mid": 0.3, "low": 0.1}
    retention = news_cfg.get("retention_days") or {
        "must_read": 60, "save_for_later": 30, "low_priority": 10,
    }

    sorted_news = sorted(
        news_items, key=lambda x: float(x.get("score") or 0), reverse=True
    )
    n = len(sorted_news)
    high_cut = int(round(n * float(quota.get("high", 0.6))))
    mid_cut = high_cut + int(round(n * float(quota.get("mid", 0.3))))

    kept: list[dict[str, Any]] = []
    for i, it in enumerate(sorted_news):
        if i < high_cut:
            tier = "must_read"
        elif i < mid_cut:
            tier = "save_for_later"
        else:
            tier = "low_priority"

        max_age = float(retention.get(tier, 10))
        pub = it.get("updated") or it.get("published") or ""
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() / 86400 > max_age:
                    continue
            except ValueError:
                pass

        # Shallow copy so we don't mutate the original dict shared across
        # all_items views.
        it2 = dict(it)
        it2["priority"] = tier
        kept.append(it2)
    return kept


def _load_all_processed(processed_dir: Path) -> list[dict[str, Any]]:
    """Merge every snapshot, keeping the highest-scoring copy of each item id."""
    by_id: dict[str, dict[str, Any]] = {}
    for fp in sorted(processed_dir.glob("*_processed.json")):
        date_from_name = fp.name[:10] if len(fp.name) >= 10 and fp.name[4] == "-" else ""
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("site: skipping %s: %s", fp, exc)
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            iid = it.get("id")
            if not iid:
                continue
            # Backfill first_seen using the filename date for snapshots written
            # before state tracking existed.
            extra = it.setdefault("extra", {}) or {}
            if isinstance(extra, dict) and not extra.get("first_seen") and date_from_name:
                extra["first_seen"] = date_from_name
                it["extra"] = extra
            existing = by_id.get(iid)
            if existing is None or _prefer(it, existing):
                by_id[iid] = it
    return list(by_id.values())


def _prefer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Decide whether `candidate` should replace `current` when deduping the
    same item across snapshots. An LLM summary always beats a truncation one
    (so a CI run that re-summarized with truncation can't shadow a codex
    summary from an earlier snapshot); otherwise higher score wins."""
    def rank(it: dict[str, Any]) -> int:
        return 1 if (it.get("extra") or {}).get("summary_kind") == "llm" else 0
    cr, ur = rank(candidate), rank(current)
    if cr != ur:
        return cr > ur
    return float(candidate.get("score") or 0) >= float(current.get("score") or 0)


def _rfc822(iso_dt: str) -> str:
    """Convert ISO-8601 to RFC-822 (what RSS readers expect). Defaults to 'now'."""
    if iso_dt:
        try:
            dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt)
        except ValueError:
            pass
    return format_datetime(datetime.now(timezone.utc))


def _build_env(templates_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["priority_label"] = lambda p: PRIORITY_LABELS.get(p, p or "")
    env.globals["rfc822"] = _rfc822
    return env


def build_site(cfg: dict[str, Any]) -> Path:
    site_cfg = cfg.get("site", {}) or {}
    if not site_cfg.get("enabled", True):
        log.info("site: disabled in config; skipping")
        return Path(site_cfg.get("output_dir", "site"))

    paths = cfg.get("paths", {}) or {}
    processed_dir = Path(paths.get("processed", "data/processed"))
    site_dir = Path(site_cfg.get("output_dir", "site"))
    templates_dir = Path(site_cfg.get("templates_dir", "templates"))

    if not templates_dir.exists():
        raise FileNotFoundError(f"templates_dir not found: {templates_dir}")
    if not processed_dir.exists():
        log.warning("site: processed_dir %s missing; nothing to render", processed_dir)
        return site_dir

    site_dir.mkdir(parents=True, exist_ok=True)
    # Daily archive pages were removed; clean up any stale ones from old builds.
    stale_daily = site_dir / "daily"
    if stale_daily.exists():
        shutil.rmtree(stale_daily)

    for asset in ("styles.css", "filters.js"):
        src = templates_dir / asset
        if src.exists():
            shutil.copy2(src, site_dir / asset)
    # Banner image lives at the repo root so the README can use the same file.
    banner = Path("banner.png")
    if banner.exists():
        shutil.copy2(banner, site_dir / "banner.png")

    # Copy locally-stored thumbnails (currently: arxiv figure crops produced by
    # scripts/extract_thumbnails.py) into the site so templates can reference
    # them via relative `thumbnails/...` paths.
    thumb_src = Path("data/thumbnails")
    if thumb_src.exists():
        thumb_dest = site_dir / "thumbnails"
        if thumb_dest.exists():
            shutil.rmtree(thumb_dest)
        shutil.copytree(thumb_src, thumb_dest)

    all_items = _load_all_processed(processed_dir)
    log.info("site: loaded %d unique items from %s", len(all_items), processed_dir)
    _annotate_is_new(all_items, days=30)

    # Sort by score desc, then first_seen desc as a tiebreaker. We previously
    # had first_seen as the primary key, but that buried high-quality repos
    # under freshly-backfilled papers just because the papers were "discovered
    # today". Score-first is what the user actually scans for.
    all_items.sort(key=lambda x: (float(x.get("score") or 0), _first_seen(x)), reverse=True)

    items_on_index = int(site_cfg.get("items_on_index", 80))
    news_min_slots = int(site_cfg.get("news_min_slots", 50))
    # Reserve slots for News explicitly. News items score low (no stars, no
    # has_code) so a pure top-by-score cut buries almost all of them. Take the
    # top-N non-news by score, then the most RECENT news (news is timely — we
    # sort the News section by date, not score).
    non_news = [it for it in all_items if it.get("source") != "news"][:items_on_index]
    news_raw = [it for it in all_items if it.get("source") == "news"]
    # Apply priority-quota reassignment + per-tier retention so High news stays
    # exposed longer and Low ages out first.
    news_cfg = (cfg.get("sources") or {}).get("news") or {}
    news_all = _apply_news_policy(news_raw, news_cfg, datetime.now(timezone.utc))
    news_all.sort(key=lambda x: (x.get("updated") or x.get("published") or ""), reverse=True)
    news_subset = news_all[:news_min_slots]
    seen_ids = {it.get("id") for it in non_news}
    index_items = non_news + [it for it in news_subset if it.get("id") not in seen_ids]

    # Partition into three sections, top to bottom on the page:
    #   1. Code & repos    — actionable (github items + papers with released code)
    #   2. News & articles — industry pulse (RSS sources, scored mostly by recency)
    #   3. Papers only     — research depth (arxiv without code link)
    # News needs its own bucket because it's neither "code" nor "paper" — putting
    # news in either of the existing buckets reads wrong.
    def _bucket(it: dict[str, Any]) -> str:
        if it.get("source") == "news":
            return "news"
        return "code" if _has_code(it) else "papers"

    # Default order differs by section, matching the "Score" sort chip:
    #   News  -> priority tier (High -> Mid -> Low), newest within each tier
    #            (timely content; relevance score is mostly recency anyway).
    #   Code & Papers -> relevance score desc (topic match, released code,
    #            GitHub stars/forks, real-robot, recency), date as a tiebreak.
    _PRANK = {"must_read": 2, "save_for_later": 1, "low_priority": 0}
    def _news_key(x: dict[str, Any]):
        pr = _PRANK.get(x.get("priority", ""), 0)
        date = (x.get("updated") or x.get("published") or "")
        return (pr, date)
    def _score_key(x: dict[str, Any]):
        date = (x.get("updated") or x.get("published") or "")
        return (float(x.get("score") or 0), date)
    index_with_code = sorted((it for it in index_items if _bucket(it) == "code"),
                             key=_score_key, reverse=True)
    index_news = sorted((it for it in index_items if _bucket(it) == "news"),
                        key=_news_key, reverse=True)
    index_papers_only = sorted((it for it in index_items if _bucket(it) == "papers"),
                               key=_score_key, reverse=True)

    tag_counter: Counter[str] = Counter()
    for it in index_items:
        for t in it.get("tags") or []:
            tag_counter[t] += 1
    top_tags = tag_counter.most_common(int(site_cfg.get("top_tags", 16)))

    # tag_groups in config.yaml controls both the visual grouping in the chip
    # UI (e.g. Method vs Platform) AND the independent filter dimension keys
    # (data-filter="method", data-filter="platform"). Two-dim filtering means
    # users can stack chips across groups (#VLA + #Manipulator -> intersect).
    # Tags with zero items in the current window are omitted so we don't
    # render empty chips.
    raw_groups = cfg.get("tag_groups") or []
    grouped_tag_counts: list[tuple[str, str, list[tuple[str, int]]]] = []
    if isinstance(raw_groups, list):
        for g in raw_groups:
            label = str(g.get("label") or "Tag")
            filter_key = str(g.get("filter_key") or label.lower())
            wanted = list(g.get("tags") or [])
            counts = [(t, tag_counter[t]) for t in wanted if tag_counter[t] > 0]
            if counts:
                grouped_tag_counts.append((label, filter_key, counts))

    env = _build_env(templates_dir)
    site_ctx = {
        "title": site_cfg.get("title", "RNEWS — Robot NEWS"),
        "description": site_cfg.get("description", ""),
        "url": site_cfg.get("url", ""),
    }
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # Cache-bust query string for static assets (styles.css, filters.js). Each
    # build gets a fresh value, so browsers refetch instead of serving a stale
    # cached copy when we ship a UI change.
    generated_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    index_html = env.get_template("index.html.j2").render(
        items_with_code=index_with_code,
        items_news=index_news,
        items_papers_only=index_papers_only,
        items=index_items,  # kept for back-compat if templates still reference it
        top_tags=top_tags,
        grouped_tag_counts=grouped_tag_counts,
        generated=generated,
        generated_ts=generated_ts,
        site=site_ctx,
        url_root="",
    )
    (site_dir / "index.html").write_text(index_html, encoding="utf-8")
    log.info("site: wrote index.html (%d w/code + %d news + %d papers-only)",
             len(index_with_code), len(index_news), len(index_papers_only))

    # Daily archive pages were removed — the unified index (with search +
    # filters + time chips) covers the same need without the per-day clutter.

    feed_xml = env.get_template("feed.xml.j2").render(
        items=index_items[:50], site=site_ctx, build_date=_rfc822(""),
    )
    (site_dir / "feed.xml").write_text(feed_xml, encoding="utf-8")
    log.info("site: wrote feed.xml")

    return site_dir


def _load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render the RNEWS static site.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = _load_config(args.config)
    out = build_site(cfg)
    print(f"Site written to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
