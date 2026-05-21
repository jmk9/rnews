(function () {
  "use strict";
  // Two-dim tag filter: Method and Platform act independently and AND together.
  // E.g. method=#VLA + platform=#Manipulator -> only VLA papers on cobot arms.
  // `sort` reorders within each section. Source/Priority/Time/Search AND on top.
  const state = { method: "", platform: "", source: "", priority: "", time: "", sort: "" };
  const items = Array.from(document.querySelectorAll(".item"));
  const counter = document.getElementById("visible-count");
  const sectionCounts = document.querySelectorAll(".section-count");
  const emptyState = document.getElementById("empty-state");
  const now = Date.now();
  const DAY_MS = 24 * 3600 * 1000;

  function sectionKey(it) {
    const section = it.closest("section.section");
    if (!section) return "code";
    if (section.id === "section-news") return "news";
    if (section.id === "section-papers-only" || section.classList.contains("section-deprioritized")) return "papers";
    return "code";
  }

  // Precompute per item: age in days, a lowercase search haystack, section,
  // and stash the original title text for highlight/restore.
  for (const it of items) {
    const ds = it.dataset.published;
    let age = Infinity;
    if (ds) {
      const t = Date.parse(ds);
      if (!Number.isNaN(t)) age = (now - t) / DAY_MS;
    }
    it.__ageDays = age;
    it.__section = sectionKey(it);
    const titleEl = it.querySelector(".item-title");
    const sumEl = it.querySelector(".item-summary");
    it.__titleEl = titleEl;
    it.__titleText = titleEl ? titleEl.textContent : "";
    it.__searchText = [
      it.__titleText,
      sumEl ? sumEl.textContent : "",
      it.dataset.tags || "",
      it.dataset.source || "",
    ].join(" ").toLowerCase();
  }

  // ---- Search -------------------------------------------------------------
  let searchTerms = [];
  const searchBox = document.getElementById("search-box");
  const searchClear = document.getElementById("search-clear");

  function syncSearch() {
    const raw = searchBox ? searchBox.value.trim() : "";
    searchTerms = raw.toLowerCase().split(/\s+/).filter(Boolean);
    if (searchClear) searchClear.hidden = raw.length === 0;
    apply();
  }
  if (searchBox) searchBox.addEventListener("input", syncSearch);
  if (searchClear) {
    searchClear.addEventListener("click", () => {
      searchBox.value = "";
      searchBox.focus();
      syncSearch();
    });
  }

  function highlightTitle(it, on) {
    if (!it.__titleEl) return;
    if (!on || searchTerms.length === 0) {
      if (it.__titleEl.dataset.hl === "1") {
        it.__titleEl.textContent = it.__titleText;
        delete it.__titleEl.dataset.hl;
      }
      return;
    }
    let html = "";
    const text = it.__titleText;
    const lower = text.toLowerCase();
    // Build a combined match map across all terms.
    const marks = new Array(text.length).fill(false);
    for (const term of searchTerms) {
      let i = lower.indexOf(term);
      while (i !== -1) {
        for (let j = i; j < i + term.length; j++) marks[j] = true;
        i = lower.indexOf(term, i + term.length);
      }
    }
    for (let i = 0; i < text.length; i++) {
      const ch = text[i].replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      if (marks[i] && (i === 0 || !marks[i - 1])) html += "<mark>";
      html += ch;
      if (marks[i] && (i === text.length - 1 || !marks[i + 1])) html += "</mark>";
    }
    it.__titleEl.innerHTML = html;
    it.__titleEl.dataset.hl = "1";
  }

  // ---- Apply filters ------------------------------------------------------
  function apply() {
    let visible = 0;
    const matched = { code: 0, news: 0, papers: 0 };
    const timeLimit = state.time ? parseFloat(state.time) : Infinity;

    for (const it of items) {
      const tags = (it.dataset.tags || "").split(/\s+/).filter(Boolean);
      const ok =
        (!state.method || tags.indexOf(state.method) !== -1) &&
        (!state.platform || tags.indexOf(state.platform) !== -1) &&
        (!state.source || it.dataset.source === state.source) &&
        (!state.priority || it.dataset.priority === state.priority) &&
        it.__ageDays <= timeLimit &&
        (searchTerms.length === 0 || searchTerms.every((t) => it.__searchText.indexOf(t) !== -1));

      it.style.display = ok ? "" : "none";
      highlightTitle(it, ok);
      if (ok) {
        visible++;
        matched[it.__section]++;
      }
    }

    if (counter) counter.textContent = visible.toString();
    sectionCounts.forEach((el) => {
      const key = el.dataset.section;
      if (key && matched[key] !== undefined) el.textContent = matched[key].toString();
    });
    if (emptyState) emptyState.hidden = visible !== 0;
  }

  // ---- Sort ---------------------------------------------------------------
  // Default ("Score" chip): priority tier (High->Mid->Low) then newest within.
  const PRANK = { must_read: 2, save_for_later: 1, low_priority: 0 };
  function dateMs(art) { return Date.parse(art.dataset.published || art.dataset.created || "") || 0; }
  function cmp(a, b) {
    const k = state.sort;
    if (k === "created") return (Date.parse(b.dataset.created || "") || 0) - (Date.parse(a.dataset.created || "") || 0);
    if (k === "pushed") return dateMs(b) - dateMs(a);
    if (k === "stars") return parseInt(b.dataset.stars || "0", 10) - parseInt(a.dataset.stars || "0", 10);
    // default / "score": priority tier desc, then recency desc
    const pr = (PRANK[b.dataset.priority] || 0) - (PRANK[a.dataset.priority] || 0);
    return pr !== 0 ? pr : dateMs(b) - dateMs(a);
  }
  function applySort() {
    document.querySelectorAll("section.section .items").forEach((container) => {
      const arts = Array.from(container.children).filter((c) => c.tagName === "ARTICLE");
      arts.sort(cmp);
      for (const art of arts) container.appendChild(art);
    });
  }

  // ---- Chips --------------------------------------------------------------
  document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const filter = btn.dataset.filter;
      const value = btn.dataset.value || "";
      state[filter] = value;
      document.querySelectorAll('.chip[data-filter="' + filter + '"]').forEach((b) => {
        b.classList.toggle("active", (b.dataset.value || "") === value);
      });
      if (filter === "sort") applySort();
      apply();
    });
  });

  // ---- Broken thumbnail -> placeholder ------------------------------------
  document.querySelectorAll("img.item-thumb").forEach((img) => {
    img.addEventListener("error", () => {
      const src = img.dataset.source || "arxiv";
      const ph = document.createElement("div");
      ph.className = "item-thumb item-thumb-placeholder item-thumb-" + src;
      ph.innerHTML = "<span>" + src + "</span>";
      img.replaceWith(ph);
    });
  });

  // ---- Back to top --------------------------------------------------------
  const toTop = document.getElementById("back-to-top");
  if (toTop) {
    window.addEventListener("scroll", () => {
      toTop.hidden = window.scrollY < 600;
    }, { passive: true });
    toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  }

  // Initial paint (applies the PAGE limit on load).
  apply();
})();
