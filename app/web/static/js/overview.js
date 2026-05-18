// Page 1 (Overview) — wires up SSE for live state, POSTs for actions, and the
// demand-pattern chart. Theme toggles re-init uPlot and Panzoom against the
// currently-visible network SVG.

const banner = document.getElementById("error-banner");

// Latest cached SSE payload — consulted by the network-plot tooltip.
let latestState = {};
let lastPatternId = null;

const tanksMetaEl = document.getElementById("tanks-meta");
const tanksMeta = tanksMetaEl ? JSON.parse(tanksMetaEl.textContent) : {};

function el(tag, attrs, text) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "dataset") for (const [dk, dv] of Object.entries(v)) e.dataset[dk] = dv;
    else if (k === "style") for (const [sk, sv] of Object.entries(v)) e.style[sk] = sv;
    else e.setAttribute(k, v);
  }
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function ensureTankRow(tid) {
  let li = document.querySelector(`#tank-list li[data-tank-id="${tid}"]`);
  if (li) return li;
  const meta = tanksMeta[tid] || { min_level_ft: 0, max_level_ft: 1 };
  li = el("li", { dataset: { tankId: tid, tankMin: meta.min_level_ft, tankMax: meta.max_level_ft } });

  const text = el("div", { class: "tank-row-text" });
  text.appendChild(el("span", { class: "row-id" }, tid));
  const numWrap = el("span", { class: "mono" });
  numWrap.appendChild(el("span", { class: "tank-level" }, "—"));
  numWrap.appendChild(document.createTextNode(" / "));
  numWrap.appendChild(el("span", { class: "tank-max" }, Number(meta.max_level_ft).toFixed(0)));
  numWrap.appendChild(document.createTextNode(" ft"));
  text.appendChild(numWrap);
  li.appendChild(text);

  const bar = el("div", { class: "tank-bar" });
  bar.appendChild(el("div", { class: "tank-fill", style: { width: "0%" } }));
  li.appendChild(bar);

  document.getElementById("tank-list").appendChild(li);
  return li;
}

function ensurePumpRow(pid, mode, pumpState) {
  let li = document.querySelector(`#pump-list li[data-pump-id="${pid}"]`);
  if (li) return li;
  const modeVal = mode || "AUTO";
  const stateVal = pumpState || "—";
  li = el("li", { dataset: { pumpId: pid } });

  li.appendChild(el("span", { class: "row-id" }, pid));
  li.appendChild(el("span", { class: "pump-state status status-" + stateVal.toLowerCase() }, stateVal));

  const modes = el("div", { class: "pump-modes" });
  for (const m of [["AUTO", "AUTO"], ["HAND_OPEN", "OPEN"], ["HAND_CLOSED", "CLOSED"]]) {
    const label = el("label");
    const radio = el("input", { type: "radio", name: "mode-" + pid, value: m[0] });
    if (modeVal === m[0]) radio.checked = true;
    label.appendChild(radio);
    label.appendChild(document.createTextNode(" " + m[1]));
    modes.appendChild(label);
  }
  li.appendChild(modes);

  document.getElementById("pump-list").appendChild(li);
  return li;
}

function showBanner(msg) { banner.textContent = msg; banner.hidden = false; }
function hideBanner() { banner.hidden = true; }

async function refreshStateNow() {
  try {
    const r = await fetch("/sim/state");
    if (r.ok) applyState(await r.json());
  } catch (e) { /* swallow */ }
}

function updateSimControl(state) {
  const statusEl = document.getElementById("sim-status");
  statusEl.textContent = state.status;
  statusEl.className = "status status-" + state.status.toLowerCase();
  document.getElementById("sim-id").textContent = state.sim_id || "—";
  document.getElementById("sim-time").textContent = (state.sim_time_hr || 0).toFixed(2);
}

function updateTanks(state) {
  for (const [tid, level] of Object.entries(state.tank_levels || {})) {
    const li = ensureTankRow(tid);
    li.querySelector(".tank-level").textContent = level.toFixed(1);
    const min = parseFloat(li.dataset.tankMin || "0");
    const max = parseFloat(li.dataset.tankMax || "1");
    const span = max - min;
    const pct = span > 0 ? Math.max(0, Math.min(100, ((level - min) / span) * 100)) : 0;
    li.querySelector(".tank-fill").style.width = pct.toFixed(1) + "%";
  }
}

function updatePumps(state) {
  const modes  = state.pump_modes  || {};
  const states = state.pump_states || {};
  const ids = new Set([...Object.keys(modes), ...Object.keys(states)]);
  for (const pid of ids) {
    const li = ensurePumpRow(pid, modes[pid], states[pid]);
    const badge = li.querySelector(".pump-state");
    const pumpState = states[pid] || "—";
    badge.textContent = pumpState;
    badge.className = "pump-state status status-" + pumpState.toLowerCase();
  }
  // NOTE: pump_modes radios are NOT overwritten on subsequent ticks — operator owns them mid-click.
}

function updateEnergy(state) {
  const totalKwEl  = document.getElementById("energy-total-kw");
  const stepKwhEl  = document.getElementById("energy-step-kwh");
  const stepEurEl  = document.getElementById("energy-step-eur");
  const listEl     = document.getElementById("energy-pump-list");
  if (!totalKwEl || !stepKwhEl || !stepEurEl || !listEl) return;

  const totalKw  = state.total_power_kw;
  const stepKwh  = state.step_energy_kwh;
  const stepEur  = state.step_cost_eur;

  totalKwEl.textContent = (typeof totalKw === "number") ? totalKw.toFixed(2) : "—";
  stepKwhEl.textContent = (typeof stepKwh === "number") ? stepKwh.toFixed(2) : "—";
  stepEurEl.textContent = (typeof stepEur === "number") ? stepEur.toFixed(4) : "—";

  const powers = state.pump_powers_kw       || {};
  const energy = state.pump_step_energy_kwh || {};
  const costs  = state.pump_step_cost_eur   || {};
  const pumpIds = Object.keys(powers).sort();

  // Build/refresh per-pump rows. Re-render rather than reconcile — short list.
  while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
  for (const pid of pumpIds) {
    const li = el("li", { dataset: { pumpId: pid } });
    li.appendChild(el("span", { class: "row-id" }, pid));
    const kwSpan  = el("span", { class: "mono energy-cell" },
                       (powers[pid] || 0).toFixed(2) + " kW");
    const eurVal  = costs[pid];
    const eurText = (typeof eurVal === "number") ? eurVal.toFixed(4) + " EUR" : "— EUR";
    const eurSpan = el("span", { class: "mono energy-cell" }, eurText);
    li.appendChild(kwSpan);
    li.appendChild(eurSpan);
    listEl.appendChild(li);
  }
}

function updatePrice(state) {
  const kwhEl    = document.getElementById("price-kwh");
  const mwhEl    = document.getElementById("price-mwh");
  const sourceEl = document.getElementById("price-source");
  if (!kwhEl) return;
  const p = state.current_price;
  if (p === null || p === undefined) {
    kwhEl.textContent = "—";
    mwhEl.textContent = "—";
    sourceEl.textContent = state.status === "RUNNING" ? "pricing disabled" : "awaiting first tick";
    return;
  }
  kwhEl.textContent = p.toFixed(4);
  mwhEl.textContent = (p * 1000).toFixed(2);
  sourceEl.textContent = "live (Energy-Charts CC BY 4.0)";
}

// --- Pattern chart ---
const patternEl = document.getElementById("pattern-chart");
const patternIdLabel = document.getElementById("pattern-id-label");
const patternStepLabel = document.getElementById("pattern-step");
const patternChart = (typeof createPatternChart === "function" && patternEl)
  ? createPatternChart(patternEl)
  : { setPattern() {}, setCursor() {}, reTheme() {}, destroy() {} };

async function fetchPattern() {
  try {
    const r = await fetch("/sim/pattern");
    if (!r.ok) return;
    const body = await r.json();
    patternChart.setPattern(body.multipliers || []);
    patternIdLabel.textContent = body.pattern_id || "no pattern";
  } catch (e) { /* swallow */ }
}

function updatePattern(state) {
  const pid = state.pattern_id || null;
  if (pid !== lastPatternId) {
    lastPatternId = pid;
    if (pid) fetchPattern();
    else { patternChart.setPattern([]); patternIdLabel.textContent = "no pattern"; }
  }
  // Cursor: step within current 24h cycle.
  const t = state.sim_time_hr;
  if (typeof t === "number" && pid) {
    const stepIdx = Math.floor(((t % 24) + 24) % 24 * 4); // 0..95
    patternChart.setCursor(stepIdx);
    patternStepLabel.textContent = String(stepIdx);
  } else {
    patternChart.setCursor(null);
    patternStepLabel.textContent = "—";
  }
}

function applyState(state) {
  latestState = state;
  updateSimControl(state);
  updatePrice(state);
  updateTanks(state);
  updatePumps(state);
  updatePattern(state);
  updateEnergy(state);
}

// Initial pattern fetch (covers reload after pattern was already installed)
fetchPattern();

// --- SSE ---
const es = new EventSource("/sim/stream");
es.onmessage = (ev) => {
  hideBanner();
  try {
    applyState(JSON.parse(ev.data));
  } catch (err) {
    console.error("Bad SSE payload", err, ev.data);
  }
};
es.onerror = () => showBanner("Reconnecting to server…");

// --- Buttons ---
async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : null,
  });
  if (!res.ok) {
    const detail = await res.text();
    showBanner(`${url} -> ${res.status}: ${detail}`);
    refreshStateNow();
    throw new Error(detail);
  }
  return res.json();
}

function timeScale() {
  return parseInt(document.getElementById("time-scale").value, 10) || 1;
}

document.getElementById("btn-start").addEventListener("click", () =>
  postJson("/sim/start", {time_scale: timeScale()}).catch(() => {})
);
document.getElementById("btn-stop").addEventListener("click", () =>
  postJson("/sim/stop").catch(() => {})
);
document.getElementById("btn-reset").addEventListener("click", () =>
  postJson("/sim/reset", {time_scale: timeScale()}).catch(() => {})
);

// --- Pump override radios ---
document.getElementById("pump-list").addEventListener("change", (ev) => {
  if (ev.target.tagName !== "INPUT" || ev.target.type !== "radio") return;
  const li = ev.target.closest("li[data-pump-id]");
  if (!li) return;
  const pumpId = li.dataset.pumpId;
  const mode   = ev.target.value;
  postJson("/sim/override", {pump_id: pumpId, mode}).catch(() => {});
});

// --- Network plot: pan, zoom, hover tooltips ---
// Picks whichever SVG variant is currently visible (light or dark). Switching
// theme re-runs this initializer against the now-visible SVG.
const geomEl = document.getElementById("network-geometry");
const geom = geomEl ? JSON.parse(geomEl.textContent) : null;
const nodeById = geom ? new Map(geom.nodes.map(n => [n.id, n])) : null;

let pzInstance = null;
let activePlotInner = null;

function visibleNetworkInner() {
  const theme = document.documentElement.dataset.theme || "light";
  return document.querySelector(
    `.network-svg-variant[data-theme-variant="${theme}"]`
  );
}

function initNetworkPlot() {
  if (!geom) return;
  const wrap = document.querySelector(".network-plot-wrap");
  const inner = visibleNetworkInner();
  const svg = inner && inner.querySelector("svg");
  const tip = document.getElementById("network-tooltip");
  if (!wrap || !svg || !tip || typeof Panzoom !== "function") return;

  if (pzInstance) {
    try { pzInstance.destroy(); } catch (e) { /* noop */ }
    pzInstance = null;
  }
  activePlotInner = inner;
  pzInstance = Panzoom(inner, { maxScale: 12, minScale: 0.5, canvas: true });
  wrap.addEventListener("wheel", pzInstance.zoomWithWheel);

  const resetBtn = document.getElementById("network-reset");
  resetBtn.onclick = () => pzInstance.reset();
  svg.ondblclick = () => pzInstance.reset();

  const NODE_HIT_PX = 12;
  const LINK_HIT_PX = 6;

  function fmt(v, digits) {
    if (digits === undefined) digits = 1;
    return (v === undefined || v === null) ? "—" : Number(v).toFixed(digits);
  }
  function tooltipText(hit) {
    const item = hit.item;
    if (hit.kind === "node") {
      if (item.type === "junction") {
        return "junction " + item.id +
               "\npressure: " + fmt(latestState.pressures && latestState.pressures[item.id]) + " psi";
      }
      if (item.type === "tank") {
        return "tank " + item.id +
               "\nlevel: " + fmt(latestState.tank_levels && latestState.tank_levels[item.id]) + " ft";
      }
      return "reservoir " + item.id;
    }
    const flow = latestState.flows && latestState.flows[item.id];
    if (item.type === "pump") {
      const st = (latestState.pump_states && latestState.pump_states[item.id]) || "—";
      return "pump " + item.id +
             "\nstatus: " + st +
             "\nflow: " + fmt(flow) + " gpm";
    }
    return item.type + " " + item.id + "\nflow: " + fmt(flow) + " gpm";
  }
  function distSeg2(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-9;
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    if (t < 0) t = 0; else if (t > 1) t = 1;
    const cx = ax + t * dx, cy = ay + t * dy;
    return (px - cx) ** 2 + (py - cy) ** 2;
  }

  wrap.onmousemove = (e) => {
    const liveSvg = activePlotInner.querySelector("svg");
    if (!liveSvg) { tip.hidden = true; return; }
    const rect = liveSvg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) { tip.hidden = true; return; }
    const scaleX = geom.svg_width  / rect.width;
    const scaleY = geom.svg_height / rect.height;
    const sx = (e.clientX - rect.left) * scaleX;
    const sy = (e.clientY - rect.top)  * scaleY;

    let best = null;
    let bestD2 = NODE_HIT_PX * NODE_HIT_PX;
    for (const n of geom.nodes) {
      const d2 = (n.x - sx) ** 2 + (n.y - sy) ** 2;
      if (d2 < bestD2) { bestD2 = d2; best = { kind: "node", item: n }; }
    }
    if (!best) {
      let bestL2 = LINK_HIT_PX * LINK_HIT_PX;
      for (const l of geom.links) {
        const a = nodeById.get(l.from);
        const b = nodeById.get(l.to);
        if (!a || !b) continue;
        const d2 = distSeg2(sx, sy, a.x, a.y, b.x, b.y);
        if (d2 < bestL2) { bestL2 = d2; best = { kind: "link", item: l }; }
      }
    }
    if (best) {
      tip.textContent = tooltipText(best);
      const wrapRect = wrap.getBoundingClientRect();
      tip.style.left = (e.clientX - wrapRect.left + 12) + "px";
      tip.style.top  = (e.clientY - wrapRect.top  + 12) + "px";
      tip.hidden = false;
    } else {
      tip.hidden = true;
    }
  };
  wrap.onmouseleave = () => { tip.hidden = true; };
}

initNetworkPlot();

// React to theme changes: re-init plot panzoom against the new SVG, redraw chart.
window.addEventListener("themechange", () => {
  initNetworkPlot();
  patternChart.reTheme();
});
