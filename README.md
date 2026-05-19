# RNEWS — Robot NEWS

**👉 Live site: https://jmk9.github.io/rnews/**

Daily picks in robot learning — RL / IL / VLA across cobot manipulators,
bimanual setups, mobile manipulators, and humanoid robots. Plus an industry
news feed (Figure / 1X / NVIDIA / DeepMind announcements, TechCrunch, IEEE
Spectrum, Hacker News, …).

Updated daily at **07:00 KST**. Sorted by what people *actually use* —
GitHub stars, code availability, real-robot experiments.

---

## How to use the site (no setup, just browse)

Open https://jmk9.github.io/rnews/ in any browser. The page is a single
scrollable feed, organized top to bottom:

1. **Code & repos** — GitHub projects + papers that released code. The
   actionable stuff. Sorted by popularity (stars + forks).
2. **News & articles** — fresh industry pulse from RSS feeds.
3. **Papers only** — research papers without code. Lower priority but kept
   for completeness.

### Filter chips at the top

| Chip row | What it does |
|---|---|
| **Method** | `#RL` / `#IL` / `#VLA` — how the policy was trained |
| **Platform** | `#Manipulator` / `#Bimanual` / `#MobileManipulator` / `#Humanoid` — what kind of robot |
| **Source** | `arXiv` / `GitHub` / `News` |
| **Priority** | `High` / `Mid` / `Low` (color-bar on left of each card matches) |
| **Time** | last `1 week` / `1 month` / `3 months` |

**Method and Platform stack** — clicking `#VLA` then `#Manipulator` shows
only VLA work on cobot arms. Other dimensions also AND together. Click
**All** in a row to clear that dimension.

### Other things on the page

- **Card color bar** (left edge): 🟢 green = High, 🟠 amber = Mid, ⚪ gray = Low
- **Card title** is a link — opens the original paper / repo / article
- **Tags on each card** show its Method + Platform classification
- **Daily archive** links at the top jump to per-day snapshots
- **RSS feed** at `https://jmk9.github.io/rnews/feed.xml` — subscribe in any
  feed reader (NetNewsWire, Reeder, Feedly, Vivaldi) for new items

### What it covers

- Sources: **arXiv** (cs.RO / cs.AI / cs.LG / cs.CV) + **GitHub** search + **9 RSS news feeds**
- ~90-day rolling window; the daily run accumulates more over time
- Two-axis tag taxonomy stays small on purpose — Method (3) × Platform (4) — so the filter UI stays scannable

---

## For developers — running locally

Below is everything you need to fork, run, and self-host. Skip if you just
want to read the live site.

## What you get out of one run

```
data/raw/2026-05-18_raw.json          all items as collected
data/processed/2026-05-18_processed.json  tagged, scored, sorted
data/state/seen.json                   item_id -> first-seen date
reports/2026-05-18_daily_report.md     human-readable markdown report
reports/latest_report.md               same content under a stable filename
site/index.html                        shareable site, with tag/source/priority filters
site/daily/2026-05-18.html             archive page for each day
site/feed.xml                          RSS feed (subscribe with any reader)
site/styles.css, site/filters.js       static assets
```

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Optional but recommended for GitHub:
```bash
export GITHUB_TOKEN=ghp_xxx   # any read-only PAT — raises rate limit
```

## Run

```bash
# Weekly digest (default lookback windows from config)
python main.py

# Daily refresh — shorter lookback AND markdown report shows only items new today
python main.py --mode daily

# Just rebuild the static site from existing data/processed/* — no network
python main.py --site-only

# Skip the site, just collect + write markdown
python main.py --no-site

# Single-source debugging
python main.py --source arxiv -v
python main.py --no-filter -v   # disable keyword pre-filter to inspect raw output
```

## What "daily mode" means

Daily mode is the one you'll actually use most. It:

1. Pulls the last ~2 days of arXiv + 7 days of GitHub.
2. Looks each item up in `data/state/seen.json` and stamps `first_seen` on it.
3. **The markdown report only includes items first seen today** — so you read truly new things, not a re-rendering of yesterday's list.
4. The static site still shows everything (newest first); only the markdown shortlist is filtered.

This is what makes daily mode useful: no rolling déjà vu.

## Share with colleagues (the site)

`site/` is a self-contained folder of static HTML + CSS + JS — no server-side
code, no external CDNs, no JS framework. Several hosting options:

```bash
# Local network preview
python -m http.server 8000 --directory site

# GitHub Pages: commit `site/` to a `gh-pages` branch (or use a publish workflow)
# Netlify / Vercel / S3: point them at the `site/` folder
# Internal nginx: just rsync `site/` to a web-served path
```

The page has client-side filter chips for **tag**, **source**, and **priority**
so colleagues can drill down without you regenerating anything.

The page also exposes `feed.xml` — anyone using a feed reader (NetNewsWire,
Reeder, Feedly, …) can subscribe and get updates automatically.

## Automate daily runs

### cron (simplest)

```cron
# Every weekday at 8:30 KST = 23:30 UTC the previous day
30 23 * * 1-5 cd /home/lny/RNEWS && /home/lny/RNEWS/.venv/bin/python main.py --mode daily >> data/run.log 2>&1
```

### systemd timer (more reliable, recovers from missed runs)

`~/.config/systemd/user/rnews.service`:
```ini
[Unit]
Description=RNEWS daily run

[Service]
Type=oneshot
WorkingDirectory=%h/RNEWS
ExecStart=%h/RNEWS/.venv/bin/python main.py --mode daily
```

`~/.config/systemd/user/rnews.timer`:
```ini
[Unit]
Description=RNEWS daily

[Timer]
OnCalendar=Mon..Fri *-*-* 08:30:00 Asia/Seoul
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now rnews.timer
```

### GitHub Actions

If you want the site to be auto-built and published to GitHub Pages, drop this
into `.github/workflows/daily.yml`:

```yaml
name: daily
on:
  schedule:
    - cron: '30 23 * * 1-5'  # 08:30 KST Tue–Sat
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
        run: python main.py --mode daily
      - uses: actions/upload-pages-artifact@v3
        with: { path: site }
      - uses: actions/deploy-pages@v4
```

## Configure (everything in `config.yaml`)

- `must_match_keywords` — items missing all of these in title/abstract are dropped.
- `research_interests` — items hitting these get a bigger score boost.
- `tags` — map of tag → substring patterns. Add your own.
- `sources.arxiv` / `sources.github` — categories, queries, lookback windows.
- `ranking` — weights for each score component (title hit, recency, has_code, stars, …).
- `priority` — score thresholds for `must_read` / `save_for_later` / `low_priority`.
- `state.seen_path` — where first-seen state lives.
- `site` — title, description, items on index, output dir.

## How to add a new source

1. Create `collectors/<name>_collector.py` exposing `collect(cfg: dict) -> list[Item]`.
2. Map records into the shared [`Item`](utils/io.py) dataclass.
3. Wire it into `run_collectors()` in [main.py](main.py).
4. Add a `sources.<name>` section to `config.yaml`.

The rest of the pipeline (dedupe, tag, rank, summarize, report, site) is
source-agnostic, so a new collector drops in without touching anything else.

## What's intentionally **not** here yet

- Semantic Scholar / Hugging Face Papers collectors — rate limits and HTML scraping respectively. Pending.
- LLM-backed summarizer — `processors/summarizer.py` exposes a `Summarizer` protocol; a Claude/OpenAI/Ollama backend is a drop-in replacement.
- Slack / email push — can be added as an additional "output" once you decide where to send.
- Auto-deploy — kept out so you can pick a host that matches your company's setup.

## Rate-limit notes

- arXiv: ~1 request / 3 s. The `arxiv` package handles this internally.
- GitHub search (unauth): ~10 req/min. With `GITHUB_TOKEN` set: ~30 req/min.
- Everything stored as UTC ISO-8601.
