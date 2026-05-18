"""
/sim/* HTTP endpoints. Thin layer over SimulationRunner.
"""
import asyncio
import json
from datetime import UTC, datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    OverrideRequest, OverrideResponse, PatternResponse,
    SimStartRequest, SimStartResponse, SimStopResponse, SimState,
)
from app.simulation.types import SimStatus

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


@router.post("/reset", response_model=SimStartResponse)
async def reset(req: SimStartRequest, request: Request) -> SimStartResponse:
    """Stop any running sim, then start a fresh one. Returns the new sim_id."""
    runner = _runner(request)
    if runner.status == SimStatus.RUNNING:
        await runner.stop()
    sim_id = await runner.start(time_scale=req.time_scale)
    return SimStartResponse(
        status=runner.status, sim_id=sim_id,
        time_scale=req.time_scale, started_at=_now(),
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


@router.get("/pattern", response_model=PatternResponse)
async def get_pattern(request: Request) -> PatternResponse:
    """Current 96-step demand multiplier pattern. Empty list before /sim/start."""
    sim = _runner(request)._sim
    return PatternResponse(
        pattern_id=sim.current_pattern_id,
        multipliers=sim.current_pattern_multipliers(),
        step_minutes=15,
    )


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """
    Server-Sent Events: pushes the cached sim state after each tick.
    On connect, immediately sends the current snapshot (so clients don't
    have to wait for the next tick).
    """
    runner = _runner(request)
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    runner.subscribe(queue)

    async def event_generator():
        try:
            # Initial snapshot
            initial = runner.get_cached_state()
            yield f"data: {json.dumps(initial, default=str)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    state = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies from closing the connection
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(state, default=str)}\n\n"
        finally:
            runner.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
