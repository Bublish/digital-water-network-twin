"""
Shared plain-Python types for the simulation layer.

These are NOT Pydantic models — they live below the HTTP boundary and are
passed between EPANETSimulator and SimulationRunner. HTTP-facing types
(SimState, OverrideRequest, PredictRequest/Response) live in api/schemas.py.
"""
from dataclasses import dataclass, field
from enum import Enum


class SimStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING     = "RUNNING"
    STOPPED     = "STOPPED"


class PumpMode(str, Enum):
    AUTO        = "AUTO"
    HAND_OPEN   = "HAND_OPEN"
    HAND_CLOSED = "HAND_CLOSED"


@dataclass
class StepState:
    """Snapshot read from EPANET before a step is computed."""
    sim_time_sec: float
    tank_levels:  dict[str, float] = field(default_factory=dict)   # tank_id -> ft
    pump_states:  dict[str, str]   = field(default_factory=dict)   # pump_id -> "OPEN"|"CLOSED"


@dataclass
class StepResult:
    """Full result returned by EPANETSimulator.step()."""
    sim_time_sec:  float
    pressures:     dict[str, float] = field(default_factory=dict)
    heads:         dict[str, float] = field(default_factory=dict)
    demands:       dict[str, float] = field(default_factory=dict)
    flows:         dict[str, float] = field(default_factory=dict)
    velocities:    dict[str, float] = field(default_factory=dict)
    headlosses:    dict[str, float] = field(default_factory=dict)
    pump_states:   dict[str, str]   = field(default_factory=dict)
    tank_levels:   dict[str, float] = field(default_factory=dict)
    pump_powers_kw: dict[str, float] = field(default_factory=dict)  # pump_id -> avg kW over last step
