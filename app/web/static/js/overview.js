// Page 1 (Overview) — wires up SSE for live state and POSTs for actions.

const banner = document.getElementById("error-banner");

// Latest cached SSE payload — consulted by the network-plot tooltip.
let latestState = {};

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
    const li = document.querySelector(`#tank-list li[data-tank-id="${tid}"]`);
    if (!li) continue;
    li.querySelector(".tank-level").textContent = level.toFixed(1);
    const min = parseFloat(li.dataset.tankMin || "0");
    const max = parseFloat(li.dataset.tankMax || "1");
    const span = max - min;
    const pct = span > 0 ? Math.max(0, Math.min(100, ((level - min) / span) * 100)) : 0;
    li.querySelector(".tank-fill").style.width = pct.toFixed(1) + "%";
  }
}

function updatePumps(state) {
  for (const [pid, pumpState] of Object.entries(state.pump_states || {})) {
    const li = document.querySelector(`#pump-list li[data-pump-id="${pid}"]`);
    if (!li) continue;
    const badge = li.querySelector(".pump-state");
    badge.textContent = pumpState;
    badge.className = "pump-state status status-" + pumpState.toLowerCase();
  }
  // NOTE: pump_modes radios are NOT overwritten — operator owns them mid-click.
}

function applyState(state) {
  latestState = state;
  updateSimControl(state);
  updateTanks(state);
  updatePumps(state);
}

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
(function initNetworkPlot() {
  const wrap  = document.querySelector(".network-plot-wrap");
  const inner = document.getElementById("network-plot");
  const svg   = inner && inner.querySelector("svg");
  const tip   = document.getElementById("network-tooltip");
  const geomEl = document.getElementById("network-geometry");
  if (!wrap || !svg || !tip || !geomEl || typeof Panzoom !== "function") return;

  const geom = JSON.parse(geomEl.textContent);
  const nodeById = new Map(geom.nodes.map(n => [n.id, n]));

  const pz = Panzoom(inner, { maxScale: 12, minScale: 0.5, canvas: true });
  wrap.addEventListener("wheel", pz.zoomWithWheel);
  document.getElementById("network-reset").addEventListener("click", () => pz.reset());
  svg.addEventListener("dblclick", () => pz.reset());

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

  // squared point-to-segment distance
  function distSeg2(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1e-9;
    let t = ((px - ax) * dx + (py - ay) * dy) / len2;
    if (t < 0) t = 0; else if (t > 1) t = 1;
    const cx = ax + t * dx, cy = ay + t * dy;
    return (px - cx) ** 2 + (py - cy) ** 2;
  }

  wrap.addEventListener("mousemove", (e) => {
    // Use the SVG's bounding rect after pan/zoom — pixel→SVG-unit scale stays
    // valid because the wrap clips and the SVG scales uniformly with the transform.
    const rect = svg.getBoundingClientRect();
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
  });
  wrap.addEventListener("mouseleave", () => { tip.hidden = true; });
})();
