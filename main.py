"""RNEWS (Robot NEWS) — CLI entry point.

Pipeline: collect -> dedupe -> tag -> rank -> mark-seen -> (daily filter) ->
summarize -> save -> markdown report -> static site.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from collectors import arxiv_collector, github_collector, news_collector
from processors.deduplicator import deduplicate
from processors.ranker import rank_items
from processors.summarizer import make_summarizer, summarize_items
from processors.tagger import tag_items
from site_builder import build_site
from utils.io import Item, ensure_dir, load_json, save_json, save_text
from utils.state import SeenStore

log = logging.getLogger("rnews")

PRIORITY_LABELS = {
    "must_read": "High",
    "save_for_later": "Mid",
    "low_priority": "Low",
}


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_mode_overrides(cfg: dict[str, Any], mode: str) -> dict[str, Any]:
    """Daily mode uses a moderate window (not 1-day) because:
      - arXiv doesn't post on weekends; Fri→Mon is a 3-day gap.
      - "What's new today" is enforced separately by SeenStore + the daily filter
        on the markdown report. The window is just a collection budget.
    """
    if mode == "daily":
        cfg.setdefault("sources", {}).setdefault("arxiv", {})["days_back"] = 5
        cfg["sources"].setdefault("github", {})["days_back"] = 14
    return cfg


def run_collectors(cfg: dict[str, Any], source: str) -> list[Item]:
    items: list[Item] = []
    if source in ("all", "arxiv"):
        items.extend(arxiv_collector.collect(cfg))
    if source in ("all", "github"):
        items.extend(github_collector.collect(cfg))
    if source in ("all", "news"):
        items.extend(news_collector.collect(cfg))
    return items


def annotate_first_seen(items: list[Item], cfg: dict[str, Any]) -> SeenStore:
    """Stamp every item with its first-seen date and persist the state."""
    state_path = cfg.get("state", {}).get("seen_path", "data/state/seen.json")
    seen = SeenStore(state_path)
    for it in items:
        fs = seen.mark_seen(it.id)
        it.extra["first_seen"] = fs
    seen.save()
    return seen


def _format_item_md(it: Item, *, kind: str) -> str:
    lines = [f"### {it.title}"]
    lines.append(f"- Source: {it.source}")
    if it.authors:
        author_str = ", ".join(it.authors[:6]) + (" et al." if len(it.authors) > 6 else "")
        lines.append(f"- Authors: {author_str}")
    year = (it.published or "")[:4]
    if year:
        lines.append(f"- Year: {year}")
    lines.append(f"- Link: {it.url}")
    if kind == "github":
        stars = it.extra.get("stars")
        if stars is not None:
            lines.append(f"- Stars: {stars}")
        lang = it.extra.get("language")
        if lang:
            lines.append(f"- Language: {lang}")
        lines.append(f"- Last updated: {(it.updated or '')[:10]}")
    if it.tags:
        lines.append(f"- Tags: {' '.join(it.tags)}")
    lines.append(f"- Relevance score: {it.score}")
    why = _why_it_matters(it)
    if why:
        lines.append(f"- Why it matters: {why}")
    if it.summary:
        lines.append(f"- Short summary: {it.summary}")
    return "\n".join(lines)


def _why_it_matters(it: Item) -> str:
    reasons: list[str] = []
    bd = it.score_breakdown or {}
    if bd.get("research_interest_title", 0) or bd.get("research_interest_abstract", 0):
        reasons.append("matches your research interests")
    if bd.get("real_robot", 0):
        reasons.append("real-robot experiment")
    if bd.get("has_code", 0) and it.source != "github":
        reasons.append("code released")
    if it.source == "github":
        stars = it.extra.get("stars", 0)
        if stars >= 1000:
            reasons.append(f"popular repo ({stars}★)")
        elif stars >= 100:
            reasons.append(f"actively starred ({stars}★)")
    if bd.get("recency", 0) >= 1.5:
        reasons.append("very recent")
    return "; ".join(reasons)


def _summarize_trends(items: list[Item], top_n: int = 40) -> str:
    pool = items[:top_n]
    tally: dict[str, int] = {}
    for it in pool:
        for t in it.tags:
            tally[t] = tally.get(t, 0) + 1
    if not tally:
        return "_No tag signal in this batch._"
    ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return "Most common tags in top results: " + ", ".join(f"{t} ({n})" for t, n in ranked)


def build_report(items: list[Item], cfg: dict[str, Any], mode: str) -> str:
    rep_cfg = cfg.get("report", {})
    top_papers_n = int(rep_cfg.get("top_papers", 15))
    top_repos_n = int(rep_cfg.get("top_repos", 10))

    papers = [it for it in items if it.source == "arxiv"][:top_papers_n]
    repos = [it for it in items if it.source == "github"][:top_repos_n]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = "RNEWS " + ("Daily" if mode == "daily" else "Weekly") + " Report"

    parts = [f"# {title}", f"_Generated: {today} (UTC)_", ""]
    parts.append(f"- arXiv papers in this report: {sum(1 for i in items if i.source == 'arxiv')}")
    parts.append(f"- GitHub repos in this report: {sum(1 for i in items if i.source == 'github')}")
    parts.append("")

    parts.append("## 1. Top Papers")
    parts.append("")
    if papers:
        for it in papers:
            parts.append(_format_item_md(it, kind="arxiv"))
            parts.append("")
    else:
        parts.append("_No papers matched the filter._\n")

    parts.append("## 2. Top GitHub Repositories")
    parts.append("")
    if repos:
        for it in repos:
            parts.append(_format_item_md(it, kind="github"))
            parts.append("")
    else:
        parts.append("_No repos matched the filter._\n")

    parts.append("## 3. Notable Trends")
    parts.append("")
    parts.append(_summarize_trends(items))
    parts.append("")

    parts.append("## 4. Recommended Reading Priority")
    parts.append("")
    for bucket in ("must_read", "save_for_later", "low_priority"):
        chosen = [it for it in items if it.priority == bucket]
        parts.append(f"### {PRIORITY_LABELS[bucket]} ({len(chosen)})")
        if not chosen:
            parts.append("_None._")
        else:
            for it in chosen[:20]:
                tag_str = " " + " ".join(it.tags) if it.tags else ""
                parts.append(f"- [{it.title}]({it.url}) — score {it.score}{tag_str}")
        parts.append("")

    return "\n".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RNEWS — collect and summarize Robot AI / Robot Learning updates.")
    p.add_argument("--mode", choices=["daily", "weekly"], default="weekly",
                   help="`daily` shrinks lookback windows; `weekly` uses config defaults.")
    p.add_argument("--source", choices=["all", "arxiv", "github", "news"], default="all")
    p.add_argument("--output", choices=["markdown", "json", "both"], default="both")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--no-filter", action="store_true",
                   help="Skip the must-match keyword pre-filter (useful for debugging).")
    p.add_argument("--no-site", action="store_true",
                   help="Skip rebuilding the static HTML site at the end.")
    p.add_argument("--site-only", action="store_true",
                   help="Skip collection; just rebuild the site from existing data.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)

    if args.site_only:
        out = build_site(cfg)
        print(f"Site rebuilt at {out}/")
        return 0

    cfg = apply_mode_overrides(cfg, args.mode)

    log.info("Collecting from source=%s, mode=%s", args.source, args.mode)
    raw_items = run_collectors(cfg, args.source)
    log.info("Collected %d raw items", len(raw_items))

    if not raw_items:
        log.warning("No items collected; check network / config / rate limits.")
        # Still attempt to rebuild the site from whatever data already exists.
        if not args.no_site:
            build_site(cfg)
        return 1

    items = deduplicate(raw_items)
    log.info("After dedup: %d", len(items))

    items = tag_items(items, cfg)
    items = rank_items(items, cfg, filter_unrelated=not args.no_filter)
    log.info("After filter+rank: %d", len(items))

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen = annotate_first_seen(items, cfg)
    log.info("Seen-state tracks %d items total", len(seen))

    # Summarizer is config-driven (summarizer.provider). Falls back to
    # truncation if the chosen provider's key/SDK is missing, so the pipeline
    # never breaks. Original source text is preserved in extra.full_text.
    summarizer = make_summarizer(cfg)
    log.info("summarizer: %s", type(summarizer).__name__)
    items = summarize_items(items, summarizer)

    if args.mode == "daily":
        items_for_report = [it for it in items if it.extra.get("first_seen") == today_str]
        log.info("Daily filter: %d new items today (of %d processed)",
                 len(items_for_report), len(items))
    else:
        items_for_report = items

    paths = cfg.get("paths", {})
    raw_dir = ensure_dir(paths.get("raw", "data/raw"))
    processed_dir = ensure_dir(paths.get("processed", "data/processed"))
    reports_dir = ensure_dir(paths.get("reports", "reports"))

    raw_path = raw_dir / f"{today_str}_raw.json"
    processed_path = processed_dir / f"{today_str}_processed.json"
    save_json(raw_path, [it.to_dict() for it in raw_items])
    # Merge with any prior same-day snapshot so a narrower run (e.g. workflow's
    # daily mode covering only one source) never erases items from an earlier
    # broader run. Latest copy of each id wins.
    merged: dict[str, dict[str, Any]] = {}
    prior = load_json(processed_path)
    if isinstance(prior, list):
        for it in prior:
            iid = it.get("id")
            if iid:
                merged[iid] = it
    for it in items:
        merged[it.id] = it.to_dict()
    save_json(processed_path, list(merged.values()))
    log.info("Wrote %s (%d items, %d merged from prior same-day snapshot) and %s",
             processed_path, len(merged), len(merged) - len(items) if len(merged) > len(items) else 0,
             raw_path)

    if args.output in ("markdown", "both"):
        report_md = build_report(items_for_report, cfg, args.mode)
        report_name = f"{today_str}_{args.mode}_report.md"
        save_text(reports_dir / report_name, report_md)
        save_text(reports_dir / "latest_report.md", report_md)
        log.info("Wrote %s", reports_dir / report_name)

    if not args.no_site:
        site_dir = build_site(cfg)
        log.info("Site at %s/index.html", site_dir)

    print(f"Done. {len(items_for_report)} items in report; {len(items)} items persisted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
