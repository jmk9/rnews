"""Send a daily news digest by email.

Reads data/processed/*.json, picks news items that landed recently (default:
last 30h by first_seen, falling back to published date), renders an HTML
email with thumbnail + title + summary per card, and sends via SMTP.

Recipients and a few formatting options live in config.yaml under `email:`.
SMTP credentials come from environment variables — never put them in
config.yaml.

    export RNEWS_SMTP_USER="you@gmail.com"
    export RNEWS_SMTP_PASSWORD="<gmail app password>"

Common runs:
    # local test send to one address, ignore the time window (just send the
    # most recent news right now):
    python scripts/email_news.py --to alsrb4696@gmail.com --now

    # see what would be sent without actually sending:
    python scripts/email_news.py --dry-run --now

    # the scheduled run (CI): just use config recipients and the time window:
    python scripts/email_news.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("email-news")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_all_news(processed_dir: Path) -> list[dict]:
    """All news items deduped across snapshots, keeping the highest-scored copy."""
    by_id: dict[str, dict] = {}
    for fp in sorted(processed_dir.glob("*_processed.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("skipping %s: %s", fp, exc)
            continue
        if not isinstance(data, list):
            continue
        for it in data:
            if it.get("source") != "news":
                continue
            iid = it.get("id")
            if not iid:
                continue
            ex = by_id.get(iid)
            if ex is None or float(it.get("score") or 0) > float(ex.get("score") or 0):
                by_id[iid] = it
    return list(by_id.values())


def _first_seen_dt(it: dict) -> datetime | None:
    fs = (it.get("extra") or {}).get("first_seen") or ""
    if not fs:
        return None
    try:
        return datetime.strptime(fs, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _published_dt(it: dict) -> datetime | None:
    pub = it.get("updated") or it.get("published") or ""
    if not pub:
        return None
    try:
        dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _select(
    news: list[dict],
    *,
    hours_window: int,
    max_items: int,
    ignore_window: bool,
) -> list[dict]:
    """Pick which items go in this email."""
    now = datetime.now(timezone.utc)
    if ignore_window:
        candidates = news
    else:
        cutoff = now - timedelta(hours=hours_window)
        candidates = []
        for it in news:
            fs = _first_seen_dt(it)
            # first_seen is day-granular; allow one extra day of slack so a
            # 30h window doesn't drop items first seen "today" 12h ago.
            if fs and fs >= cutoff - timedelta(days=1):
                candidates.append(it)
                continue
            pub = _published_dt(it)
            if pub and pub >= cutoff:
                candidates.append(it)
        if not candidates:
            log.info("nothing in last %dh; falling back to most recent items", hours_window)
            candidates = news
    candidates.sort(
        key=lambda x: (
            float(x.get("score") or 0),
            x.get("updated") or x.get("published") or "",
        ),
        reverse=True,
    )
    return candidates[:max_items]


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _thumb_url(it: dict, site_url: str) -> str | None:
    """Return an *absolute* thumbnail URL the email client can fetch."""
    extra = it.get("extra") or {}
    path = extra.get("thumbnail_path")
    if path:
        return f"{site_url.rstrip('/')}/{path.lstrip('/')}"
    return extra.get("thumbnail") or None


def _render_card(it: dict, site_url: str) -> str:
    title = escape(it.get("title") or "(untitled)")
    url = escape(it.get("url") or "#")
    summary = escape(it.get("summary") or "")
    feed = escape(((it.get("extra") or {}).get("feed") or "news"))
    date = (it.get("updated") or it.get("published") or "")[:10]
    thumb = _thumb_url(it, site_url)
    thumb_html = ""
    if thumb:
        thumb_html = (
            '<td style="width:200px; vertical-align:top; padding-right:14px;">'
            f'<a href="{url}" style="display:block;">'
            f'<img src="{escape(thumb)}" width="200" '
            'style="display:block; width:200px; height:125px; object-fit:cover; '
            'border-radius:6px; border:1px solid #e6e6e6;" alt="">'
            '</a></td>'
        )
    return (
        '<div style="border:1px solid #e6e6e6; border-radius:10px; padding:14px; '
        'margin:0 0 14px; background:#fff;">'
        '<table style="width:100%; border-collapse:collapse;"><tr>'
        f'{thumb_html}'
        '<td style="vertical-align:top;">'
        f'<a href="{url}" style="display:block; color:#111; text-decoration:none; '
        'font-weight:600; font-size:16px; line-height:1.35; margin:0 0 6px;">'
        f'{title}</a>'
        f'<div style="color:#777; font-size:12px; margin:0 0 8px;">{feed} · {date}</div>'
        f'<div style="color:#333; font-size:14px; line-height:1.5;">{summary}</div>'
        '</td></tr></table></div>'
    )


_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{subject}</title></head>
<body style="margin:0; padding:0; background:#f4f4f5; \
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;">
<div style="max-width:680px; margin:0 auto; padding:24px 16px;">
  <div style="background:#fff; border-radius:12px; padding:24px;">
    <h1 style="margin:0 0 4px; font-size:22px;">{header}</h1>
    <p style="margin:0 0 24px; color:#666; font-size:13px;">{subhead}</p>
    {cards}
    <p style="margin:24px 0 0; color:#888; font-size:12px; text-align:center;">
      <a href="{site_url}" style="color:#2d5cf6; text-decoration:none;">Open full site →</a>
    </p>
  </div>
</div>
</body></html>
"""


def _build_email(items: list[dict], cfg: dict) -> tuple[str, str]:
    email_cfg = cfg.get("email") or {}
    site_url = (
        email_cfg.get("site_url")
        or (cfg.get("site") or {}).get("url")
        or "https://jmk9.github.io/rnews"
    )
    today_local = datetime.now().astimezone().strftime("%Y-%m-%d")
    prefix = email_cfg.get("subject_prefix") or "[RNEWS] Robot news"
    subject = f"{prefix} — {today_local}"
    header = email_cfg.get("header") or "RNEWS — daily robot news"
    subhead = f"{len(items)} items · {today_local}"

    cards = "".join(_render_card(it, site_url) for it in items)
    html = _HTML_SHELL.format(
        subject=escape(subject),
        header=escape(header),
        subhead=escape(subhead),
        cards=cards,
        site_url=escape(site_url),
    )
    return subject, html


def _text_alt(items: list[dict]) -> str:
    """Plain-text fallback for clients that don't render HTML."""
    lines = ["RNEWS — daily robot news", ""]
    for it in items:
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        feed = ((it.get("extra") or {}).get("feed") or "news")
        date = (it.get("updated") or it.get("published") or "")[:10]
        summary = (it.get("summary") or "").strip()
        lines += [
            f"• {title}",
            f"  {feed} · {date}",
            f"  {url}",
            f"  {summary}",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _send(subject: str, html: str, text: str, cfg: dict) -> None:
    email_cfg = cfg.get("email") or {}
    # Strip NBSP and surrounding whitespace. Gmail's app-password page often
    # uses non-breaking spaces between the 4-char groups; copy-pasting brings
    # those along and smtplib's ASCII AUTH PLAIN encode then explodes with
    # `UnicodeEncodeError: 'ascii' codec can't encode character '\xa0'`.
    user = os.environ.get("RNEWS_SMTP_USER", "").replace("\xa0", "").strip()
    password = os.environ.get("RNEWS_SMTP_PASSWORD", "").replace("\xa0", "").strip()
    if not user or not password:
        raise SystemExit("RNEWS_SMTP_USER and RNEWS_SMTP_PASSWORD must be set in env")
    # Sanity check: any non-ASCII left in credentials will still fail at login.
    try:
        user.encode("ascii")
        password.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SystemExit(
            "SMTP credentials contain non-ASCII characters — most likely a stray "
            "non-breaking space from copy-paste. Retype the value by hand. "
            f"(detail: {exc})"
        )

    sender = email_cfg.get("from") or user
    recipients = list(email_cfg.get("to") or [])
    if not recipients:
        raise SystemExit("email.to (recipient list) is empty in config")

    host = email_cfg.get("smtp_host", "smtp.gmail.com")
    port = int(email_cfg.get("smtp_port", 587))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    # Plain To: — every recipient in email.to gets the mail and sees the
    # rest of the list. Sender does NOT receive a copy (they're not in To:).
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    log.info("connecting to %s:%d as %s", host, port, user)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, password)
        s.send_message(msg)
    log.info("sent to %d recipient(s): %s", len(recipients), ", ".join(recipients))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="render HTML to stdout, do not send")
    p.add_argument("--now", action="store_true",
                   help="ignore the time window — pick the most recent news")
    p.add_argument("--to", action="append",
                   help="override recipient (repeatable); useful for ad-hoc test sends")
    p.add_argument("--max-items", type=int, default=None,
                   help="override email.max_items just for this run")
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config)) or {}
    email_cfg = cfg.setdefault("email", {})

    if args.to:
        email_cfg["to"] = args.to
    if args.max_items is not None:
        email_cfg["max_items"] = args.max_items

    # Safety: enabled flag protects against accidental sends from the
    # scheduled pipeline. Manual runs that supply --to or --dry-run skip it.
    enabled = bool(email_cfg.get("enabled", False))
    if not enabled and not args.dry_run and not args.to:
        log.error("email.enabled is false; aborting. Set it true or pass --to.")
        return 1

    processed_dir = Path((cfg.get("paths") or {}).get("processed", "data/processed"))
    news = _load_all_news(processed_dir)
    log.info("loaded %d news items total", len(news))

    items = _select(
        news,
        hours_window=int(email_cfg.get("hours_window", 30)),
        max_items=int(email_cfg.get("max_items", 25)),
        ignore_window=args.now,
    )
    log.info("selected %d items for the email", len(items))
    if not items:
        log.warning("nothing to send")
        return 0

    subject, html = _build_email(items, cfg)
    text = _text_alt(items)

    if args.dry_run:
        sys.stdout.write(html)
        return 0

    _send(subject, html, text, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
