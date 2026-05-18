# Self-feedback / improvement backlog

This file is the working memory for evolving `robot-ai-monitor` from a researcher's perspective. Future Claude sessions read this before suggesting features so we don't lose threads across conversations.

## How to use this file

1. **Before any new work**, scan the **Recent user observations** section first, then the **P0** list.
2. Pick 1–2 items per session. Prefer P0 → P1 → P2.
3. When done, move the item to **Completed** with the date.
4. When the user gives new observations or asks for features, append to **Recent user observations** before starting work — that's the highest-priority signal.

## Recent user observations

- **2026-05-18** — "Live site shows only GitHub items, no arXiv." Root cause: daily mode used `arxiv.days_back=2`; latest arXiv is 2026-05-15 (Fri), today is Mon, 3-day gap > 2-day cutoff. Compounded by same-day file overwrite that erased the 122 arXiv items from a prior local run. **Fix shipped:** widen daily arXiv to 5 days; merge same-day processed JSON on save.
- **2026-05-18** — "Papers without separate doc site or GitHub code are low importance — keep them, but at the bottom." **Fix shipped:** site index partitions into **Code & repos** (top) and **Papers only** (bottom); same on day archive pages.

## Mental model

A robot-AI researcher uses this site to do six jobs. Improvements should serve at least one.

- **J1: Catch new SOTA / techniques in my subfield within 24 h.**
- **J2: Find code I can actually run (sim env, baseline impl, training script).**
- **J3: See what's hot in adjacent fields without drowning.**
- **J4: Track specific labs/authors I care about.**
- **J5: Build a reading queue, mark things read.**
- **J6: Share interesting finds with my team.**

Quick honest scoring of current state (1–5):
J1: 3 (filter is keyword-only, no method-novelty signal)
J2: 3 (we surface GitHub + has_code, but don't pair paper↔repo)
J3: 3 (cs.AI/LG/CV included but no trend digest)
J4: 1 (no author/lab tracking at all)
J5: 2 (priority buckets exist; no per-user bookmark/dismiss)
J6: 2 (links work; no per-item permalink on our site, no share button)

---

## P0 — High value, ship soon

### Cross-source paper ↔ repo pairing
- **Why:** When a paper releases code on GitHub, scrolling through the site shows the two as separate cards. A researcher reads the abstract and immediately wants to click "code." Pairing them as one card is the single biggest UX win for J2.
- **How:** In `processors/deduplicator.py` (or a new `processors/linker.py`), scan arxiv items for `github.com/<owner>/<repo>` URLs in abstract or arxiv "comment" field. If the matching repo is also collected by `github_collector`, merge — keep the paper card but stamp `extra.repo_url`, `extra.stars`, `extra.repo_id`.
- **Done when:** at least 30% of "paper has code" items show the repo's star count inline; clicking the repo badge opens the github URL.

### Arxiv version dedup (v1, v2, v3 → one entry)
- **Why:** Right now `arxiv:2605.15559v1` and `arxiv:2605.15559v2` are separate items. Researchers don't care about old versions.
- **How:** In `collectors/arxiv_collector.py` `_to_item`, normalize id by stripping the `v\d+` suffix (already partially done — verify). In the deduplicator, when two items share the same versionless id, keep the latest updated.
- **Done when:** no two cards on the index share the same arXiv core id.

### Site search box (client-side)
- **Why:** Filters are good but not enough when you remember a fragment of a title. Static-site search is cheap because the dataset is small.
- **How:** Add an `<input>` at the top of `templates/index.html.j2`; in `filters.js`, add a substring filter against title+summary+tags. No external library.
- **Done when:** typing "diffusion" reduces the list to matching items live as you type.

### Better has-code detection
- **Why:** Current regex only catches `github.com/x/y` URLs. Many papers have project pages (e.g. `https://robot-x.github.io`) or mention "Project page" without a domain match. Researchers filter for actionable papers.
- **How:** Extend the regex set in `processors/ranker.py` to also catch `*.github.io`, `project page:`, `https?://[^\s]+` containing `project`, `code:`, `released`. Also scan `extra.comment` from arxiv (often holds "code: https://…").
- **Done when:** sampling 20 P0 papers, has_code detection agrees with manual inspection ≥ 17/20.

### Per-item anchor + permalink button
- **Why:** Sharing with the team requires a link to a specific item. Currently you can only share the whole page.
- **How:** In `_item.html.j2`, give each `<article>` an `id` = sanitized item.id. Add a small "🔗" button that copies `window.location.href + '#' + id` to clipboard.
- **Done when:** clicking the link button copies a working anchored URL.

### Keyboard navigation
- **Why:** Power users move faster with `j`/`k`. Removes a UX friction for daily scanning.
- **How:** In `filters.js`, add a keyboard handler: `j`/`k` move focus to next/prev visible `.item`; `o` opens its primary link; `/` focuses the search box; `Esc` clears search.
- **Done when:** all four shortcuts work without click.

---

## P1 — Worth doing

### Semantic Scholar collector (citation count + influential-citation badge)
- **Why:** Citation count is the single best post-hoc signal for "did this paper matter." For 1–3 month old papers it's noisy but still useful at the tail.
- **How:** New `collectors/semantic_scholar_collector.py`. Use `/graph/v1/paper/search` with `query=<paper title>` for top-scoring arxiv items only (not all — rate limits). Stamp `extra.citation_count`, `extra.influential_citations`. Ranker adds a small weight.
- **Done when:** top-50 papers show a citation count when SS has them; ranker uses log(1+cites) with weight ≤ 1.5.

### LLM summarizer (Claude API, opt-in)
- **Why:** Abstract truncation is OK for most, weak for verbose abstracts. A 2-sentence "what's new + so what" summary saves seconds per item × dozens of items per day.
- **How:** `processors/summarizer.py` already exposes a `Summarizer` protocol. Add `ClaudeSummarizer` that batches items via Claude Haiku (cheap). Gated by `ANTHROPIC_API_KEY`; falls back to truncation when unset.
- **Done when:** with `ANTHROPIC_API_KEY` set, top-30 papers get LLM summaries; without, abstract truncation still works.

### Bookmark / dismiss via localStorage
- **Why:** Researchers want a personal reading queue without backend. localStorage is enough.
- **How:** In `_item.html.j2`, add "★ save" and "✕ hide" buttons. `filters.js` persists item-id → state in localStorage. New filter chip: "Saved only". Hidden items get `display:none` until "Show hidden" toggle.
- **Done when:** state survives a page reload.

### Conference badges (CoRL / ICRA / IROS / RSS / NeurIPS)
- **Why:** Conference acceptance is a strong quality signal for researchers and a great filter.
- **How:** Scan `extra.comment` from arxiv for strings like "Accepted at CoRL 2026", "ICRA 2026". Add tag `#CoRL2026` etc. Render as a coloured badge in `_item.html.j2`.
- **Done when:** papers with `Accepted at` comments get a visible conference badge.

### Top robotics lab blog feeds
- **Why:** Some of the highest-signal content (NVIDIA Isaac, FAIR / Meta, DeepMind, Boston Dynamics, 1X, Figure, Physical Intelligence) is published as blog posts before it hits arXiv.
- **How:** New `collectors/blog_collector.py` using `feedparser` against a configured list of RSS URLs (in `config.yaml`). Tag items `#LabBlog`.
- **Done when:** at least 5 lab feeds polled successfully; items appear with a `#LabBlog` chip.

### Weekly trend digest section
- **Why:** "What changed this week" is a recurring question. The data is already there; just visualize.
- **How:** On `index.html`, add a top strip: "Top tags this week vs prior week" with simple counts and deltas. Compute by reading the last 14 days of processed JSON.
- **Done when:** the strip shows ≥ 5 tag deltas with arrows.

### Hugging Face Papers collector
- **Why:** HF aggregates daily a curated set; high signal for VLA / foundation models.
- **How:** Scrape `https://huggingface.co/papers` (no public API). Brittle — wrap in try/except; warn but don't fail the pipeline.
- **Done when:** items appear with `source=hf`; failures don't crash the run.

### Better keyword matching (word boundaries)
- **Why:** Current substring match has false positives (e.g. "robot" matches "robust"). Word boundaries reduce noise.
- **How:** In `processors/tagger.py` and `processors/ranker.py`, switch to regex `\b<keyword>\b` when the pattern contains no spaces; keep substring for multi-word phrases.
- **Done when:** sampling shows < 5% false-positive keyword matches.

---

## P2 — Nice to have

### Slack webhook output
- Single message per day with top 5 must-read items. Gated by `SLACK_WEBHOOK_URL`.

### Email digest output
- Same as Slack but SMTP. Lower priority — Slack covers most teams.

### Conference accepted-paper lists (CoRL/ICRA/IROS proceedings)
- One-shot importers run on a per-conference cadence. Each conference page parser is its own collector.

### Author / lab tracking
- Watchlist in config: `authors: [Sergey Levine, Chelsea Finn]`. Items matching get a "👤 watched" badge.

### First-figure thumbnail for papers
- Extract figure-1 from PDF (PyMuPDF). Visual scan beats text scan.

### Mobile UX polish
- Tighter spacing, larger tap targets, swipe to dismiss.

### "Two-up" comparison view
- Pick two items, render side-by-side. Helps "is this just A with extra step" decisions.

### YouTube paper-demo channels
- Some channels post hardware demos before papers (e.g. Boston Dynamics, 1X). RSS feeds exist.

### Code-language icons on github cards
- Tiny visual; helps "is this Python or C++" filter at a glance.

### Reading time estimate
- For papers, estimate from arXiv abstract word count × heuristic.

### BibTeX / Zotero export
- "Export saved items" → .bib download.

---

## Completed

- 2026-05-18 — Code/papers partition on index and day archive (papers without code at bottom).
- 2026-05-18 — Daily arXiv lookback widened to 5 days; merge same-day processed JSON on save (no more data loss when multiple runs hit the same date).
- 2026-05-18 — Tag/source/priority client-side filters.
- 2026-05-18 — Daily/weekly modes; SeenStore for first-seen filtering.
- 2026-05-18 — Static-site build (HTML + RSS + CSS) with GitHub Actions auto-deploy.

## Out of scope (decided no)

- **Full JS framework (React/Next/Vue)** — overkill for static read-only content; introduces build pipeline.
- **DB / backend** — JSON files at this scale are faster and easier to debug.
- **In-tool auto-deploy beyond GitHub Pages** — host-specific; better left to user.
- **Twitter/X mention tracking** — anti-scraping; would burn engineering effort on a brittle layer.
