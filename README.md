# RNEWS — Robot NEWS

**👉 Live site: https://jmk9.github.io/rnews/**

Daily picks in robot learning — RL / IL / VLA across cobot arms, bimanual,
mobile manipulators, and humanoids. Plus an industry news feed from ~18
sources (Robohub, IEEE Spectrum, TechCrunch, NVIDIA, DeepMind, MIT,
로봇신문, AI타임스, …).

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

> The 2–3 sentence blurb on each card is an **LLM-written summary** of the
> source, meant for a quick gist. It can occasionally be off — open the
> original via the card title before relying on details.
