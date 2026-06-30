"""
Pydantic schemas for HTTP request/response bodies.

These cross the network boundary, so they're Pydantic (not dataclasses).
Plain-Python types used inside the simulator live in simulation/types.py.
"""
from datetime import datetime
from pydantic import BaseModel, Field

from app.simulation.types import PumpMode, SimStatus


class SimStartRequest(BaseModel):
    time_scale: int = Field(default=1, ge=1, le=100_000,
                            description="900/time_scale = wall-sec per step. 1=real-time, 60=demo, 10000=fast.")


class SimStartResponse(BaseModel):
    status:      SimStatus
    sim_id:      str
    time_scale:  int
    started_at:  datetime


class SimStopResponse(BaseModel):
    status:       SimStatus
    stopped_at:   datetime
    last_sim_hr:  float


class SimState(BaseModel):
    status:        SimStatus
    sim_id:        str | None = None
    sim_time_hr:   float       = 0.0
    wall_time:     datetime
    time_scale:    int         = 1
    tank_levels:   dict[str, float] = {}
    pump_states:   dict[str, str]   = {}
    pump_modes:    dict[str, PumpMode] = {}
    pressures:     dict[str, float] = Field(default_factory=dict)
    flows:         dict[str, float] = Field(default_factory=dict)
    current_price: float | None = None
    pump_powers_kw:       dict[str, float] = Field(default_factory=dict)
    pump_step_energy_kwh: dict[str, float] = Field(default_factory=dict)
    pump_step_cost_eur:   dict[str, float | None] = Field(default_factory=dict)
    total_power_kw:       float = 0.0
    step_energy_kwh:      float = 0.0
    step_cost_eur:        float | None = None
    last_step_at:  datetime | None = None
    pattern_id:    str | None = None


class PatternResponse(BaseModel):
    pattern_id:    str | None
    multipliers:   list[float]
    step_minutes:  int


class OverrideRequest(BaseModel):
    pump_id: str
    mode:    PumpMode


class OverrideResponse(BaseModel):
    pump_modes: dict[str, PumpMode]


class PredictRequest(BaseModel):
    sim_time_hr:   float
    tank_levels:   dict[str, float]
    pump_modes:    dict[str, PumpMode]
    current_price: float | None = None
    # NOTE: Additional features (recent pressures, demand forecast, time-of-day
    # encoding, etc.) will be added during the ML brainstorm. This schema is
    # the minimum that the stub controller needs today.


class PredictResponse(BaseModel):
    commands:    dict[str, str]            # pump_id -> "OPEN"|"CLOSED"|"NOP"
    model_id:    str
    explanation: dict | None = None


class TankInfo(BaseModel):
    min_level_ft:  float
    max_level_ft:  float
    diameter_ft:   float


class NetworkInfo(BaseModel):
    junction_count:        int
    tank_count:            int
    reservoir_count:       int
    pump_count:            int
    valve_count:           int
    pipe_count:            int
    total_pipe_length_mi:  float
    total_demand_gpm:      float
    total_demand_mgd:      float
    pattern_steps:         int
    pattern_period_min:    int
    tanks:                 dict[str, TankInfo] = {}


class PredictionStatus(BaseModel):
    state:        str
    model_id:     str | None = None
    n_train_rows: int = 0
    trained_at:   datetime | None = None
    error:        str | None = None


class NodeListResponse(BaseModel):
    nodes: list[str]


class SeriesPoint(BaseModel):
    hr: float
    p:  float


class NodeRegions(BaseModel):
    seed_end:     float
    live_end:     float
    forecast_end: float


class NodeMetrics(BaseModel):
    live_rmse: float | None = None
    live_r2:   float | None = None


class NodePrediction(BaseModel):
    node_id:  str
    seed:     list[SeriesPoint]
    live:     list[SeriesPoint]
    overlay:  list[SeriesPoint]
    forecast: list[SeriesPoint]
    regions:  NodeRegions
    metrics:  NodeMetrics
    note:     str | None = None


class ShapFeature(BaseModel):
    name:          str
    mean_abs_shap: float


class ShapResponse(BaseModel):
    node_id:  str
    features: list[ShapFeature]
    top_n:    int = 15
