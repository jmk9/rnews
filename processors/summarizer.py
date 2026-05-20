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

import glob
import logging
import os
import re
import subprocess
import tempfile
from typing import Protocol

from utils.io import Item

log = logging.getLogger(__name__)

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")

_SUMMARY_PROMPT = """Write a natural 2–3 sentence summary of this robot-AI work for a researcher scanning many items a day. Describe it the way you'd explain it to a knowledgeable colleague — flowing, concrete prose that makes clear what it does and what's interesting about it.

Source type: {source}
Title: {title}

Text:
{text}

For a paper, convey the key idea or mechanism and the main result. For a repo or tool, convey what it actually does and what it's good for. For news, convey what happened and why it matters.

Hard rules:
- Write natural prose. Do NOT use meta-labels or scaffolding such as "the novelty is", "the main contribution is", "the key capability is", "this is actionable because", "what's notable is". Just describe the work directly so the importance comes through on its own.
- Do NOT open with "This paper", "We propose", "This repository", "A framework for", "This work", "GitHub repo for".
- Be specific and concrete — name the method, mechanism, benchmark, dataset, or numbers when the text gives them. Avoid empty phrases like "novel approach", "various tasks", or "state-of-the-art" used without specifics.
- If the text is too thin to say anything substantive, just describe plainly what the thing is. Don't pad or invent.
- 2–3 sentences, one paragraph, no bullets, headings, or quotation marks.
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


class OpenAISummarizer:
    """LLM-backed summarizer via the OpenAI (a.k.a. Codex) API.

    Uses the `openai` SDK's chat-completions endpoint. `base_url` is optional —
    set it for any OpenAI-compatible endpoint. Falls back to truncation on any
    error or missing key.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        max_tokens: int = 220,
        base_url: str | None = None,
        fallback: "AbstractTruncationSummarizer | None" = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.base_url = base_url or None
        self._client = None  # lazy
        self._fallback = fallback or AbstractTruncationSummarizer(max_chars=360)

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        try:
            import openai  # type: ignore
        except ImportError:
            log.warning("openai SDK not installed; install with `pip install openai`")
            return None
        try:
            kwargs = {}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**kwargs)
            return self._client
        except Exception as exc:
            log.warning("openai client init failed: %s", exc)
            return None

    def summarize(self, text: str, *, source: str = "", title: str = "") -> str:
        if not text or not text.strip():
            return ""
        if len(text.strip()) < 80:
            return text.strip()
        client = self._client_or_none()
        if client is None:
            return self._fallback.summarize(text)
        prompt = _SUMMARY_PROMPT.format(
            source=source or "unknown",
            title=title or "(no title)",
            text=text[:6000],
        )
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            out = (resp.choices[0].message.content or "").strip()
            return out or self._fallback.summarize(text)
        except Exception as exc:
            log.warning("OpenAISummarizer call failed (%s); falling back", exc)
            return self._fallback.summarize(text)


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


class CodexCLISummarizer:
    """Summarize via the OpenAI Codex CLI (`codex exec`).

    Uses the Codex VS Code extension's bundled binary and its existing OAuth
    login (ChatGPT subscription) — no API key, no extra billing. The trade-off:
    it only works where the binary + auth exist (i.e. the user's local machine,
    NOT GitHub Actions), and each call is slow (~10-20s) because Codex is an
    agentic tool, not a bare completion endpoint.

    Falls back to truncation if the binary can't be found or a call fails.
    """

    _BINARY_GLOBS = [
        "~/.vscode/extensions/openai.chatgpt-*/bin/*/codex",
        "~/.vscode-server/extensions/openai.chatgpt-*/bin/*/codex",
        "~/.cursor/extensions/openai.chatgpt-*/bin/*/codex",
    ]

    def __init__(
        self,
        binary: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = "low",
        timeout: int = 150,
        fallback: "AbstractTruncationSummarizer | None" = None,
    ) -> None:
        self.binary = binary or self._discover_binary()
        self.model = model or None
        # Codex defaults to gpt-5.5 + xhigh reasoning, which is overkill for
        # summarization and burns the ChatGPT usage limit fast. Low effort is
        # plenty for a 3-sentence summary and stretches the daily quota.
        self.reasoning_effort = reasoning_effort or None
        self.timeout = timeout
        self._fallback = fallback or AbstractTruncationSummarizer(max_chars=360)

    @classmethod
    def _discover_binary(cls) -> str | None:
        for pattern in cls._BINARY_GLOBS:
            hits = sorted(glob.glob(os.path.expanduser(pattern)))
            if hits:
                return hits[-1]  # latest extension version
        return None

    def summarize(self, text: str, *, source: str = "", title: str = "") -> str:
        """Return a summary, or "" if the CLI call failed / echoed the input.
        Returning "" lets summarize_items mark it as truncation (retryable) so a
        flaky codex call never gets frozen in as a fake 'llm' summary."""
        if not text or not text.strip():
            return ""
        if len(text.strip()) < 80:
            return text.strip()
        if not self.binary or not os.path.exists(self.binary):
            return ""

        prompt = _SUMMARY_PROMPT.format(
            source=source or "unknown",
            title=title or "(no title)",
            text=text[:6000],
        )
        for attempt in range(2):  # one retry — codex occasionally returns junk
            out = self._run_once(prompt)
            if out and not self._looks_like_echo(out, text):
                return out
            log.warning("codex summary attempt %d unusable for %r; %s",
                        attempt + 1, (title or "")[:40],
                        "retrying" if attempt == 0 else "giving up")
        return ""

    def _run_once(self, prompt: str) -> str:
        out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="rnews_codex_")
        os.close(out_fd)
        try:
            cmd = [self.binary, "exec", "--skip-git-repo-check", "-o", out_path]
            if self.model:
                cmd += ["-m", self.model]
            if self.reasoning_effort:
                cmd += ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
            cmd.append(prompt)
            subprocess.run(
                cmd, capture_output=True, timeout=self.timeout,
                cwd=tempfile.gettempdir(), check=False,
            )
            with open(out_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("CodexCLISummarizer call failed (%s)", exc)
            return ""
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    @staticmethod
    def _looks_like_echo(result: str, src: str) -> bool:
        """True if the 'summary' is really just the input handed back (codex
        sometimes echoes, and the truncation fallback would too)."""
        r, s = result.strip(), src.strip()
        if len(r) > 320 and r[:100].lower() == s[:100].lower():
            return True
        return False


def make_summarizer(cfg: dict) -> Summarizer:
    """Build a summarizer from config. Swapping providers is a one-line config
    change. `provider: auto` picks whichever API key is present, preferring
    OpenAI, then Claude, then the no-cost truncation fallback.
    """
    scfg = (cfg or {}).get("summarizer", {}) or {}
    provider = str(scfg.get("provider", "auto")).lower()

    if provider == "auto":
        if CodexCLISummarizer._discover_binary():
            provider = "codex"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "claude"
        else:
            provider = "truncation"

    if provider == "codex":
        return CodexCLISummarizer(
            binary=scfg.get("codex_binary") or None,
            model=scfg.get("codex_model") or None,
            reasoning_effort=scfg.get("codex_reasoning_effort", "low") or None,
        )
    if provider == "openai":
        return OpenAISummarizer(
            model=scfg.get("openai_model", "gpt-4o-mini"),
            base_url=scfg.get("openai_base_url") or None,
        )
    if provider == "claude":
        return ClaudeSummarizer(model=scfg.get("claude_model", "claude-haiku-4-5-20251001"))
    # truncation: keep full text essentially intact (cards render it as-is)
    return AbstractTruncationSummarizer(max_chars=int(scfg.get("truncation_max_chars", 10000)))


_TRUNC_FALLBACK = AbstractTruncationSummarizer(max_chars=360)


def summarize_items(items: list[Item], summarizer: Summarizer) -> list[Item]:
    is_llm = not isinstance(summarizer, AbstractTruncationSummarizer)
    for it in items:
        extra = dict(it.extra or {})
        # Don't downgrade an existing model summary. A CI run (truncation-only,
        # no codex binary) must not clobber a summary we produced locally with
        # codex/OpenAI/Claude.
        if extra.get("summary_kind") == "llm":
            it.extra = extra
            continue
        # Keep the original around so we can re-summarize later with a better
        # prompt / model without losing the raw source text.
        if "full_text" not in extra:
            extra["full_text"] = it.summary
        src_text = extra.get("full_text") or it.summary

        result = summarizer.summarize(src_text, source=it.source, title=it.title)
        if is_llm and result:
            # Genuine model summary.
            it.summary = result
            extra["summary_kind"] = "llm"
        else:
            # LLM unavailable / failed (returned "") -> truncation. Mark it
            # "truncation" (NOT llm) so a later run retries it.
            it.summary = result if (not is_llm and result) else _TRUNC_FALLBACK.summarize(src_text)
            extra["summary_kind"] = "truncation"
        it.extra = extra
    return items
