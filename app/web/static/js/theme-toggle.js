// Theme toggle. Initial value is set in base.html before the stylesheet loads
// (FOUC prevention). This script handles the toggle interaction and dispatches
// a 'themechange' CustomEvent so other scripts can re-theme (uPlot, panzoom).
(function () {
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode etc. */ }
    window.dispatchEvent(new CustomEvent("themechange", { detail: { theme: next } }));
  });
})();
