"""Extract real thumbnails for items.

- **arXiv**: download PDF, find the largest raster image on the first 3 pages
  (typically Figure 1). Fall back to rendering page 1 as a thumbnail if no
  embedded raster image qualifies. Saves to `data/thumbnails/arxiv/<id>.jpg`
  and stamps `extra.thumbnail_path` on the item (relative site path).

- **GitHub**: fetch README via the GitHub API, parse for the first
  reasonably-sized `<img>` tag. Resolve relative URLs to
  `raw.githubusercontent.com`. Stamp `extra.thumbnail` on the item (remote URL,
  browser fetches directly).

- **News**: fetch the article page and parse `og:image` meta tag. Fallback
  for news items whose RSS already had a thumbnail is a no-op (we skip them).

Idempotent: items already carrying `extra.thumbnail` or `extra.thumbnail_path`
are skipped, so a second run is cheap.
"""
from __future__ import annotations

import argparse
import html as html_lib
import io
import json
from datetime import datetime, timezone, timedelta
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Make the project root importable when run as `python scripts/extract_thumbnails.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # type: ignore  # PyMuPDF
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("thumbnails")

ARXIV_THUMB_DIR = Path("data/thumbnails/arxiv")
_IMG_TAG = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_OG_IMG = re.compile(
    r"<meta[^>]+(?:property|name)=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_OG_IMG_REV = re.compile(
    r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"']og:image[\"']",
    re.IGNORECASE,
)
# Article blurb fallback for feeds that publish title-only RSS entries.
_OG_DESC = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:description|description)[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_OG_DESC_REV = re.compile(
    r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name)=[\"'](?:og:description|description)[\"']",
    re.IGNORECASE,
)

# Heuristic threshold: skip images smaller than this — typically shields/badges.
MIN_W = 240
MIN_H = 120


# ---------------------------------------------------------------------------
# arXiv: PDF figure extraction
# ---------------------------------------------------------------------------

def _arxiv_id_from(item: dict) -> str:
    raw = item.get("id", "")
    if raw.startswith("arxiv:"):
        raw = raw.split(":", 1)[1]
    return raw.split("v")[0]


def _save_arxiv_thumb(pdf_bytes: bytes, dest: Path) -> bool:
    """Pick the largest raster image on the first 3 pages; if none qualifies,
    render page 1 as a thumbnail. Always writes JPG."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        best = None  # (pix, area)
        for pno in range(min(3, len(doc))):
            page = doc[pno]
            for img in page.get_images(full=False):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    continue
                if pix.width < MIN_W or pix.height < MIN_H:
                    continue
                area = pix.width * pix.height
                if best is None or area > best[1]:
                    best = (pix, area)
        if best is not None:
            pix = best[0]
            if pix.alpha or pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            pix.save(str(dest))
            return True
        # No usable embedded image — render page 1.
        if len(doc) > 0:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4))
            if pix.alpha or pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            pix.save(str(dest))
            return True
    finally:
        doc.close()
    return False


def extract_arxiv_thumb(item: dict, *, sleep_after: float = 3.5) -> str | None:
    aid = _arxiv_id_from(item)
    if not aid:
        return None
    pdf_url = (item.get("extra") or {}).get("pdf_url")
    if not pdf_url:
        # Fallback: build the canonical arxiv PDF URL from id
        pdf_url = f"https://arxiv.org/pdf/{aid}.pdf"

    ARXIV_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARXIV_THUMB_DIR / f"{aid}.jpg"
    rel = f"thumbnails/arxiv/{aid}.jpg"
    if dest.exists():
        return rel

    try:
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "rnews/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            pdf_bytes = r.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        log.warning("  arxiv %s: download failed (%s)", aid, exc)
        return None

    try:
        if not _save_arxiv_thumb(pdf_bytes, dest):
            return None
    except Exception as exc:
        log.warning("  arxiv %s: parse failed (%s)", aid, exc)
        return None
    finally:
        time.sleep(sleep_after)

    return rel


# ---------------------------------------------------------------------------
# GitHub: README first image
# ---------------------------------------------------------------------------

def _resolve_relative(url: str, owner: str, repo: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{url.lstrip('/')}"


def extract_github_thumb(item: dict, *, token: str | None = None) -> str | None:
    full = item.get("title", "")
    if "/" not in full:
        return None
    owner, repo = full.split("/", 1)
    headers = {
        "Accept": "application/vnd.github.html+json",
        "User-Agent": "rnews",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException as exc:
        log.warning("  github %s: README fetch failed (%s)", full, exc)
        return None
    if r.status_code == 404:
        return None
    if r.status_code == 403 and "rate limit" in r.text.lower():
        log.warning("  github %s: rate limited", full)
        time.sleep(30)
        return None
    if r.status_code != 200:
        return None

    # The HTML representation gives us a fully-rendered README.
    for m in _IMG_TAG.finditer(r.text):
        src = m.group(1)
        # Skip common badge/shield image hosts — they aren't main figures.
        lowered = src.lower()
        if any(d in lowered for d in (
            "img.shields.io", "shields.io", "badge.fury.io", "travis-ci",
            "codecov.io", "circleci", "appveyor", "github.com/badges",
            "/badge/", "/badges/", ".svg",
        )):
            continue
        return _resolve_relative(src, owner, repo)
    return None


# ---------------------------------------------------------------------------
# News: og:image
# ---------------------------------------------------------------------------

def fetch_news_meta(item: dict) -> tuple[str | None, str | None]:
    """One page fetch -> (og:image url, og:description text). Either may be None.
    The description backfills the summary for feeds that publish title-only RSS."""
    url = item.get("url")
    if not url:
        return None, None
    try:
        r = requests.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; rnews/1.0)"},
            allow_redirects=True,
        )
    except requests.RequestException:
        return None, None
    if r.status_code != 200:
        return None, None
    body = r.text
    img_url = None
    mi = _OG_IMG.search(body) or _OG_IMG_REV.search(body)
    if mi:
        img_url = mi.group(1).strip()
        if img_url.startswith("//"):
            img_url = "https:" + img_url
    desc = None
    md = _OG_DESC.search(body) or _OG_DESC_REV.search(body)
    if md:
        desc = html_lib.unescape(md.group(1).strip())
        if len(desc) < 40:  # too short to be a useful blurb
            desc = None
    return img_url, desc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _needs_thumb(item: dict) -> bool:
    extra = item.get("extra") or {}
    return not (extra.get("thumbnail") or extra.get("thumbnail_path"))


def _load_all() -> tuple[dict[str, dict], list[Path]]:
    by_id: dict[str, dict] = {}
    files: list[Path] = []
    for fp in sorted(Path("data/processed").glob("*_processed.json")):
        files.append(fp)
        for it in json.load(open(fp)):
            existing = by_id.get(it["id"])
            if existing is None or it.get("score", 0) > existing.get("score", 0):
                by_id[it["id"]] = it
    return by_id, files


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-items", type=int, default=200,
                   help="Process at most this many top-scoring items.")
    p.add_argument("--recent-days", type=int, default=14,
                   help="Also include EVERY item newer than this many days, "
                        "regardless of score — fresh papers score low (no stars/"
                        "code) and would otherwise never get a thumbnail.")
    p.add_argument("--sources", default="arxiv,github,news",
                   help="Comma-separated list of sources to process.")
    args = p.parse_args()
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}

    by_id, files = _load_all()
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        log.info("GITHUB_TOKEN found — github fetches will use it.")

    ranked = sorted(by_id.values(), key=lambda x: float(x.get("score") or 0), reverse=True)
    pool = {it["id"]: it for it in ranked[: args.max_items]}
    # Always include ALL news (regardless of score) — news scores low so it
    # falls outside top-N, but it's a primary surface and og:image fetch is
    # one cheap HTTP per item.
    if "news" in sources:
        for it in by_id.values():
            if it.get("source") == "news":
                pool[it["id"]] = it
    # Always include RECENT items of any source: the latest papers/repos rank
    # low on score, so a pure top-by-score cut leaves the freshest content
    # thumbnail-less. Extraction is idempotent, so each daily run only fetches
    # the genuinely new ones.
    if args.recent_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.recent_days)).strftime("%Y-%m-%d")
        for it in by_id.values():
            d = (it.get("published") or it.get("updated") or "")[:10]
            if d and d >= cutoff:
                pool[it["id"]] = it
    def _empty_summary(it: dict) -> bool:
        return not (it.get("summary") or "").strip()
    # News is fetched if it needs a thumbnail OR has no summary text (title-only
    # RSS) — one HTTP yields both the image and an og:description blurb.
    candidates = [it for it in pool.values()
                  if it.get("source") in sources
                  and (_needs_thumb(it)
                       or (it.get("source") == "news" and _empty_summary(it)))]
    # Newest first: if a run is cut short (arXiv rate-limit, timeout), the
    # freshest items — the ones a reader is most likely to see — get done.
    candidates.sort(key=lambda x: (x.get("published") or x.get("updated") or ""), reverse=True)
    log.info("Processing %d candidates (max=%d, recent=%dd + all news, sources=%s)",
             len(candidates), args.max_items, args.recent_days, sources)

    updates: dict[str, dict[str, str]] = {}  # id -> {extra_key: value}
    summary_fills: dict[str, str] = {}        # id -> backfilled summary text
    for i, it in enumerate(candidates, 1):
        src = it["source"]
        log.info("[%d/%d] %s %s: %s", i, len(candidates), src, it["id"], it["title"][:60])
        result: str | None = None
        key: str | None = None
        if src == "arxiv":
            result = extract_arxiv_thumb(it)
            key = "thumbnail_path"
        elif src == "github":
            result = extract_github_thumb(it, token=token)
            key = "thumbnail"
            time.sleep(0.5 if token else 1.5)
        elif src == "news":
            img, desc = fetch_news_meta(it)
            result, key = img, "thumbnail"
            if desc and _empty_summary(it):
                summary_fills[it["id"]] = desc
                log.info("  -> summary backfilled (%d chars)", len(desc))
            time.sleep(0.5)
        if result and key and _needs_thumb(it):
            updates[it["id"]] = {key: result}
            log.info("  -> %s", result[:90])
        else:
            log.info("  -> (no thumb)")

    if not updates and not summary_fills:
        log.info("Nothing to update.")
        return 0

    # Write updates back to every processed JSON that contains the items.
    for fp in files:
        data = json.load(open(fp))
        changed = False
        for it in data:
            up = updates.get(it["id"])
            if up:
                extra = it.setdefault("extra", {})
                for k, v in up.items():
                    extra[k] = v
                changed = True
            desc = summary_fills.get(it["id"])
            if desc and _empty_summary(it):
                it["summary"] = desc
                it.setdefault("extra", {})["full_text"] = desc
                changed = True
        if changed:
            with open(fp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("Wrote updates to %s", fp)
    log.info("Items updated: %d thumb, %d summary", len(updates), len(summary_fills))
    return 0


if __name__ == "__main__":
    sys.exit(main())
