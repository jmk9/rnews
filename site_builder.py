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

log = logging.getLogger("robot-ai-monitor.site")

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


def _has_code(it: dict[str, Any]) -> bool:
    """An item is 'has-code' if it's a repo, or its ranker stamped it with the has_code bonus.

    Used by the site to push papers-without-code to the bottom of the page —
    they're still indexed and searchable, just deprioritized in the layout.
    """
    if it.get("source") == "github":
        return True
    bd = it.get("score_breakdown") or {}
    return bool(bd.get("has_code", 0))


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
            if existing is None or it.get("score", 0) >= existing.get("score", 0):
                by_id[iid] = it
    return list(by_id.values())


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
    (site_dir / "daily").mkdir(parents=True, exist_ok=True)

    for asset in ("styles.css", "filters.js"):
        src = templates_dir / asset
        if src.exists():
            shutil.copy2(src, site_dir / asset)

    all_items = _load_all_processed(processed_dir)
    log.info("site: loaded %d unique items from %s", len(all_items), processed_dir)

    # Sort by score desc, then first_seen desc as a tiebreaker. We previously
    # had first_seen as the primary key, but that buried high-quality repos
    # under freshly-backfilled papers just because the papers were "discovered
    # today". Score-first is what the user actually scans for.
    all_items.sort(key=lambda x: (float(x.get("score") or 0), _first_seen(x)), reverse=True)

    items_on_index = int(site_cfg.get("items_on_index", 80))
    index_items = all_items[:items_on_index]

    # Partition the index list: actionable items (has code or repo) at the top,
    # paper-only items at the bottom. Within each section, sort by score desc.
    index_with_code = sorted(
        (it for it in index_items if _has_code(it)),
        key=lambda x: float(x.get("score") or 0), reverse=True,
    )
    index_papers_only = sorted(
        (it for it in index_items if not _has_code(it)),
        key=lambda x: float(x.get("score") or 0), reverse=True,
    )

    tag_counter: Counter[str] = Counter()
    for it in index_items:
        for t in it.get("tags") or []:
            tag_counter[t] += 1
    top_tags = tag_counter.most_common(int(site_cfg.get("top_tags", 16)))

    # Group by first-seen date for the archive.
    by_day: dict[str, list[dict[str, Any]]] = {}
    for it in all_items:
        d = _first_seen(it)
        if d:
            by_day.setdefault(d, []).append(it)
    days_sorted = sorted(by_day.keys(), reverse=True)

    env = _build_env(templates_dir)
    site_ctx = {
        "title": site_cfg.get("title", "Robot AI Monitor"),
        "description": site_cfg.get("description", ""),
        "url": site_cfg.get("url", ""),
    }
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    index_html = env.get_template("index.html.j2").render(
        items_with_code=index_with_code,
        items_papers_only=index_papers_only,
        items=index_items,  # kept for back-compat if templates still reference it
        top_tags=top_tags,
        archive_days=days_sorted,
        generated=generated,
        site=site_ctx,
        url_root="",
    )
    (site_dir / "index.html").write_text(index_html, encoding="utf-8")
    log.info("site: wrote index.html (%d w/code + %d papers-only)",
             len(index_with_code), len(index_papers_only))

    day_tpl = env.get_template("day.html.j2")
    for i, d in enumerate(days_sorted):
        day_all = sorted(by_day[d], key=lambda x: float(x.get("score") or 0), reverse=True)
        day_with_code = [it for it in day_all if _has_code(it)]
        day_papers_only = [it for it in day_all if not _has_code(it)]
        prev_day = days_sorted[i + 1] if i + 1 < len(days_sorted) else ""
        next_day = days_sorted[i - 1] if i > 0 else ""
        html = day_tpl.render(
            items_with_code=day_with_code,
            items_papers_only=day_papers_only,
            items=day_all,
            date=d, generated=generated, site=site_ctx,
            url_root="../", prev_day=prev_day, next_day=next_day,
        )
        (site_dir / "daily" / f"{d}.html").write_text(html, encoding="utf-8")
    log.info("site: wrote %d day pages", len(days_sorted))

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
    p = argparse.ArgumentParser(description="Render the Robot AI Monitor static site.")
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
