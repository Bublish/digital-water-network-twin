# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **SCADA web application** for monitoring and managing water distribution networks. The primary feature is an AI-based pump control system with explainable AI (XAI) analysis of pump scheduling decisions. The network under study is **WSN1** (Water Sensor Network 1) — a hypothetical transmission dense-loop network from the Battle of the Networks benchmark (Ostfeld, 2021) with ~130 junctions, 1 reservoir, 2 pump stations, 2 tanks, and 23.3 miles of pipe.

The app allows operators to import EPANET network files, view live network state, inspect AI pump control decisions with XAI breakdowns, and run ML ensemble pressure/demand predictions.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI |
| Templating | Jinja2 (server-side rendered HTML) |
| Frontend | HTML + CSS (no JS framework) |
| Database & storage | Supabase (project: **EPANETSIM**) |
| EPANET engine | epyt (wraps the EPANET C engine) |
| Network visualisation | `epyt` `.plot()` method, served as inline SVG/PNG |
| ML models | XGBoost, MLP (sklearn), GAM (pygam), EBM (interpret) |
| Explainability | SHAP, permutation importance |

---

## Repository Layout

```
Backend/
  api/                  # FastAPI route handlers (not yet implemented)
  data pipeline/        # Input data, raw EPANET output, and the main analysis notebook
    WSN1.inp            # EPANET network definition (junctions, pipes, patterns, controls)
    WSN1.rpt            # Full EPANET hydraulic report (raw simulation output)
    WSN1 - report.txt   # Parsed node/link results used by the notebook
    DMA4_demand.pat     # Demand pattern file
    EPANET Analysis.ipynb  # Main ML pipeline notebook (predictions, evaluation, explainability)
  db/
    SupabaseClient.py   # Supabase singleton client (all DB I/O goes here)
    __init__.py
  ml/                   # ML model training, inference, and XAI modules (not yet implemented)
  scheduler/            # Pump control scheduling logic (not yet implemented)
  simulation/
    Simulator.py        # EPANETSimulator class (context manager, wraps epyt)
  main.py               # Current debug entry point — runs simulation directly, no FastAPI yet
requirements.txt        # Minimal; most ML/web dependencies must be installed separately
.env                    # SUPABASE_URL and SUPABASE_KEY
```

> ⚠ `Backend/simulation/SimulationResults.py` is referenced in comments but the source file does not exist (only stale `__pycache__`). It needs to be created.

---

## Running the App

```bash
# Install core dependencies
pip install -r requirements.txt

# Install remaining dependencies not yet in requirements.txt
pip install fastapi uvicorn python-multipart jinja2 pandas scikit-learn xgboost pygam interpret shap matplotlib

# Current debug runner (no FastAPI — runs EPANETSimulator directly)
python Backend/main.py

# Future FastAPI server (once Backend/api/main.py is implemented)
uvicorn Backend.api.main:app --reload

# Run the analysis notebook
cd "Backend/data pipeline"
jupyter notebook "EPANET Analysis.ipynb"
```

A VS Code launch config already exists in `.vscode/launch.json` and runs `Backend/main.py`.

---

## EPANETSimulator (`Backend/simulation/Simulator.py`)

Context manager wrapping `epyt`. Key methods:

| Method | Description |
|---|---|
| `load()` | Opens WSN1.inp, extracts base demands, returns self |
| `seed(days=4)` | Runs full EPS for N days, inserts hourly rows to Supabase in 500-row chunks, returns `run_id` (UUID) |
| `run_live()` | Steps EPS in real-time, inserting one snapshot per simulated hour |
| `_randomizeDemand()` | Applies per-node lognormal demand multiplier (σ=0.15, zero bias) |

> ⚠ Known issue: `Simulator.py` around line 104 has `while t<100:` with `_insert_snapshot(...)` commented out — incomplete test code that must be resolved before `run_live()` works correctly.

---

## SupabaseDB (`Backend/db/SupabaseClient.py`)

Singleton pattern; credentials from `.env`. All DB operations must use this class — never instantiate `supabase.create_client` elsewhere.

**Database tables:**

| Table | Key columns |
|---|---|
| `network_state` | `tank_id`, `level_ft`, `updated_at` |
| `seed_node_results` | `run_id`, `sim_hour`, `node_id`, `pressure_psi`, `head_ft`, `demand_gpm` |
| `seed_link_results` | `run_id`, `sim_hour`, `link_id`, `flow_gpm`, `velocity_fps`, `headloss_ft_per_kft` |
| `live_node_results` | same schema as `seed_node_results` |
| `live_link_results` | same schema as `seed_link_results` |

Use the MCP Supabase connection in Claude Code for schema inspection, migrations, and SQL queries during development.

---

## Planned API Endpoints (not yet implemented)

- `POST /upload` — accept `.inp` file, run EPANETSimulator, persist to Supabase
- `GET /pump-control` — run pump control ML model, return decision + SHAP values
- `GET /prediction` — run ensemble forecast, return per-node predictions
- `GET /network-plot` — call `epyt` `.plot()`, return image

---

## Frontend Structure

Three navigational sections rendered via Jinja2 templates:

### 1. Overview
- **Project import**: upload a `.inp` file; parse and store network topology in Supabase.
- **Network visualisation**: render imported network via `epyt` `.plot()` as embedded image.
- **General information panel**: summary statistics (junction count, pipe count, total demand, etc.).

### 2. Pump Control
- **AI decision dashboard**: current pump scheduling decision from the ML pump control model.
- **XAI breakdown**: SHAP waterfall or bar chart showing which features drove the decision.
- **Decision history**: table/timeline of past pump commands with XAI justifications.

### 3. Prediction
- **ML ensemble predictions**: pressure, demand, and head forecasts from XGBoost, MLP, GAM, EBM.
- **Per-node and per-link views**: selectable node/link showing time-series forecast vs. actuals.
- **Model performance metrics**: RMSE, R², MSE per model.

---

## Data Flow Architecture

```
WSN1.inp (EPANET network)
    │
    ├── EPANETSimulator → rows inserted directly to Supabase
    │
    └── WSN1 - report.txt (pre-parsed node/link results)
            │
            ▼
    Regex parser in EPANET Analysis.ipynb
    → JunctionPressures DataFrame (743k rows: node × timestep)
    → LinkResults DataFrame (1M rows: link × timestep)
            │
            ▼
    Feature Engineering
    → Merge PATTERN-0 demand multipliers (0.5-hr steps, 96-hr window)
    → Lag features: lags [1, 2, 3, 6, 24] for Demand, Head, Pressure
    → Derived feature: Demand_to_Elev_ratio
            │
            ▼
    Temporal train/test split: hours ≤ 35 train, hours > 35 test
    (first 24 hrs dropped per node due to lag NaNs)
            │
            ▼
    sklearn Pipeline: OneHotEncoder('Node') + MultiOutputRegressor
    → Targets: Pressure [psi], Demand, Head
    → Models: XGBoost | MLP | GAM | EBM
            │
            ▼
    Evaluation: RMSE, R², MSE (overall + per-node)
    Explainability: SHAP values + permutation importance (pressure target)
```

> ⚠ Known bug in the notebook: `JunctionPressures` uses column `'Node-ID'` but the merge calls `on='Node'`. Fix by renaming the column or updating the merge key before `pd.merge`.

---

## EPANET Network Details

- Simulation pattern: **PATTERN-0**, 96-hour window, 0.5-hr timestep
- Flow units: GPM; headloss formula: Hazen-Williams
- Report fields captured: STATUS, SUMMARY, NODES ALL, LINKS ALL, PRESSURE, HEAD, DEMAND, FLOW, VELOCITY, HEADLOSS
- Total system demand: ~1.3 MGD (~4.9 million L/day)
