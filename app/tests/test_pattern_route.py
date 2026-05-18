"""
Unit-level test for GET /sim/pattern.

Builds a minimal FastAPI app with a fake simulator wired into a real
SimulationRunner — the runner's only role here is to expose ._sim, which
the route reads from. No tick loop is started.
"""
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_pattern():
    from app.api.sim_routes import router as sim_router
    from app.scheduler.SimulationRunner import SimulationRunner

    fake_sim = MagicMock()
    fake_sim.current_pattern_id = None
    fake_sim.current_pattern_multipliers.return_value = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        http_client = httpx.AsyncClient(base_url="http://testserver")
        app.state.runner = SimulationRunner(
            sim=fake_sim, db=MagicMock(), http_client=http_client,
            ml_url="http://testserver/ml/predict",
        )
        try:
            yield
        finally:
            await http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(sim_router)
    app.state._fake_sim = fake_sim
    return app


def test_pattern_route_returns_empty_before_rotation(app_with_pattern):
    with TestClient(app_with_pattern) as client:
        r = client.get("/sim/pattern")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "pattern_id":   None,
            "multipliers":  [],
            "step_minutes": 15,
        }


def test_pattern_route_returns_96_multipliers_after_rotation(app_with_pattern):
    fake_sim = app_with_pattern.state._fake_sim
    fake_sim.current_pattern_id = "abc123def456"
    fake_sim.current_pattern_multipliers.return_value = [1.0 + i * 0.01 for i in range(96)]

    with TestClient(app_with_pattern) as client:
        r = client.get("/sim/pattern")
        assert r.status_code == 200
        body = r.json()
        assert body["pattern_id"] == "abc123def456"
        assert len(body["multipliers"]) == 96
        assert body["multipliers"][0] == pytest.approx(1.0)
        assert body["multipliers"][-1] == pytest.approx(1.95)
        assert body["step_minutes"] == 15
