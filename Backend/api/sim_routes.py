"""
/sim/* HTTP endpoints. Thin layer over SimulationRunner.
"""
from datetime import UTC, datetime
from fastapi import APIRouter, HTTPException, Request

from api.schemas import (
    OverrideRequest, OverrideResponse,
    SimStartRequest, SimStartResponse, SimStopResponse, SimState,
)
from simulation.types import SimStatus

router = APIRouter(prefix="/sim")


def _runner(request: Request):
    return request.app.state.runner


def _now() -> datetime:
    return datetime.now(UTC)


@router.post("/start", response_model=SimStartResponse)
async def start(req: SimStartRequest, request: Request) -> SimStartResponse:
    runner = _runner(request)
    if runner.status == SimStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Simulation already running.")
    sim_id = await runner.start(time_scale=req.time_scale)
    return SimStartResponse(
        status=runner.status, sim_id=sim_id,
        time_scale=req.time_scale, started_at=_now(),
    )


@router.post("/stop", response_model=SimStopResponse)
async def stop(request: Request) -> SimStopResponse:
    runner = _runner(request)
    if runner.status != SimStatus.RUNNING:
        raise HTTPException(status_code=409, detail="Simulation is not running.")
    await runner.stop()
    return SimStopResponse(
        status=runner.status, stopped_at=_now(),
        last_sim_hr=runner.last_sim_hr,
    )


@router.get("/state", response_model=SimState)
async def get_state(request: Request) -> SimState:
    runner = _runner(request)
    cached = runner.get_cached_state()
    # Coerce ISO strings back to datetimes for Pydantic
    if isinstance(cached.get("wall_time"), str):
        cached["wall_time"] = datetime.fromisoformat(cached["wall_time"])
    if isinstance(cached.get("last_step_at"), str):
        cached["last_step_at"] = datetime.fromisoformat(cached["last_step_at"])
    return SimState(**cached)


@router.post("/override", response_model=OverrideResponse)
async def set_override(req: OverrideRequest, request: Request) -> OverrideResponse:
    runner = _runner(request)
    new_modes = runner.set_override(req.pump_id, req.mode)
    return OverrideResponse(pump_modes=new_modes)


@router.get("/overrides", response_model=OverrideResponse)
async def get_overrides(request: Request) -> OverrideResponse:
    runner = _runner(request)
    return OverrideResponse(pump_modes=runner.pump_modes)
