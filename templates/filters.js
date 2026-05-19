(function () {
  "use strict";
  const state = { tag: "", source: "", priority: "", time: "" };
  const items = Array.from(document.querySelectorAll(".item"));
  const counter = document.getElementById("visible-count");
  const sectionCounts = document.querySelectorAll(".section-count");
  const now = Date.now();
  const DAY_MS = 24 * 3600 * 1000;

  // Each item is born with a data-published date string (YYYY-MM-DD).
  // We precompute its age in days once so filtering is O(1) per item.
  for (const it of items) {
    const ds = it.dataset.published;
    let age = Infinity;
    if (ds) {
      const t = Date.parse(ds);
      if (!Number.isNaN(t)) age = (now - t) / DAY_MS;
    }
    it.__ageDays = age;
  }

  function apply() {
    let visible = 0;
    const perSection = { code: 0, news: 0, papers: 0 };
    const timeLimit = state.time ? parseFloat(state.time) : Infinity;
    for (const it of items) {
      const tags = (it.dataset.tags || "").split(/\s+/).filter(Boolean);
      const okTag = !state.tag || tags.indexOf(state.tag) !== -1;
      const okSrc = !state.source || it.dataset.source === state.source;
      const okPri = !state.priority || it.dataset.priority === state.priority;
      const okTime = it.__ageDays <= timeLimit;
      const show = okTag && okSrc && okPri && okTime;
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
      apply();
    });
  });
})();
