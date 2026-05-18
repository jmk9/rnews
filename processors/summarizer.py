"""Produce a short human-readable summary for each item.

Default implementation just trims the abstract/description to the first sentences.
A future LLM-backed summarizer can be added by implementing the same
`Summarizer.summarize(text) -> str` protocol and selecting it in main.py.
"""
from __future__ import annotations

import re
from typing import Protocol

from utils.io import Item

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


class Summarizer(Protocol):
    def summarize(self, text: str) -> str: ...


class AbstractTruncationSummarizer:
    """Keep the first ~N chars worth of sentences from the abstract."""

    def __init__(self, max_chars: int = 320) -> None:
        self.max_chars = max_chars

    def summarize(self, text: str) -> str:
        text = (text or "").strip().replace("\n", " ")
        if not text:
            return ""
        if len(text) <= self.max_chars:
            return text
        sentences = _SENT_SPLIT.split(text)
        out: list[str] = []
        used = 0
        for s in sentences:
            if used + len(s) + 1 > self.max_chars and out:
                break
            out.append(s)
            used += len(s) + 1
        result = " ".join(out).strip()
        return result if result else (text[: self.max_chars].rstrip() + "…")


def summarize_items(items: list[Item], summarizer: Summarizer) -> list[Item]:
    for it in items:
        it.summary = summarizer.summarize(it.summary)
    return items
