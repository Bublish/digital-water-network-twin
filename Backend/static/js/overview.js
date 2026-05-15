// Page 1 (Overview) — wires up SSE for live state and POSTs for actions.

const banner = document.getElementById("error-banner");

function showBanner(msg) { banner.textContent = msg; banner.hidden = false; }
function hideBanner() { banner.hidden = true; }

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
