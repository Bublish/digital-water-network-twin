# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **SCADA web application** for monitoring and managing water distribution networks. The primary feature is an AI-based pump control system with explainable AI (XAI) analysis of pump scheduling decisions. The network under study is **WSN1** (Water Sensor Network 1) — a hypothetical transmission dense-loop network from the Battle of the Networks benchmark (Ostfeld, 2021) with ~130 junctions, 1 reservoir, 2 pump stations, 2 tanks, and 23.3 miles of pipe.

The backend runs a continuous EPANET hydraulic simulation stepped at 15-min intervals. Each step the simulator reads tank/pump state, asks an ML service for pump commands, applies them, solves hydraulics, persists results to Supabase, and pushes the new state to all connected browsers via Server-Sent Events.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (lifespan-managed singletons) |
| Templating | Jinja2 (server-side rendered HTML) |
| Frontend | Vanilla HTML/CSS/JS, no framework; `EventSource` for live updates |
| Database & storage | Supabase (project: **EPANETSIM**) |
| EPANET engine | `epyt` (wraps the EPANET C engine) |
| Network visualisation | `epyt.plot()` → matplotlib SVG, with a JSON geometry sidecar for client hit-testing |
| ML models (notebook) | XGBoost, MLP (sklearn), GAM (pygam), EBM (interpret) |
| Explainability | SHAP, permutation importance |

---

## Repository Layout

```
app/
  __init__.py
  api/
    main.py              # FastAPI app factory + lifespan (wires singletons onto app.state)
    sim_routes.py        # /sim/{start,stop,reset,state,stream,override,overrides}
    ml_routes.py         # POST /ml/predict (stub controller)
    network_routes.py    # GET /network/{info,plot.svg}
    web_routes.py        # GET /, /pump-control, /xai (Jinja2 pages)
    schemas.py           # Pydantic HTTP-boundary models
  data/
    WSN1.inp             # EPANET network (local reference; canonical copy in Supabase storage)
    WSN1.rpt             # Raw EPANET hydraulic report
    WSN1 - report.txt    # Pre-parsed node/link results used by the notebook
    DMA4_demand.pat      # Demand pattern file
    EPANET Analysis.ipynb  # Offline ML pipeline (predictions, evaluation, explainability)
  db/
    SupabaseClient.py    # SupabaseDB singleton — all storage + DB I/O goes here
  scheduler/
    SimulationRunner.py  # Async tick-loop orchestrator (state machine, SSE broadcast)
  simulation/
    Simulator.py         # EPANETSimulator — context manager wrapping epyt
    Pattern.py           # DemandPattern — 96-step sinusoidal multipliers + lognormal noise
    Randomizer.py        # Lognormal noise for base demands and pattern arrays
    types.py             # Internal dataclasses + enums (SimStatus, PumpMode, StepState, StepResult)
  tests/                 # pytest suite; conftest.py adds repo root to sys.path
  web/
    templates/           # base.html, overview.html, pump_control.html, xai.html
    static/              # css/, js/, vendor/
requirements.txt
.env                     # SUPABASE_URL, SUPABASE_KEY
```

> All Python modules use absolute `app.*` imports. The repo root must be on `sys.path` — `pytest` handles this via `app/tests/conftest.py`; `uvicorn app.api.main:app` works when launched from the repo root.

---

## Running the App

```bash
# Install dependencies (requirements.txt already covers fastapi/uvicorn/httpx/pydantic/pytest)
pip install -r requirements.txt

# Notebook-only ML deps (not needed to run the web app):
pip install pandas scikit-learn xgboost pygam interpret shap matplotlib jupyter

# FastAPI server — run from the repo root
uvicorn app.api.main:app --reload

# Full pytest suite
pytest app/tests/

# Run a single test
pytest app/tests/test_simulation_runner.py::test_name
```

VS Code launch config at `.vscode/launch.json` runs `uvicorn app.api.main:app` from the workspace root.

> ⚠ **Supabase setup required before first run:** upload `WSN1.inp` to the `network` storage bucket — `EPANETSimulator.__init__` downloads it on construction and will raise `RuntimeError` if missing.

---

## Runtime Architecture

### Startup (`app/api/main.py` lifespan)

On FastAPI startup, one each of these is constructed and stashed on `app.state`:

- `EPANETSimulator()` then `.load()` — opens the network in a tempdir; **does not start the hydraulic loop**.
- `SupabaseDB()` — singleton client.
- `httpx.AsyncClient()` — used by the runner to call `/ml/predict`.
- `SimulationRunner(sim, db, http_client, ml_url=...)` → `app.state.runner`.
- `sim.compute_network_info()` → `app.state.network_info`.
- `sim.render_plot_svg()` → `(svg_str, geometry)` → `app.state.network_plot_svg`, `app.state.network_geometry`.
- `Jinja2Templates(...)` → `app.state.templates`.

The runner is in `NOT_STARTED` until a client POSTs `/sim/start`.

On shutdown the runner is stopped, the HTTP client is closed, the simulator is closed (epyt unloaded, tempdir removed), then `db.clear_live_tables()` is called. **The `clear_live_tables` call is debug-only and is marked for removal before production.**

### SimulationRunner state machine

```
NOT_STARTED --start(time_scale)--> RUNNING --stop()--> STOPPED
                                     |                    |
                                     +----- start() ------+
```

`time_scale` controls how fast simulated time advances: `900 / time_scale` wall-seconds per step. `1` = real-time, `60` = demo, `10000` = fast. `/sim/reset` is `stop` + `start` in one call.

### Tick loop (every 15 min of sim time)

For each tick under `self._lock`:

1. **Day boundary**: every 96 steps (incl. step 0), call `sim.rotate_demand_pattern()` — installs a fresh lognormal 96-step pattern and re-randomizes per-node base demands.
2. **Read state**: `sim.read_state()` returns `StepState(sim_time_sec, tank_levels, pump_states)`.
3. **Call ML**: POST `PredictRequest` (sim_time_hr, tank_levels, pump_modes, current_price) to `ml_url` with a **2-second timeout**. On any failure, fall back to the last successful commands (or `CLOSED` for all if none yet) and tag `model_id="stub-v0+fallback"`.
4. **Resolve overrides**: HAND_OPEN/HAND_CLOSED win over the ML command; AUTO defers to ML.
5. **Apply + solve**: `sim.apply_pump_commands(final_commands)` then `sim.step()`. On `StopIteration` the runner stops. Other exceptions retry up to **3 hydraulic failures** before stopping.
6. **Persist**: insert one batch each into `live_node_results`, `live_link_results`, `control_decisions`. Supabase failures are logged and dropped — they do not stop the loop.
7. **Cache + broadcast**: store `_cached_state` (served by `/sim/state`) and push it to every SSE subscriber via `asyncio.Queue`. Slow consumers are dropped non-blocking.

### Pump overrides

`PumpMode = AUTO | HAND_OPEN | HAND_CLOSED`. `set_override(pump_id, mode)` can be called any time, including in `NOT_STARTED` — modes are kept in memory and take effect on the next tick. Modes default to `AUTO` for every pump in the network on `start`.

### Schema split

- `app/simulation/types.py` — plain dataclasses (`StepState`, `StepResult`) + enums. Used *below* the HTTP boundary, between `EPANETSimulator` and `SimulationRunner`.
- `app/api/schemas.py` — Pydantic models for HTTP I/O (`SimState`, `SimStart/StopRequest/Response`, `OverrideRequest/Response`, `PredictRequest/Response`, `NetworkInfo`, `TankInfo`).

Keep these layers separate — the runner must not depend on Pydantic, and the routers must not return dataclasses.

---

## Simulator (`app/simulation/Simulator.py`)

Context manager around `epyt.epanet`. On construction, downloads `WSN1.inp` from the Supabase `network` bucket into a temp directory.

| Method | Description |
|---|---|
| `load()` | Open the network, extract category-1 base demands. Returns self (also called by `__enter__`). |
| `seed(days=4)` | Run a full EPS at **1-hour** steps using `getComputedHydraulicTimeSeries`, insert into `seed_*` tables in 500-row chunks. Returns `run_id`. Used for offline data generation, **not** by the web app. |
| `start_simulation()` | Configure 15-min steps, set duration to 365 days, open + initialize hydraulic analysis. Returns `sim_id`. |
| `read_state()` | Snapshot tank levels (`head − elevation`) and pump *commanded* statuses (via `getLinkStatus`, not `getLinkPumpState`). No I/O. |
| `apply_pump_commands(dict)` | `setLinkStatus` for each pump set to `OPEN`/`CLOSED`. `NOP` (or missing) is skipped, leaving the .inp `[RULES]` in charge for that pump. |
| `step()` | `runHydraulicAnalysis` → `nextHydraulicAnalysisStep`. Returns full `StepResult`. Raises `StopIteration` if EPANET reports `tstep ≤ 0`. |
| `rotate_demand_pattern()` | Install a fresh lognormal 96-step pattern (`σ=0.10`) named `DMA_AUTO` and re-randomize base demands (`σ=0.15`, zero-bias). Called on day boundaries. |
| `stop_simulation()` | Close hydraulic analysis. Safe no-op if not started. |
| `compute_network_info()` | Static topology stats → dict matching `NetworkInfo` schema. Safe to call before `start_simulation()`. **Assumes US units (ft / GPM)** — true for WSN1; non-US `.inp` files would need a `getFlowUnits()` guard. |
| `render_plot_svg()` | Render the network with `epyt.plot()` → matplotlib SVG. Returns `(svg_str, geometry)` where `geometry` includes `svg_width/height`, per-node `{id, type, x, y}` (post Y-flip, in SVG points at dpi=72), and per-link `{id, type, from, to}` for client-side hit-testing. |
| `close()` | Unload epyt, delete tempdir. |

---

## SupabaseDB (`app/db/SupabaseClient.py`)

Singleton; credentials from `.env`. All DB and storage I/O goes through this class — never call `supabase.create_client` elsewhere.

**Storage:** `network` bucket holds `WSN1.inp`. `download_network()` / `save_network(bytes)`.

**Tables:**

| Table | Key columns |
|---|---|
| `seed_node_results` | `run_id`, `sim_hour`, `node_id`, `pressure_psi`, `head_ft`, `demand_gpm` |
| `seed_link_results` | `run_id`, `sim_hour`, `link_id`, `flow_gpm`, `velocity_fps`, `headloss_ft_per_kft` |
| `live_node_results` | same schema as `seed_node_results` |
| `live_link_results` | same schema as `seed_link_results` |
| `control_decisions` | `sim_id`, `sim_time_hr`, `pump_id`, `ml_commanded`, `applied_status`, `mode`, `model_id`, `current_price`, `explanation` (XAI audit trail) |

**Methods:** `insert_seed_node_results`, `insert_seed_link_results`, `insert_live_node_results`, `insert_live_link_results`, `insert_control_decision`, `clear_live_tables` (debug-only — wipes `live_*` and `control_decisions`).

Use the MCP Supabase connection in Claude Code for schema inspection, migrations, and SQL queries during development.

---

## HTTP API

### Simulation control (`/sim/*`)

| Endpoint | Notes |
|---|---|
| `POST /sim/start` | Body: `SimStartRequest(time_scale)`. 409 if already running. Returns `sim_id`. |
| `POST /sim/stop` | 409 if not running. Returns `last_sim_hr`. |
| `POST /sim/reset` | Stop (if running) then start. Returns new `sim_id`. |
| `GET /sim/state` | Last cached snapshot (no lock — readers don't block ticks). |
| `GET /sim/stream` | SSE. Initial snapshot, then one event per tick. Heartbeat `: ping` every 15s. |
| `POST /sim/override` | `{pump_id, mode}`. Effective next tick. Allowed in any state. |
| `GET /sim/overrides` | Current pump_modes map. |

### ML (`/ml/*`)

`POST /ml/predict` is a **stub**: returns `NOP` for every pump, deferring fully to the `[RULES]` defined in the .inp. `model_id="stub-v0"`. To be replaced after the ML brainstorm.

Command vocabulary: `"OPEN"` / `"CLOSED"` apply `setLinkStatus` and force the pump (overriding the network's rules for that step); `"NOP"` means "no opinion" — the runner skips `setLinkStatus` and the .inp `[RULES]` govern. `HAND_OPEN`/`HAND_CLOSED` overrides always emit OPEN/CLOSED (rules can't push back against the human).

### Network (`/network/*`)

`GET /network/info` → `NetworkInfo`. `GET /network/plot.svg` → cached SVG with `image/svg+xml`.

### Pages

`GET /` (overview), `GET /pump-control`, `GET /xai`. All Jinja2. `GET /healthz` returns `{"status": "ok"}`.

---

## Frontend

Three nav sections rendered via Jinja2; only the overview page is currently wired up beyond the nav shell.

### Overview (implemented)

- Static network SVG rendered server-side via `epyt.plot()`, served from `/network/plot.svg`, embedded in the page.
- `overview.js` opens an `EventSource` on `/sim/stream`. On each event: update tank fullness bars, pump status pills, pressure overlays, and the start/stop button. SVG geometry from `app.state.network_geometry` is passed to the client as JSON for hit-testing nodes/links without re-parsing the SVG.
- Static topology stats panel (junctions, pipes, total demand, etc.) hydrated from `/network/info`.

### Pump Control (placeholder)

- AI decision dashboard, SHAP breakdown, decision history. Currently a navigation shell only.

### XAI / Prediction (placeholder)

- ML ensemble predictions, per-node/link time-series, model performance metrics. Currently a navigation shell only.

---

## Offline ML pipeline (`app/data/EPANET Analysis.ipynb`)

Separate from the live web loop. Uses pre-parsed `WSN1 - report.txt`:

```
WSN1 - report.txt
    │
    ▼ regex parser
JunctionPressures (~743k rows: node × timestep)
LinkResults (~1M rows)
    │
    ▼ feature engineering
- Merge PATTERN-0 demand multipliers (0.5-hr steps, 96-hr window)
- Lag features: [1, 2, 3, 6, 24] for Demand, Head, Pressure
- Derived: Demand_to_Elev_ratio
    │
    ▼ temporal split: hours ≤ 35 train, > 35 test (first 24 hrs dropped per node)
    │
    ▼ sklearn Pipeline: OneHotEncoder('Node') + MultiOutputRegressor
Targets: Pressure [psi], Demand, Head
Models:  XGBoost | MLP | GAM | EBM
    │
    ▼ evaluation: RMSE, R², MSE (overall + per-node)
    ▼ explainability: SHAP + permutation importance (pressure target)
```

> ⚠ Known bug in the notebook: `JunctionPressures` uses column `'Node-ID'` but the merge calls `on='Node'`. Rename the column or update the merge key before `pd.merge`.

---

## EPANET Network Details

- Live mode: 15-min timestep, 1-year duration (continuous; restart if exceeded).
- Seed mode: 1-hour timestep, N-day duration.
- Flow units: GPM. Headloss formula: Hazen-Williams.
- Report fields: STATUS, SUMMARY, NODES ALL, LINKS ALL, PRESSURE, HEAD, DEMAND, FLOW, VELOCITY, HEADLOSS.
- Total system demand: ~1.3 MGD (~4.9 million L/day).
