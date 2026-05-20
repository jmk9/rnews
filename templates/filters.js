(function () {
  "use strict";
  // Two-dim tag filter: Method and Platform act independently and AND together.
  // E.g. method=#VLA + platform=#Manipulator -> only VLA papers on cobot arms.
  // `sort` reorders within each section: "" = default (score desc), "created"
  // = first-seen / repo-creation desc, "pushed" = last-activity desc, "stars" desc.
  const state = { method: "", platform: "", source: "", priority: "", time: "", sort: "" };
  const items = Array.from(document.querySelectorAll(".item"));
  const counter = document.getElementById("visible-count");
  const sectionCounts = document.querySelectorAll(".section-count");
  const now = Date.now();
  const DAY_MS = 24 * 3600 * 1000;

  // Each item is born with a data-published date string (YYYY-MM-DD).
  // We precompute its age in days once so filtering is O(1) per item.
  // We also precompute a lowercase searchable haystack (title + summary +
  // tags + source) so the search box doesn't re-walk the DOM each keystroke.
  for (const it of items) {
    const ds = it.dataset.published;
    let age = Infinity;
    if (ds) {
      const t = Date.parse(ds);
      if (!Number.isNaN(t)) age = (now - t) / DAY_MS;
    }
    it.__ageDays = age;
    const titleEl = it.querySelector(".item-title");
    const sumEl = it.querySelector(".item-summary");
    const parts = [
      titleEl ? titleEl.textContent : "",
      sumEl ? sumEl.textContent : "",
      it.dataset.tags || "",
      it.dataset.source || "",
    ];
    it.__searchText = parts.join(" ").toLowerCase();
  }

  // Multi-word search: every whitespace-separated term must appear somewhere
  // in the item's searchable text. Empty query => no constraint.
  let searchTerms = [];
  const searchBox = document.getElementById("search-box");
  if (searchBox) {
    searchBox.addEventListener("input", () => {
      searchTerms = searchBox.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
      apply();
    });
  }

  function apply() {
    let visible = 0;
    const perSection = { code: 0, news: 0, papers: 0 };
    const timeLimit = state.time ? parseFloat(state.time) : Infinity;
    for (const it of items) {
      const tags = (it.dataset.tags || "").split(/\s+/).filter(Boolean);
      const okMethod = !state.method || tags.indexOf(state.method) !== -1;
      const okPlatform = !state.platform || tags.indexOf(state.platform) !== -1;
      const okSrc = !state.source || it.dataset.source === state.source;
      const okPri = !state.priority || it.dataset.priority === state.priority;
      const okTime = it.__ageDays <= timeLimit;
      const okSearch = searchTerms.length === 0 ||
        searchTerms.every((t) => it.__searchText.indexOf(t) !== -1);
      const show = okMethod && okPlatform && okSrc && okPri && okTime && okSearch;
      it.style.display = show ? "" : "none";
      if (show) {
        visible++;
        // Walk up to the nearest <section> and attribute by section id.
        const section = it.closest("section.section");
        if (section) {
          if (section.id === "section-code") perSection.code++;
          else if (section.id === "section-news") perSection.news++;
          else if (section.id === "section-papers-only") perSection.papers++;
          else if (section.classList.contains("section-deprioritized")) perSection.papers++;
          else perSection.code++;
        }
      }
    }
    if (counter) counter.textContent = visible.toString();
    sectionCounts.forEach((el) => {
      const key = el.dataset.section;
      if (key && perSection[key] !== undefined) {
        el.textContent = perSection[key].toString();
      }
    });
  }

  // ---- Sort: reorder DOM within each section -----------------------------
  // Items carry data-score / data-created / data-published / data-stars so
  // we can resort without hitting any data file. Default key "" uses score
  // (the build-time order). All sorts are descending — newest / largest first.
  function sortKeyOf(art, key) {
    if (key === "created") return Date.parse(art.dataset.created || "") || 0;
    if (key === "pushed") return Date.parse(art.dataset.published || "") || 0;
    if (key === "stars") return parseInt(art.dataset.stars || "0", 10);
    return parseFloat(art.dataset.score || "0"); // default: score
  }

  function applySort() {
    document.querySelectorAll("section.section .items").forEach((container) => {
      const arts = Array.from(container.children).filter((c) => c.tagName === "ARTICLE");
      arts.sort((a, b) => sortKeyOf(b, state.sort) - sortKeyOf(a, state.sort));
      for (const art of arts) container.appendChild(art);
    });
  }

  document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const filter = btn.dataset.filter;
      const value = btn.dataset.value || "";
      state[filter] = value;
      // A given filter dimension may repeat its "All" button across multiple
      // visual groups (e.g. Method row + Platform row both have an All chip
      // bound to data-filter=tag). Activating *every* chip with the matching
      // value keeps the UI honest: clicking "All" in either row lights up
      // both Alls, clicking #VLA lights up only #VLA.
      document
        .querySelectorAll('.chip[data-filter="' + filter + '"]')
        .forEach((b) => {
          if ((b.dataset.value || "") === value) {
            b.classList.add("active");
          } else {
            b.classList.remove("active");
          }
        });
      if (filter === "sort") applySort();
      apply();
    });
  });

  // ---- Expand / collapse summary on each card ----------------------------
  // Hide the Summary button on cards whose text already fits inside the
  // 3-line clamp. Otherwise the user would click "Summary" and see nothing
  // change — the text was already fully visible. Compare `scrollHeight`
  // (full content height) to `clientHeight` (clipped height): equal means
  // nothing's hidden, so the button is useless.
  document.querySelectorAll(".item").forEach((item) => {
    const sum = item.querySelector(".item-summary");
    const btn = item.querySelector(".expand-btn");
    if (!sum || !btn) return;
    if (sum.scrollHeight <= sum.clientHeight + 1) {  // +1 for sub-pixel rounding
      btn.style.display = "none";
    }
  });

  // Direct binding (not delegation) so a stray ancestor click handler can't
  // swallow the event. ~1500 listeners is fine in modern browsers.
  document.querySelectorAll(".expand-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const item = btn.closest(".item");
      if (!item) return;
      const expanded = item.classList.toggle("expanded");
      btn.textContent = expanded ? "Hide ▲" : "Summary ▼";
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
  });
})();
