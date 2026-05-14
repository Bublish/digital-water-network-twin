"""
End-to-end test using FastAPI's TestClient. Mocks Supabase + Simulator
to avoid network dependencies; uses the real stub ML route.
"""
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport


@pytest.fixture
def test_app():
    """Build a FastAPI app with mocked sim and db, real ml route, real runner."""
    from api.ml_routes import router as ml_router
    from api.sim_routes import router as sim_router
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import SimStatus

    # Build a fake sim that mimics EPANETSimulator
    fake_sim = MagicMock()
    fake_sim.start_simulation.return_value = "test-sim-id"
    fake_sim.read_state.return_value = MagicMock(
        sim_time_sec=0.0,
        tank_levels={"T1": 25.0, "T2": 40.0},
        pump_states={"P1": "CLOSED", "P2": "CLOSED"},
    )
    fake_sim.step.return_value = MagicMock(
        sim_time_sec=900.0,
        pressures={"J1": 60.0}, heads={"J1": 200.0}, demands={"J1": 10.0},
        flows={"P1": 800.0, "P2": 800.0},
        velocities={"P1": 5.0, "P2": 5.0},
        headlosses={"P1": 1.0, "P2": 1.0},
        pump_states={"P1": "OPEN", "P2": "OPEN"},
        tank_levels={"T1": 25.5, "T2": 40.5},
    )
    fake_sim.compute_network_info.return_value = {
        "junction_count": 130, "tank_count": 2, "reservoir_count": 1,
        "pump_count": 2, "valve_count": 0, "pipe_count": 168,
        "total_pipe_length_mi": 23.3, "total_demand_gpm": 906.0,
        "total_demand_mgd": 1.30, "pattern_steps": 96, "pattern_period_min": 15,
    }
    fake_sim.render_plot_png.return_value = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
    fake_db = MagicMock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        app.state.runner = SimulationRunner(
            sim=fake_sim, db=fake_db, http_client=http_client,
            ml_url="http://testserver/ml/predict",
        )
        app.state.network_info = fake_sim.compute_network_info()
        app.state.network_plot_png = fake_sim.render_plot_png()
        try:
            yield
        finally:
            if app.state.runner.status == SimStatus.RUNNING:
                await app.state.runner.stop()
            await http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(sim_router)
    app.include_router(ml_router)
    from api.network_routes import router as network_router
    app.include_router(network_router)
    app.state._fake_sim = fake_sim
    app.state._fake_db = fake_db
    return app


def test_start_then_override_then_state(test_app):
    with TestClient(test_app) as client:
        # 1. Start the sim at high time_scale so a few ticks fire fast
        r = client.post("/sim/start", json={"time_scale": 10000})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "RUNNING"
        assert body["sim_id"] == "test-sim-id"

        # 2. Wait briefly for at least one tick
        time.sleep(0.5)

        # 3. Set HAND_OPEN on P1
        r = client.post("/sim/override", json={"pump_id": "P1", "mode": "HAND_OPEN"})
        assert r.status_code == 200
        assert r.json()["pump_modes"]["P1"] == "HAND_OPEN"

        # 4. Wait for the next tick to pick up the override
        time.sleep(0.5)

        # 5. State should reflect the override
        r = client.get("/sim/state")
        assert r.status_code == 200
        state = r.json()
        assert state["status"] == "RUNNING"
        assert state["pump_modes"]["P1"] == "HAND_OPEN"

        # 6. Stop
        r = client.post("/sim/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "STOPPED"


def test_double_start_returns_409(test_app):
    with TestClient(test_app) as client:
        client.post("/sim/start", json={"time_scale": 10000})
        r = client.post("/sim/start", json={"time_scale": 10000})
        assert r.status_code == 409
        client.post("/sim/stop")


def test_stop_when_not_running_returns_409(test_app):
    with TestClient(test_app) as client:
        r = client.post("/sim/stop")
        assert r.status_code == 409


def test_get_network_info(test_app):
    with TestClient(test_app) as client:
        r = client.get("/network/info")
        assert r.status_code == 200
        body = r.json()
        assert body["pump_count"] == 2
        assert body["tank_count"] == 2
        assert body["junction_count"] == 130


def test_get_network_plot_png(test_app):
    with TestClient(test_app) as client:
        r = client.get("/network/plot.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")


async def _read_first_sse_event(app, path: str = "/sim/stream", timeout: float = 5.0) -> dict:
    """
    Drive the ASGI app directly to read the first SSE 'data:' event.

    httpx's ASGITransport buffers the entire response body before returning,
    which deadlocks on long-lived streaming endpoints. To test SSE we have
    to talk ASGI directly and stop after the first body chunk.
    """
    import asyncio as _asyncio

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "state": {},
    }

    body_chunks: list[bytes] = []
    status_holder: dict = {}
    headers_holder: dict = {}
    first_body_received = _asyncio.Event()
    disconnect_now = _asyncio.Event()

    async def receive():
        # First request body, then block until we want to disconnect.
        if not getattr(receive, "_sent", False):
            receive._sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect_now.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            status_holder["code"] = message["status"]
            headers_holder["headers"] = dict(message.get("headers", []))
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if chunk:
                body_chunks.append(chunk)
                first_body_received.set()

    app_task = _asyncio.create_task(app(scope, receive, send))
    try:
        await _asyncio.wait_for(first_body_received.wait(), timeout=timeout)
    finally:
        disconnect_now.set()
        try:
            await _asyncio.wait_for(app_task, timeout=2.0)
        except _asyncio.TimeoutError:
            app_task.cancel()
            try:
                await app_task
            except (BaseException,):
                pass

    assert status_holder.get("code") == 200, f"Expected 200, got {status_holder.get('code')}"
    ct = headers_holder["headers"].get(b"content-type", b"").decode()
    assert ct.startswith("text/event-stream"), f"Unexpected content-type: {ct!r}"

    raw = b"".join(body_chunks).decode()
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"No data: line in SSE body: {raw!r}")


@pytest.mark.asyncio
async def test_sim_stream_pushes_event_after_tick(test_app):
    """Open SSE stream after starting the sim; expect a JSON snapshot event."""
    async with test_app.router.lifespan_context(test_app):
        # Start the sim via the ASGI app so the runner ticks
        async with httpx.AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            r = await client.post("/sim/start", json={"time_scale": 10000})
            assert r.status_code == 200

        event = await _read_first_sse_event(test_app)
        assert event["status"] in ("RUNNING", "STOPPED")
        assert "tank_levels" in event

        async with httpx.AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            await client.post("/sim/stop")


@pytest.mark.asyncio
async def test_sim_stream_sends_initial_event_when_not_started(test_app):
    """On connect, server immediately sends one snapshot — even if sim never started."""
    async with test_app.router.lifespan_context(test_app):
        event = await _read_first_sse_event(test_app)
        assert event["status"] == "NOT_STARTED"
