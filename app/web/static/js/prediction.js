/* Prediction Engine page: node selector, 3-band uPlot pressure chart, SHAP bars. */
(function () {
  "use strict";

  const statusEl = document.getElementById("pred-status");
  const selectEl = document.getElementById("pred-node");
  const metricsEl = document.getElementById("pred-metrics");
  const chartEl = document.getElementById("pred-chart");
  const shapEl = document.getElementById("shap-bars");
  const shapLabel = document.getElementById("shap-node-label");
  const liveEl = document.getElementById("pred-live");

  let chart = null;
  let lastRegions = null;

  // Live-refresh throttle/coalesce state (chart only; SHAP refreshes on node change).
  const MIN_REFRESH_MS = 3000;
  let lastRefreshAt = 0;
  let refreshInFlight = false;
  let pendingRefresh = false;
  let refreshTimer = null;

  function themeColors() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    return {
      seed: dark ? "rgba(56,189,248,0.10)" : "rgba(2,132,199,0.08)",
      live: dark ? "rgba(34,197,94,0.10)" : "rgba(22,163,74,0.08)",
      forecast: dark ? "rgba(168,85,247,0.12)" : "rgba(147,51,234,0.08)",
      actual: dark ? "#e2e8f0" : "#0f172a",
      fit: dark ? "#38bdf8" : "#0284c7",
      forecastLine: dark ? "#a855f7" : "#9333ea",
      grid: dark ? "rgba(148,163,184,0.2)" : "rgba(15,23,42,0.1)",
      text: dark ? "#cbd5e1" : "#334155",
    };
  }

  /* Align several [{hr,p}] series onto one sorted x axis; missing => null. */
  function buildAligned(seriesList) {
    const xset = new Set();
    seriesList.forEach((s) => s.forEach((pt) => xset.add(pt.hr)));
    const xs = Array.from(xset).sort((a, b) => a - b);
    const index = new Map(xs.map((x, i) => [x, i]));
    const ys = seriesList.map((s) => {
      const arr = new Array(xs.length).fill(null);
      s.forEach((pt) => { arr[index.get(pt.hr)] = pt.p; });
      return arr;
    });
    return [xs, ...ys];
  }

  function bandsPlugin() {
    return {
      hooks: {
        draw: (u) => {
          if (!lastRegions) return;
          const c = themeColors();
          const { seed_end, live_end, forecast_end } = lastRegions;
          const x0 = u.scales.x.min, x1 = u.scales.x.max;
          const span = [
            [Math.max(x0, u.data[0][0] ?? x0), seed_end, c.seed],
            [seed_end, live_end, c.live],
            [live_end, Math.min(x1, forecast_end), c.forecast],
          ];
          const top = u.bbox.top, h = u.bbox.height;
          const ctx = u.ctx;
          ctx.save();
          span.forEach(([a, b, color]) => {
            if (b <= a) return;
            const xa = u.valToPos(a, "x", true);
            const xb = u.valToPos(b, "x", true);
            ctx.fillStyle = color;
            ctx.fillRect(xa, top, xb - xa, h);
          });
          ctx.restore();
        },
      },
    };
  }

  function renderChart(data) {
    const c = themeColors();
    const actual = data.seed.concat(data.live);
    const aligned = buildAligned([actual, data.overlay, data.forecast]);
    lastRegions = data.regions;

    if (chart) { chart.destroy(); chart = null; }
    const opts = {
      width: chartEl.clientWidth || 800,
      height: 360,
      plugins: [bandsPlugin()],
      scales: { x: { time: false } },
      axes: [
        { stroke: c.text, grid: { stroke: c.grid }, label: "Hours" },
        { stroke: c.text, grid: { stroke: c.grid }, label: "Pressure [psi]" },
      ],
      series: [
        { label: "Hour" },
        { label: "Actual", stroke: c.actual, width: 2 },
        { label: "Model fit", stroke: c.fit, width: 2, dash: [6, 4] },
        { label: "Forecast", stroke: c.forecastLine, width: 2, dash: [6, 4] },
      ],
    };
    chart = new uPlot(opts, aligned, chartEl);

    const m = data.metrics || {};
    metricsEl.textContent = (m.live_rmse != null)
      ? `live RMSE ${m.live_rmse.toFixed(2)} psi · R² ${m.live_r2 != null ? m.live_r2.toFixed(3) : "—"}`
      : (data.note || "");
  }

  function renderShap(data) {
    shapLabel.textContent = data.node_id ? `(${data.node_id})` : "";
    shapEl.innerHTML = "";
    const feats = data.features || [];
    const max = feats.reduce((mx, f) => Math.max(mx, f.mean_abs_shap), 0) || 1;
    feats.forEach((f) => {
      const row = document.createElement("div");
      row.className = "shap-row";
      const name = document.createElement("span");
      name.className = "shap-name";
      name.textContent = f.name;
      const track = document.createElement("span");
      track.className = "shap-track";
      const bar = document.createElement("span");
      bar.className = "shap-bar";
      bar.style.width = `${(f.mean_abs_shap / max) * 100}%`;
      bar.title = f.mean_abs_shap.toFixed(4);
      track.appendChild(bar);
      row.appendChild(name);
      row.appendChild(track);
      shapEl.appendChild(row);
    });
  }

  async function loadNode(nodeId) {
    if (!nodeId) return;
    statusEl.textContent = `Loading ${nodeId}…`;
    try {
      const [pred, shap] = await Promise.all([
        fetch(`/prediction/node/${encodeURIComponent(nodeId)}`).then((r) => r.json()),
        fetch(`/prediction/node/${encodeURIComponent(nodeId)}/shap`).then((r) => r.json()),
      ]);
      renderChart(pred);
      renderShap(shap);
      statusEl.textContent = "";
    } catch (e) {
      statusEl.textContent = `Failed to load ${nodeId}: ${e}`;
    }
  }

  function liveEnabled() {
    return liveEl ? liveEl.checked : true;
  }

  // Called once per simulation step (via SSE). Coalesces bursts into at most one
  // chart refresh per MIN_REFRESH_MS — with a trailing refresh so the chart ends
  // on fresh data — and never more than one request in flight. SHAP is not
  // refreshed here (it changes little step-to-step and is comparatively heavy).
  function onSimStep() {
    if (!liveEnabled()) return;
    pendingRefresh = true;
    maybeRefresh();
  }

  function maybeRefresh() {
    if (refreshInFlight || !pendingRefresh || !liveEnabled()) return;
    if (document.hidden) return;            // resume via visibilitychange
    const nodeId = selectEl.value;
    if (!nodeId) return;

    const wait = MIN_REFRESH_MS - (Date.now() - lastRefreshAt);
    if (wait > 0) {
      if (refreshTimer === null) {
        refreshTimer = setTimeout(() => { refreshTimer = null; maybeRefresh(); }, wait);
      }
      return;
    }

    pendingRefresh = false;
    refreshInFlight = true;
    lastRefreshAt = Date.now();
    fetch(`/prediction/node/${encodeURIComponent(nodeId)}`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((pred) => { renderChart(pred); })
      .catch(() => { /* keep last good chart on transient errors */ })
      .finally(() => {
        refreshInFlight = false;
        if (pendingRefresh) maybeRefresh();
      });
  }

  function startLiveUpdates() {
    const es = new EventSource("/sim/stream");
    es.onmessage = () => onSimStep();
    es.onerror = () => { /* EventSource auto-reconnects */ };

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { pendingRefresh = true; maybeRefresh(); }
    });
    if (liveEl) liveEl.addEventListener("change", () => {
      if (liveEnabled()) { pendingRefresh = true; maybeRefresh(); }
    });
  }

  async function populateNodes() {
    const { nodes } = await fetch("/prediction/nodes").then((r) => r.json());
    selectEl.innerHTML = "";
    nodes.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      selectEl.appendChild(opt);
    });
    selectEl.disabled = false;
    selectEl.addEventListener("change", () => loadNode(selectEl.value));
    if (nodes.length) loadNode(nodes[0]);
  }

  async function pollUntilReady() {
    for (;;) {
      let s;
      try { s = await fetch("/prediction/status").then((r) => r.json()); }
      catch (e) { statusEl.textContent = "Status unavailable; retrying…"; await wait(3000); continue; }
      if (s.state === "ready") { statusEl.textContent = ""; return true; }
      if (s.state === "failed") { statusEl.textContent = `Training failed: ${s.error || ""}`; return false; }
      statusEl.textContent = s.state === "seeding"
        ? "Generating training data (seeding)…"
        : "Training model…";
      await wait(3000);
    }
  }

  function wait(ms) { return new Promise((res) => setTimeout(res, ms)); }

  // Re-render on theme toggle so colours/bands track the active theme.
  new MutationObserver(() => { if (selectEl.value) loadNode(selectEl.value); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  (async function init() {
    const ready = await pollUntilReady();
    if (ready) {
      await populateNodes();
      startLiveUpdates();
    }
  })();
})();
