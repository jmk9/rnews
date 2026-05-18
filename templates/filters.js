(function () {
  "use strict";
  const state = { tag: "", source: "", priority: "" };
  const items = Array.from(document.querySelectorAll(".item"));
  const counter = document.getElementById("visible-count");

  function apply() {
    let visible = 0;
    for (const it of items) {
      const tags = (it.dataset.tags || "").split(/\s+/).filter(Boolean);
      const okTag = !state.tag || tags.indexOf(state.tag) !== -1;
      const okSrc = !state.source || it.dataset.source === state.source;
      const okPri = !state.priority || it.dataset.priority === state.priority;
      const show = okTag && okSrc && okPri;
      it.style.display = show ? "" : "none";
      if (show) visible++;
    }
    if (counter) counter.textContent = visible.toString();
  }

  document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const filter = btn.dataset.filter;
      const value = btn.dataset.value || "";
      state[filter] = value;
      document
        .querySelectorAll('.chip[data-filter="' + filter + '"]')
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      apply();
    });
  });
})();
