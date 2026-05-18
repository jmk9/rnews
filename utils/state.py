"""Track the first date we ever saw each item, so daily mode highlights only what's new."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class SeenStore:
    """item_id -> first-seen date (YYYY-MM-DD). Loaded eagerly, saved explicitly."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {str(k): str(v) for k, v in raw.items()}
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("state: failed to read %s (%s); starting fresh", self.path, exc)
                self._data = {}

    def first_seen(self, item_id: str) -> str | None:
        return self._data.get(item_id)

    def mark_seen(self, item_id: str, when: str | None = None) -> str:
        existing = self._data.get(item_id)
        if existing is not None:
            return existing
        ts = when or _today()
        self._data[item_id] = ts
        return ts

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self._data)
