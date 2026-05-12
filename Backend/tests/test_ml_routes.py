from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_app():
    from api.ml_routes import router as ml_router
    app = FastAPI()
    app.include_router(ml_router)
    return app


def test_predict_returns_open_when_tank_below_threshold():
    client = TestClient(make_app())
    resp = client.post("/ml/predict", json={
        "sim_time_hr": 6.0,
        "tank_levels": {"T1": 25.0, "T2": 40.0},
        "pump_modes":  {"P1": "AUTO", "P2": "AUTO"},
        "current_price": 0.18,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_id"] == "stub-v0"
    assert body["commands"] == {"P1": "OPEN", "P2": "OPEN"}


def test_predict_returns_closed_when_tanks_full():
    client = TestClient(make_app())
    resp = client.post("/ml/predict", json={
        "sim_time_hr": 14.0,
        "tank_levels": {"T1": 45.0, "T2": 48.0},
        "pump_modes":  {"P1": "AUTO", "P2": "AUTO"},
    })
    body = resp.json()
    assert body["commands"] == {"P1": "CLOSED", "P2": "CLOSED"}
