"""Produce a short human-readable summary for each item.

Two implementations:

- **AbstractTruncationSummarizer** — no network, no cost. Keeps the first N
  chars worth of sentences from the source text. Used as the fallback when
  no API key is available.

- **ClaudeSummarizer** — calls Anthropic's Claude API to produce a tight 2-3
  sentence summary focused on (1) what the work is, (2) the key claim/capability,
  (3) what makes it actionable. This is the actual product promise: read the
  gist on the card, click only when you decide to dig in.

Both implement the `Summarizer` protocol; main.py picks based on ANTHROPIC_API_KEY
being set in the environment.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Protocol

from utils.io import Item

log = logging.getLogger(__name__)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_SUMMARY_PROMPT = """You are summarizing a piece of robot-AI work for a researcher who scans many items per day. They want to decide in 5 seconds whether to open the source.

Source type: {source}
Title: {title}

Original text:
{text}

Write a tight summary in 2–3 sentences (max ~350 characters total) that captures:
1. What this is (concrete object — a method, dataset, demo, repo, news event).
2. The key claim or capability — with numbers if the original gives them.
3. What makes it actionable (code released? real-robot tested? specific benchmark? funding amount?).

Rules:
- No filler openings ("This paper presents", "We propose", "This repository contains").
- Concrete language. If the original is vague, stay vague rather than hallucinate.
- Plain prose. No bullets, no headings, no quote marks.
- Output ONLY the summary text. No preamble, no sign-off."""


class Summarizer(Protocol):
    def summarize(self, text: str, *, source: str = "", title: str = "") -> str: ...


class AbstractTruncationSummarizer:
    """Keep the first ~N chars worth of sentences. No network, no cost."""

    def __init__(self, max_chars: int = 320) -> None:
        self.max_chars = max_chars

    def summarize(self, text: str, *, source: str = "", title: str = "") -> str:
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


class ClaudeSummarizer:
    """LLM-backed summarizer. Falls back to truncation on any API hiccup."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 220,
        fallback: AbstractTruncationSummarizer | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = None  # lazy
        self._fallback = fallback or AbstractTruncationSummarizer(max_chars=360)

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic  # type: ignore
        except ImportError:
            log.warning("anthropic SDK not installed; install with `pip install anthropic`")
            return None
        try:
            self._client = anthropic.Anthropic()
            return self._client
        except Exception as exc:
            log.warning("anthropic client init failed: %s", exc)
            return None

    def summarize(self, text: str, *, source: str = "", title: str = "") -> str:
        if not text or not text.strip():
            return ""
        # Very short text — don't bother the LLM, it'll just hallucinate padding.
        if len(text.strip()) < 80:
            return text.strip()

        client = self._client_or_none()
        if client is None:
            return self._fallback.summarize(text)

        prompt = _SUMMARY_PROMPT.format(
            source=source or "unknown",
            title=title or "(no title)",
            text=text[:6000],  # cap input — abstracts rarely longer; READMEs we truncate
        )
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
            out = "".join(parts).strip()
            if not out:
                return self._fallback.summarize(text)
            return out
        except Exception as exc:
            log.warning("ClaudeSummarizer call failed (%s); falling back", exc)
            return self._fallback.summarize(text)


def summarize_items(items: list[Item], summarizer: Summarizer) -> list[Item]:
    for it in items:
        # Keep the original around so we can re-summarize later with a better
        # prompt / model without losing the raw source text.
        if "full_text" not in (it.extra or {}):
            it.extra = dict(it.extra or {})
            it.extra["full_text"] = it.summary
        it.summary = summarizer.summarize(
            it.extra.get("full_text") or it.summary,
            source=it.source,
            title=it.title,
        )
    return items
