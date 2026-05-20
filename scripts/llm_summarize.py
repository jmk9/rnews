"""One-off backfill: rewrite every item's summary using ClaudeSummarizer.

Re-summarizes whatever is in `it.extra.full_text` (preferred) or falls back to
`it.summary`. Stores the LLM output in `it.summary`, keeps the original in
`it.extra.full_text` so the operation is repeatable / reversible.

Idempotent: items whose `extra.summary_kind == "llm"` are skipped unless
`--force` is given.

Cost on Claude Haiku, sequential calls:
- ~$0.0005 / item -> ~$2 for a full 3,800-item pass.
- ~30 min wall time at ~0.5s/call.

Requires `ANTHROPIC_API_KEY` in the environment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from processors.summarizer import make_summarizer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("llm-summarize")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-items", type=int, default=0,
                   help="Cap total items processed (0 = all).")
    p.add_argument("--top-only", type=int, default=0,
                   help="Process only the top-N items by score (0 = all).")
    p.add_argument("--force", action="store_true",
                   help="Re-summarize items that already have an LLM summary.")
    args = p.parse_args()

    cfg = yaml.safe_load(open("config.yaml")) or {}
    provider = str((cfg.get("summarizer") or {}).get("provider", "auto")).lower()
    # API-key providers need a key; codex uses local OAuth; truncation needs nothing.
    if provider in ("openai", "claude") and not (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    ):
        log.error("provider=%s but no OPENAI_API_KEY / ANTHROPIC_API_KEY set; aborting.", provider)
        return 1

    # Load every processed snapshot and dedupe by id so we don't pay for the
    # same paper twice when it shows up in two snapshots.
    by_id: dict[str, dict] = {}
    files: list[Path] = []
    for fp in sorted(Path("data/processed").glob("*_processed.json")):
        files.append(fp)
        for it in json.load(open(fp)):
            existing = by_id.get(it["id"])
            if existing is None or float(it.get("score") or 0) > float(existing.get("score") or 0):
                by_id[it["id"]] = it

    candidates = list(by_id.values())
    if args.top_only:
        candidates.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
        candidates = candidates[: args.top_only]
    if not args.force:
        candidates = [it for it in candidates
                      if (it.get("extra") or {}).get("summary_kind") != "llm"]
    if args.max_items:
        candidates = candidates[: args.max_items]

    log.info("Will summarize %d items", len(candidates))
    summ = make_summarizer(cfg)
    log.info("Using summarizer: %s", type(summ).__name__)
    updates: dict[str, dict] = {}
    for i, it in enumerate(candidates, 1):
        original = (it.get("extra") or {}).get("full_text") or it.get("summary") or ""
        if not original.strip():
            continue
        log.info("[%d/%d] %s %s: %s", i, len(candidates),
                 it.get("source"), it["id"], it.get("title", "")[:55])
        new_summary = summ.summarize(original, source=it.get("source", ""), title=it.get("title", ""))
        if not new_summary or new_summary == original:
            continue
        updates[it["id"]] = {"summary": new_summary,
                             "full_text": original,
                             "summary_kind": "llm"}
        # Gentle pacing — Haiku is fast but we don't need to slam the API.
        time.sleep(0.15)

    if not updates:
        log.info("No updates produced.")
        return 0

    # Write back to every snapshot that contains the item.
    for fp in files:
        data = json.load(open(fp))
        changed = False
        for it in data:
            u = updates.get(it["id"])
            if not u:
                continue
            it["summary"] = u["summary"]
            extra = it.setdefault("extra", {})
            extra["full_text"] = u["full_text"]
            extra["summary_kind"] = u["summary_kind"]
            changed = True
        if changed:
            with open(fp, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("Updated %s", fp)
    log.info("Total items rewritten: %d", len(updates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
