"""Fetch README text for top github items so summaries have real material.

A repo's one-line description is too thin to summarize ("Awesome list of X").
The README has the actual capability/contribution. This pulls the raw README
markdown via the GitHub API, strips it to plain text, stores it in
`extra.readme` AND `extra.full_text`, and clears `summary_kind` so the next
llm_summarize pass re-summarizes from the richer text.

Unauthenticated GitHub API is 60 req/hour, so `--max-items` defaults to 50.
Set GITHUB_TOKEN to raise the limit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("enrich-readme")

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>]+>")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_SYMS = re.compile(r"[#*`>_~|]+")
_WS = re.compile(r"\s+")


def clean_markdown(md: str) -> str:
    md = _CODE_BLOCK.sub(" ", md)
    md = _MD_IMAGE.sub(" ", md)
    md = _HTML_TAG.sub(" ", md)
    md = _MD_LINK.sub(r"\1", md)
    md = _MD_SYMS.sub(" ", md)
    md = _WS.sub(" ", md)
    return md.strip()


def fetch_readme(owner: str, repo: str, token: str | None) -> str | None:
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "rnews"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/readme",
                         headers=headers, timeout=20)
    except requests.RequestException as exc:
        log.warning("  %s/%s: fetch failed (%s)", owner, repo, exc)
        return None
    if r.status_code == 403 and "rate limit" in r.text.lower():
        log.warning("  rate limited at %s/%s", owner, repo)
        return "__RATE_LIMIT__"
    if r.status_code != 200:
        return None
    return r.text


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-items", type=int, default=50)
    p.add_argument("--max-chars", type=int, default=4000)
    args = p.parse_args()
    token = os.environ.get("GITHUB_TOKEN")

    by_id: dict[str, dict] = {}
    files: list[Path] = []
    for fp in sorted(Path("data/processed").glob("*_processed.json")):
        files.append(fp)
        for it in json.load(open(fp)):
            ex = by_id.get(it["id"])
            if ex is None or float(it.get("score") or 0) > float(ex.get("score") or 0):
                by_id[it["id"]] = it

    gh = [it for it in by_id.values() if it.get("source") == "github"]
    gh.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    # Skip ones that already have README text captured.
    gh = [it for it in gh if not (it.get("extra") or {}).get("readme")][: args.max_items]
    log.info("Fetching README for %d github items", len(gh))

    updates: dict[str, str] = {}
    for i, it in enumerate(gh, 1):
        owner, _, repo = it["title"].partition("/")
        if not repo:
            continue
        log.info("[%d/%d] %s", i, len(gh), it["title"])
        md = fetch_readme(owner, repo, token)
        if md == "__RATE_LIMIT__":
            log.warning("Stopping early due to rate limit.")
            break
        if not md:
            continue
        text = clean_markdown(md)[: args.max_chars]
        if len(text) > 80:
            updates[it["id"]] = text
        time.sleep(0.5 if token else 1.1)

    if not updates:
        log.info("No README text captured.")
        return 0

    for fp in files:
        data = json.load(open(fp))
        changed = False
        for it in data:
            t = updates.get(it["id"])
            if t:
                extra = it.setdefault("extra", {})
                extra["readme"] = t
                extra["full_text"] = t          # summarizer reads this
                extra.pop("summary_kind", None)  # force re-summary from README
                changed = True
        if changed:
            with open(fp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("Updated %s", fp)
    log.info("README captured for %d repos", len(updates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
