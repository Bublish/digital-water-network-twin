from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakePredictor:
    def __init__(self, ready=True):
        self._ready = ready

    @property
    def is_ready(self):
        return self._ready

    def status(self):
        return {"state": "ready" if self._ready else "training",
                "model_id": "m1", "n_train_rows": 10, "trained_at": None, "error": None}

    def nodes(self):
        return ["J1", "J2"]

    def node_prediction(self, node_id, run_id):
        if node_id not in self.nodes():
            raise KeyError(node_id)
        return {"node_id": node_id, "seed": [{"hr": 0.0, "p": 50.0}], "live": [],
                "overlay": [], "forecast": [{"hr": 96.25, "p": 51.0}],
                "regions": {"seed_end": 96.0, "live_end": 96.0, "forecast_end": 96.25},
                "metrics": {"live_rmse": None, "live_r2": None}, "note": None}

    def node_shap(self, node_id, run_id, top_n=15):
        if node_id not in self.nodes():
            raise KeyError(node_id)
        return {"node_id": node_id,
                "features": [{"name": "Press_lag1", "mean_abs_shap": 2.0}], "top_n": top_n}


def _app(predictor):
    from app.api.prediction_routes import router
    app = FastAPI()
    app.include_router(router)
    app.state.predictor = predictor
    app.state.runner = MagicMock(sim_id="run-1")
    return app


def test_status_ok():
    with TestClient(_app(FakePredictor())) as c:
        r = c.get("/prediction/status")
        assert r.status_code == 200
        assert r.json()["state"] == "ready"


def test_nodes_listed_even_before_ready():
    with TestClient(_app(FakePredictor(ready=False))) as c:
        r = c.get("/prediction/nodes")
        assert r.status_code == 200
        assert r.json()["nodes"] == ["J1", "J2"]


def test_node_503_before_ready():
    with TestClient(_app(FakePredictor(ready=False))) as c:
        assert c.get("/prediction/node/J1").status_code == 503


def test_node_payload_when_ready():
    with TestClient(_app(FakePredictor())) as c:
        r = c.get("/prediction/node/J1")
        assert r.status_code == 200
        body = r.json()
        assert body["node_id"] == "J1"
        assert body["regions"]["forecast_end"] == 96.25


def test_unknown_node_404():
    with TestClient(_app(FakePredictor())) as c:
        assert c.get("/prediction/node/NOPE").status_code == 404


def test_shap_payload():
    with TestClient(_app(FakePredictor())) as c:
        r = c.get("/prediction/node/J1/shap")
        assert r.status_code == 200
        assert r.json()["features"][0]["name"] == "Press_lag1"
