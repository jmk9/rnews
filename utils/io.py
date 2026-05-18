"""I/O helpers and the shared `Item` dataclass used across collectors and processors."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Item:
    """Normalized record produced by every collector.

    `extra` holds source-specific fields (e.g. stars for github, citation count for
    semantic scholar) so the rest of the pipeline can stay source-agnostic.
    """

    id: str
    source: str
    title: str
    url: str
    summary: str
    published: str
    updated: str
    authors: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    priority: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def load_json(path: str | Path) -> Any:
    p = Path(path)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_text(path: str | Path, text: str) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(text, encoding="utf-8")


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # arxiv/github both emit ISO-8601 with Z or +00:00
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: str, now: datetime | None = None) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)
