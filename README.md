# RNEWS — Robot NEWS

**👉 Live site: https://jmk9.github.io/rnews/**

Daily picks in robot learning — RL / IL / VLA across cobot manipulators,
bimanual setups, mobile manipulators, and humanoid robots. Plus an industry
news feed from ~18 sources (Robohub, IEEE Spectrum, TechCrunch, NVIDIA,
DeepMind, MIT, 로봇신문, AI타임스, …).

Each card carries a short **LLM-written summary** — read the gist in place,
click the title only when you want the original paper / repo / article.

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

- **Search box** — full-text over title, summary, and tags (× to clear; matches
  highlighted in titles).
- **Source**: `News` / `arXiv` / `GitHub`
- **Method**: `#RL` / `#IL` / `#VLA` (VLA also covers robot foundation models)
- **Platform**: `#Manipulator` / `#Bimanual` / `#MobileManipulator` / `#Humanoid` /
  `#Other` (no specific platform)
- **More filters** (collapsed): **Priority** `High`/`Mid`/`Low` · **Time** last
  `1w`/`1m`/`3m` · **Sort by** `Score` (relevance, default) / `Created` / `Pushed` / `Stars`

Filters stack — `#VLA` + `#Humanoid` shows only VLA work on humanoids.
Click **All** in a row to clear that dimension. The colored bar on each card's
left edge is its priority (🟢 High / 🟠 Mid / ⚪ Low).

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
[`config.yaml`](config.yaml) — sources, keywords, tag taxonomy
(Method × Platform), ranking weights, and `exclude_keywords` (drops
out-of-scope topics like self-driving / finance / video-gen).

**Summaries** are pluggable via `summarizer.provider` in config:
`codex` (OpenAI Codex CLI, uses the ChatGPT-extension OAuth — no API key,
local only), `openai` (`OPENAI_API_KEY`), `claude` (`ANTHROPIC_API_KEY`),
or `truncation` (no LLM). Backfill existing items with
`python scripts/llm_summarize.py`.

The pipeline runs daily via GitHub Actions
(see [`.github/workflows/daily.yml`](.github/workflows/daily.yml)) and
auto-deploys to GitHub Pages.

To add a new source: drop a `collectors/<name>_collector.py` exposing
`collect(cfg: dict) -> list[Item]` and wire it into [`main.py`](main.py).
The rest (dedup, tag, rank, summarize, render) is source-agnostic.
