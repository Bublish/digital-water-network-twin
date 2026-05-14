"""
End-to-end test using FastAPI's TestClient. Mocks Supabase + Simulator
to avoid network dependencies; uses the real stub ML route.
"""
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
