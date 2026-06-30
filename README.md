# WSN1 SCADA — Water Distribution Monitoring & Prediction

A SCADA-style web application for monitoring and managing a water distribution
network in real time. The backend runs a continuous **EPANET** hydraulic
simulation, applies pump-control decisions each timestep, and streams live state
to the browser. Alongside the live loop, a machine-learning **Prediction Engine**
forecasts per-node pressure and explains its forecasts, and a **Pricing Engine**
ties pump energy use to real electricity spot prices.

The network under study is **WSN1** — a transmission-scale, dense-loop benchmark
network from the *Battle of the Water Networks* (Ostfeld, 2021): 126 junctions,
1 reservoir, 2 pump stations, 2 storage tanks, 8 valves, and 168 pipes
(~23.3 miles), carrying a total system demand of roughly 1.36 MGD.

> Research prototype developed at TU Darmstadt. Some components (notably the
> pump-control model) are still stubs; see [Project Status](#project-status).

---

## Table of Contents

- [What It Does](#what-it-does)
- [Screenshots](#screenshots)
- [Features](#features)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Repository Layout](#repository-layout)
- [Database Schema](#database-schema)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [HTTP API](#http-api)
- [Testing](#testing)
- [Offline ML Pipeline](#offline-ml-pipeline)
- [Project Status](#project-status)

---

## What It Does

The application turns a static EPANET network model into a live, observable
system. Instead of running a single hydraulic report offline, it steps the
network forward in simulated time, makes a control decision at each step, solves
the hydraulics, and publishes the result to every connected browser. Three
concerns run in parallel:

1. **Simulation & control** — a 15-minute-step hydraulic loop that reads tank and
   pump state, asks a control service for pump commands, applies them, solves the
   network, and persists the results.
2. **Prediction & explainability** — a trained model that forecasts node pressure
   forward in time and reports, per node, which features drove the forecast (SHAP).
3. **Cost** — real day-ahead electricity spot prices mapped onto simulated time,
   so each pump step carries an energy (kWh) and cost (EUR) figure.

Everything is presented through a server-rendered web UI that updates live over
Server-Sent Events — no page reloads, no client framework.

---

## Screenshots

### Overview

Live network diagram, simulation control, EPEX electricity price, demand pattern,
and tank/pump status — all updating over SSE.

![Overview dashboard](assets/overview.png)

Per-step energy and cost, alongside static network statistics:

![Energy and network info](assets/energy-network-info.png)

### Prediction Engine

Per-node pressure (seed / live / forecast) with the model-fit overlay and SHAP
feature attributions:

![Prediction Engine](assets/prediction-engine.png)

---

## Features

### Live hydraulic simulation
- Continuous EPANET simulation stepped at **15-minute** intervals over a 1-year
  horizon, wrapped behind an async state machine (`NOT_STARTED → RUNNING → STOPPED`).
- Adjustable **time scale**: `1` = real-time, `60` = demo speed, `10000` = fast —
  controls how many wall-clock seconds elapse per simulated step.
- **Daily demand variation**: a fresh lognormal 96-step demand pattern and
  re-randomized per-node base demands are installed at each simulated day boundary,
  so no two days are identical.
- Resilient tick loop: ML timeouts fall back to the last good command, hydraulic
  solver failures retry before stopping, and database write failures are logged
  and dropped rather than halting the simulation.

### Pump control & operator overrides
- Each step, the control service returns a command per pump
  (`OPEN` / `CLOSED` / `NOP`). `NOP` defers to the network's own `[RULES]`.
- **Manual overrides** per pump: `AUTO` (follow the model), `HAND_OPEN`, or
  `HAND_CLOSED`. Hand modes always win over the model and take effect on the next
  tick — settable even before the simulation starts.
- Every decision is written to an audit trail (`control_decisions`): what the
  model commanded, what was actually applied, the operative mode, the model ID,
  and the electricity price at that step.

### Prediction Engine (ML)
- **XGBoost** multi-output regressor predicting **pressure, demand, and head** per
  junction, re-implementing the offline notebook pipeline as a live service.
- Trains itself in the background at startup: if no 15-minute seed data exists, it
  seeds a multi-day EPANET run (in an isolated subprocess), then fits the model.
- Per-node chart data stitched from **seed history + live run**, plus a recursive
  **multi-step forecast** continuing past the live data.
- **SHAP** feature attributions per node, ranking which lagged demand/head/pressure
  features and static attributes drove the prediction.
- Live accuracy metrics (RMSE, R²) computed by overlaying model predictions on the
  actual live run.

### Electricity pricing
- Fetches **DE-LU day-ahead spot prices** (15-minute resolution) from the
  [Energy-Charts API](https://www.energy-charts.info/) and maps them onto simulated
  time, anchored to wall-clock UTC at simulation start.
- Per-step and total **energy (kWh)** and **cost (EUR)** for pump operation, derived
  from EPANET pump power.
- Graceful fallback to a configurable flat rate when pricing is disabled, the API is
  unavailable, or simulated time outruns published prices.

### Live web UI
- **Overview**: server-rendered network diagram (EPANET → SVG) with live tank
  fullness, pump status, pressure overlays, and topology statistics. Pan/zoom and
  client-side node/link hit-testing via a JSON geometry sidecar.
- **Prediction Engine**: per-node pressure chart (seed / live / forecast bands via
  uPlot) with SHAP feature bars and model status.
- **Light/dark theme** with no flash-of-unstyled-content; the network diagram is
  pre-rendered for both themes.
- All live data arrives over a single SSE stream; the UI uses vanilla JS only.

---

## Architecture

```
                         Browser (vanilla JS, EventSource)
                                     │  SSE / fetch
                                     ▼
┌────────────────────────────────────────────────────────────────────┐
│                         FastAPI application                          │
│  (singletons wired onto app.state in the lifespan context manager)   │
│                                                                      │
│  Routers:  /sim   /ml   /network   /pricing   /prediction   /  pages │
│                                                                      │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────┐  │
│  │ SimulationRunner   │   │ PredictionService │   │ PricingEngine  │  │
│  │ (async tick loop,  │   │ (seed → train →   │   │ (EPEX spot     │  │
│  │  state machine,    │   │  forecast + SHAP) │   │  price cache)  │  │
│  │  SSE broadcast)    │   └───────┬──────────┘   └───────┬────────┘  │
│  └─────────┬──────────┘           │                      │           │
│            │                      │                      │ httpx     │
│   ┌────────▼─────────┐   ┌────────▼────────┐             ▼           │
│   │ EPANETSimulator   │   │ PressurePredictor│     Energy-Charts API  │
│   │ (epyt → EPANET C) │   │ (XGBoost + SHAP) │                        │
│   └────────┬─────────┘   └─────────────────┘                         │
│            │                                                          │
│            │  per-step ML call (httpx → /ml/predict)                  │
│            ▼                                                          │
│      /ml/predict  (control model — currently a stub)                 │
└────────────┬─────────────────────────────────────────────────────────┘
             │  persist results / read history
             ▼
        Supabase (Postgres + Storage: WSN1.inp, live_/seed_ tables)
```

The control loop, the prediction service, and the pricing engine are independent
singletons. The simulation persists every step to Supabase; the prediction engine
reads that same history back to build its charts. The control model is reached over
HTTP (`/ml/predict`), so it can later be swapped for an external service without
touching the runner.

---

## How It Works

### Startup (lifespan)

On FastAPI startup, the app constructs and wires its singletons onto `app.state`:

- **`EPANETSimulator`** — downloads `WSN1.inp` from Supabase storage into a
  tempdir, opens the network, and extracts base demands. It does **not** start the
  hydraulic loop yet.
- **`PricingEngine`**, **`SimulationRunner`**, and **`PredictionService`**.
- Network topology stats and the network plot (rendered to SVG for both light and
  dark themes) are computed once and cached.
- A **background training task** is launched for the Prediction Engine.

The runner sits in `NOT_STARTED` until a client `POST`s `/sim/start`.

### The tick loop

The `SimulationRunner` advances the network one 15-minute step at a time. Wall-clock
pacing is `900 / time_scale` seconds per step. Each tick, under a lock:

1. **Day boundary** — every 96 steps, install a fresh lognormal demand pattern and
   re-randomize base demands.
2. **Read state** — snapshot tank levels and commanded pump statuses.
3. **Resolve price** — map the current simulated datetime onto the EPEX price cache.
4. **Call the control model** — `POST /ml/predict` with tank levels, pump modes, and
   the current price (2-second timeout). On any failure, fall back to the last good
   commands.
5. **Resolve overrides** — `HAND_OPEN`/`HAND_CLOSED` win; `AUTO` defers to the model;
   an omitted pump becomes `NOP` so the `.inp` rules stay in charge.
6. **Apply & solve** — set pump statuses, then run one EPANET hydraulic step.
   `StopIteration` (end of duration) stops the loop cleanly; other failures retry up
   to three times.
7. **Persist** — batch-insert node results, link results, and the control-decision
   audit row.
8. **Cache & broadcast** — build the public state snapshot (including per-pump power,
   energy, and cost) and push it to every SSE subscriber. Slow consumers are dropped
   without blocking the loop.

`GET /sim/state` reads the cached snapshot without taking the lock, so live readers
never block ticks.

### The Prediction Engine

Separate from the control loop and **read-only** with respect to the simulation. On
startup it walks `NOT_TRAINED → SEEDING → TRAINING → READY`:

- If the 15-minute seed table is empty or at the wrong resolution, it runs a
  multi-day EPANET seed in a **spawned subprocess** (EPANET keeps a single global
  project per process, so seeding in-process would clobber the live simulation).
- It builds a lag-feature frame (lags `[1, 2, 3, 6, 24]` over demand/head/pressure,
  plus static elevation, base demand, and a demand-to-elevation ratio) and fits the
  XGBoost model.

At inference time, for a requested node it:
- stitches **seed + live** history into one series,
- overlays model predictions on the live segment (and reports RMSE / R²),
- recursively **forecasts** pressure forward over the horizon, and
- computes **SHAP** attributions ranking the most influential features.

Heavy per-node work runs in a worker thread so the event loop stays responsive.

### Pricing

`PricingEngine` pins simulated time-zero to wall-clock UTC at `/sim/start`. Each
tick it refreshes a 15-minute-resolution price cache from Energy-Charts (rate-limited,
with back-off when simulated time runs past published prices) and returns the price
for the current slot. Pump power from EPANET is converted to per-step kWh and
multiplied by the slot price to give per-step and cumulative cost.

---

## Repository Layout

```
app/
  api/
    main.py              # FastAPI app factory + lifespan (wires singletons)
    sim_routes.py        # /sim/{start,stop,reset,state,stream,override,overrides,pattern}
    ml_routes.py         # POST /ml/predict (control model — stub)
    network_routes.py    # GET /network/{info,plot.svg}
    pricing_routes.py    # GET /pricing/{current,forecast}
    prediction_routes.py # GET /prediction/{status,nodes,node/{id},node/{id}/shap}
    web_routes.py        # GET / (overview), /prediction (pages)
    schemas.py           # Pydantic HTTP-boundary models
  db/
    SupabaseClient.py    # SupabaseDB singleton — all storage + DB I/O
  ml/
    PredictionService.py # Orchestrator: seed → train → forecast/SHAP
    PressurePredictor.py # XGBoost multi-output regressor + SHAP (pure ML)
    FeatureBuilder.py    # Canonical lag/feature frame construction
  pricing/
    PricingEngine.py     # EPEX spot-price fetch + cache
  scheduler/
    SimulationRunner.py  # Async tick-loop orchestrator (state machine, SSE)
  simulation/
    Simulator.py         # EPANETSimulator — context manager wrapping epyt
    Pattern.py           # Lognormal 96-step demand multipliers
    Randomizer.py        # Lognormal noise for demands/patterns
    seed_worker.py       # Subprocess entrypoint for isolated seeding
    types.py             # Internal dataclasses + enums
  data/
    WSN1.inp             # EPANET network (canonical copy in Supabase storage)
    WSN1.rpt / *.txt     # EPANET reports / pre-parsed results for the notebook
    DMA4_demand.pat      # Demand pattern file
    EPANET Analysis.ipynb# Offline ML pipeline (notebook)
  tests/                 # pytest suite (conftest puts repo root on sys.path)
  web/
    templates/           # base.html, overview.html, prediction.html (Jinja2)
    static/              # css/, js/, vendor/ (uPlot, panzoom)
requirements.txt
.env                     # SUPABASE_URL, SUPABASE_KEY, pricing config
```

All Python modules use absolute `app.*` imports; run tooling from the repo root.

---

## Database Schema

All persistence goes through the `SupabaseDB` singleton — no other module talks to
Supabase directly. The `network` storage bucket holds the canonical `WSN1.inp`.
Five Postgres tables back the simulation and the prediction features.

**`seed_node_results`** / **`live_node_results`** — per-node hydraulic results.
Identical schema; `seed_*` holds the offline training run, `live_*` holds the
running simulation.

| Column | Type | Notes |
|---|---|---|
| `id` | `int8` | primary key |
| `run_id` | `text` | seed run / live `sim_id` |
| `sim_hour` | `float8` | simulated hours since run start |
| `node_id` | `text` | junction ID |
| `pressure_psi` | `float8` | nodal pressure |
| `head_ft` | `float8` | hydraulic head |
| `demand_gpm` | `float8` | nodal demand |

**`seed_link_results`** / **`live_link_results`** — per-link hydraulic results,
same seed/live split.

| Column | Type | Notes |
|---|---|---|
| `id` | `int8` | primary key |
| `run_id` | `text` | seed run / live `sim_id` |
| `sim_hour` | `float8` | simulated hours since run start |
| `link_id` | `text` | pipe / pump / valve ID |
| `flow_gpm` | `float8` | link flow |
| `velocity_fps` | `float8` | flow velocity |
| `headloss_ft_per_kft` | `float8` | unit headloss |

**`control_decisions`** — pump-control audit trail (one row per pump per step).

| Column | Type | Notes |
|---|---|---|
| `id` | `int8` | primary key |
| `sim_id` | `uuid` | simulation run |
| `sim_time_hr` | `float8` | simulated hours |
| `wall_time` | `timestamptz` | wall-clock time of the step |
| `pump_id` | `text` | pump being controlled |
| `ml_commanded` | `text` | what the control model returned (`OPEN`/`CLOSED`/`NOP`) |
| `applied_status` | `text` | status actually applied after overrides |
| `mode` | `text` | `AUTO` / `HAND_OPEN` / `HAND_CLOSED` |
| `model_id` | `text` | controller version (e.g. `stub-v0`) |
| `current_price` | `float8` | EUR/kWh at the step |
| `explanation` | `jsonb` | reserved for XAI payloads |
| `created_at` | `timestamptz` | row insertion time |

> The `seed_*` tables feed the Prediction Engine's training; the `live_*` tables
> capture the running simulation and feed the live chart overlays. On shutdown the
> `live_*` and `control_decisions` tables are cleared (debug convenience — see
> [Project Status](#project-status)).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (lifespan-managed singletons) |
| Templating | Jinja2 (server-side rendered HTML) |
| Frontend | Vanilla HTML/CSS/JS, `EventSource` for live updates, uPlot for charts |
| Database & storage | Supabase (Postgres + object storage) |
| Hydraulic engine | `epyt` (wraps the EPANET C engine) |
| Network visualisation | `epyt.plot()` → matplotlib SVG + JSON geometry sidecar |
| ML | XGBoost (multi-output), scikit-learn, SHAP |
| External data | Energy-Charts API (EPEX DE-LU day-ahead prices, CC BY 4.0) |

---

## Getting Started

### Prerequisites

- Python 3.11+
- A Supabase project (URL + API key) with:
  - a `network` storage bucket containing **`WSN1.inp`**, and
  - the `seed_*`, `live_*`, and `control_decisions` tables (see
    [Database Schema](#database-schema)).

### Install

```bash
pip install -r requirements.txt
```

### Supabase setup (required before first run)

Upload `app/data/WSN1.inp` to the `network` storage bucket. `EPANETSimulator`
downloads it on construction and raises `RuntimeError` if it is missing.

### Run

```bash
# From the repo root
uvicorn app.api.main:app --reload
```

Then open <http://localhost:8000/>. Health check: `GET /healthz`.

A VS Code launch config (`.vscode/launch.json`) runs the same command from the
workspace root.

---

## Configuration

Configuration is read from environment variables (a `.env` file is loaded
automatically):

| Variable | Purpose | Default |
|---|---|---|
| `SUPABASE_URL` | Supabase project URL | — (required) |
| `SUPABASE_KEY` | Supabase API key | — (required) |
| `PRICING_ENABLED` | Enable EPEX price fetching; `false` forces the flat rate | `true` |
| `FLAT_RATE_EUR_PER_KWH` | Fallback price when pricing is disabled/unavailable | `0.08` |

---

## HTTP API

### Simulation control — `/sim/*`
| Endpoint | Notes |
|---|---|
| `POST /sim/start` | Body `{time_scale}`. `409` if already running. Returns `sim_id`. |
| `POST /sim/stop` | `409` if not running. Returns `last_sim_hr`. |
| `POST /sim/reset` | Stop (if running) then start. Returns a new `sim_id`. |
| `GET /sim/state` | Last cached snapshot (no lock). |
| `GET /sim/stream` | **SSE**: initial snapshot, then one event per tick; `: ping` heartbeat every 15 s. |
| `POST /sim/override` | `{pump_id, mode}`. Effective next tick; allowed in any state. |
| `GET /sim/overrides` | Current pump-mode map. |
| `GET /sim/pattern` | Current 96-step demand multipliers. |

### Control model — `/ml/*`
| Endpoint | Notes |
|---|---|
| `POST /ml/predict` | Returns a command per pump. **Currently a stub** — emits `NOP` for every pump, deferring to the `.inp` `[RULES]`. |

### Network — `/network/*`
| Endpoint | Notes |
|---|---|
| `GET /network/info` | Static topology statistics. |
| `GET /network/plot.svg` | Cached network diagram (`image/svg+xml`). |

### Pricing — `/pricing/*`
| Endpoint | Notes |
|---|---|
| `GET /pricing/current` | Price (EUR/kWh and EUR/MWh) for the current simulated slot. |
| `GET /pricing/forecast` | Cached price schedule (sorted slots). |

### Prediction Engine — `/prediction/*`
| Endpoint | Notes |
|---|---|
| `GET /prediction/status` | Training state, model ID, row count, timestamp. |
| `GET /prediction/nodes` | List of predictable node IDs. |
| `GET /prediction/node/{id}` | Seed / live / overlay / forecast series + live metrics. `503` until ready. |
| `GET /prediction/node/{id}/shap` | Top-N SHAP feature attributions for the node. |

### Pages
`GET /` (Overview), `GET /prediction` (Prediction Engine), `GET /healthz`.

---

## Testing

```bash
# Full suite (run from the repo root)
pytest app/tests/

# A single test
pytest app/tests/test_simulation_runner.py::test_name
```

`app/tests/conftest.py` adds the repo root to `sys.path`. The suite covers the
runner, simulator, schemas, ML services, pricing, and API integration.

---

## Offline ML Pipeline

`app/data/EPANET Analysis.ipynb` is the research notebook the live Prediction
Engine is derived from. It parses a pre-computed EPANET report into per-node and
per-link time series, engineers lag and ratio features, applies a temporal
train/test split, and compares XGBoost, MLP, GAM, and EBM models on pressure,
demand, and head — with SHAP and permutation-importance explainability. The live
`PressurePredictor` re-implements the XGBoost branch of this pipeline as a service.

---

## Project Status

This is a research prototype, and parts of the system are intentionally incomplete:

- **Pump-control model** (`/ml/predict`) is a **stub** that emits `NOP`, leaving the
  EPANET `[RULES]` in control. The HTTP boundary exists so a trained controller can
  be dropped in later.
- The shutdown path clears the `live_*` and `control_decisions` tables — this is a
  **debug convenience** and is marked for removal before production.

The live **Prediction Engine** and **Pricing Engine** are functional.

---

## Acknowledgements

- Network model: WSN1 from the *Battle of the Water Networks* (A. Ostfeld et al., 2021).
- Electricity prices: [Energy-Charts](https://www.energy-charts.info/) (EPEX SPOT
  day-ahead, DE-LU bidding zone), licensed CC BY 4.0.
