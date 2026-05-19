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

### Search and filter

- **Search box** at the top — full-text over title, summary, and tags.
- **Method** row: `#RL` / `#IL` / `#VLA`
- **Platform** row: `#Manipulator` / `#Bimanual` / `#MobileManipulator` / `#Humanoid`
- **Source**: `arXiv` / `GitHub` / `News`
- **Priority**: `High` / `Mid` / `Low` (color bar on each card's left edge)
- **Time**: last `1 week` / `1 month` / `3 months`

Filters stack — `#VLA` + `#Manipulator` shows only VLA work on cobot arms.
Click **All** in a row to clear that dimension.

### Subscribe

RSS feed at https://jmk9.github.io/rnews/feed.xml — drop into any reader.

---

## For developers

```bash
git clone https://github.com/jmk9/rnews.git
cd rnews && pip install -r requirements.txt
python main.py            # full collection + site build
python main.py --site-only  # rebuild site from existing data, no network
```

Outputs go to `data/`, `reports/`, and `site/`. Configuration in
[`config.yaml`](config.yaml). The pipeline runs daily via GitHub Actions
(see [`.github/workflows/daily.yml`](.github/workflows/daily.yml)) and
auto-deploys to GitHub Pages.

To add a new source: drop a `collectors/<name>_collector.py` exposing
`collect(cfg: dict) -> list[Item]` and wire it into [`main.py`](main.py).
The rest (dedup, tag, rank, render) is source-agnostic.
