"""Score each item for research relevance, then assign a reading priority bucket."""
from __future__ import annotations

import math
import re
from typing import Any

from utils.io import Item, days_since

_GITHUB_RE = re.compile(r"github\.com/[\w.\-]+/[\w.\-]+", re.IGNORECASE)
_CODE_HINTS = ("code is available", "we release", "we will release", "code will be released",
               "project page", "open-source", "open source")
_REAL_ROBOT_HINTS = ("real robot", "real-world", "real world experiment", "physical robot",
                     "deployed on", "hardware experiment")


def _count_hits(text: str, phrases: list[str]) -> int:
    return sum(1 for p in phrases if p and p in text)


def _filter_must_match(
    items: list[Item],
    must_match: list[str],
    exclude: list[str] | None = None,
) -> list[Item]:
    if not must_match:
        return items
    needles = [p.lower() for p in must_match]
    blocks = [b.lower() for b in (exclude or [])]
    kept: list[Item] = []
    for it in items:
        text = f"{it.title}\n{it.summary}".lower()
        # Hard exclude clearly out-of-scope topics (self-driving, financial RL,
        # image/video generation) even if they tripped a must_match keyword.
        if blocks and any(b in text for b in blocks):
            continue
        if any(n in text for n in needles):
            kept.append(it)
    return kept


def _score_one(item: Item, cfg: dict[str, Any], days_back: int) -> tuple[float, dict[str, float]]:
    w = cfg.get("ranking", {})
    must_match = [p.lower() for p in cfg.get("must_match_keywords", [])]
    interests = [p.lower() for p in cfg.get("research_interests", [])]

    title_lc = item.title.lower()
    summary_lc = item.summary.lower()

    breakdown: dict[str, float] = {}

    breakdown["title_keyword"] = _count_hits(title_lc, must_match) * float(w.get("title_keyword", 0))
    breakdown["abstract_keyword"] = _count_hits(summary_lc, must_match) * float(w.get("abstract_keyword", 0))
    breakdown["research_interest_title"] = (
        _count_hits(title_lc, interests) * float(w.get("research_interest_title", 0))
    )
    breakdown["research_interest_abstract"] = (
        _count_hits(summary_lc, interests) * float(w.get("research_interest_abstract", 0))
    )

    # Recency: full weight if updated today, linearly decays to 0 over days_back.
    ref_date = item.updated or item.published
    age_days = days_since(ref_date) if ref_date else None
    if age_days is not None and days_back > 0:
        recency = max(0.0, 1.0 - age_days / float(days_back))
        breakdown["recency"] = recency * float(w.get("recency", 0))
    else:
        breakdown["recency"] = 0.0

    has_code = bool(_GITHUB_RE.search(item.summary)) or any(h in summary_lc for h in _CODE_HINTS)
    if item.source == "github":
        has_code = True
    breakdown["has_code"] = float(w.get("has_code", 0)) if has_code else 0.0

    if item.source == "github":
        stars = int(item.extra.get("stars", 0))
        forks = int(item.extra.get("forks", 0))
        breakdown["github_stars_log"] = math.log10(stars + 1.0) * float(w.get("github_stars_log", 0))
        breakdown["github_forks_log"] = math.log10(forks + 1.0) * float(w.get("github_forks_log", 0))
    else:
        breakdown["github_stars_log"] = 0.0
        breakdown["github_forks_log"] = 0.0

    real_robot = any(h in summary_lc for h in _REAL_ROBOT_HINTS)
    breakdown["real_robot"] = float(w.get("real_robot", 0)) if real_robot else 0.0

    total = round(sum(breakdown.values()), 3)
    return total, breakdown


def _assign_priority(score: float, cfg: dict[str, Any]) -> str:
    pri = cfg.get("priority", {})
    if score >= float(pri.get("must_read_threshold", 10.0)):
        return "must_read"
    if score <= float(pri.get("low_priority_threshold", 3.0)):
        return "low_priority"
    return "save_for_later"


def rank_items(items: list[Item], cfg: dict[str, Any], *, filter_unrelated: bool = True) -> list[Item]:
    """Filter obviously-unrelated items, score the rest, and tag priority. Returns sorted desc."""
    if filter_unrelated:
        items = _filter_must_match(
            items,
            cfg.get("must_match_keywords") or [],
            cfg.get("exclude_keywords") or [],
        )

    # Use the largest configured days_back for recency normalization so papers and
    # repos are comparable on the same time scale.
    sources_cfg = cfg.get("sources", {})
    days_back = max(
        int(sources_cfg.get("arxiv", {}).get("days_back", 7) or 7),
        int(sources_cfg.get("github", {}).get("days_back", 30) or 30),
    )

    for it in items:
        score, breakdown = _score_one(it, cfg, days_back)
        it.score = score
        it.score_breakdown = breakdown
        it.priority = _assign_priority(score, cfg)

    items.sort(key=lambda x: x.score, reverse=True)
    return items
