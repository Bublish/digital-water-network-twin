from unittest.mock import MagicMock

import numpy as np


def _seed_series(n=40):
    return [{"sim_hour": 0.25 * i, "pressure_psi": 50.0 + i,
             "head_ft": 200.0 + i, "demand_gpm": 10.0 + i} for i in range(n)]


def _make_service(live_rows, horizon=5):
    from app.ml.PredictionService import PredictionService
    db = MagicMock()
    db.fetch_seed_node_series.return_value = _seed_series(40)
    db.fetch_live_node_series.return_value = live_rows
    predictor = MagicMock()
    predictor.predict_frame.side_effect = lambda X: np.zeros((len(X), 3))
    predictor.forecast.return_value = [{"hr": 100.0 + i, "p": 1.0} for i in range(horizon)]
    static = {"J1": {"elev": 100.0, "base_demand": 20.0}}
    return PredictionService(db, predictor, static, horizon_steps=horizon, step_hours=0.25)


def test_initial_state_and_nodes():
    from app.ml.PredictionService import PredictionState
    svc = _make_service([])
    assert svc.state == PredictionState.NOT_TRAINED
    assert svc.is_ready is False
    assert svc.nodes() == ["J1"]
    assert svc.status()["state"] == "not_trained"


def test_train_sync_marks_ready():
    from app.ml.PredictionService import PredictionState
    svc = _make_service([])
    svc._db.seed_node_results_empty.return_value = False
    svc._db.seed_node_results_step_hours.return_value = 0.25
    svc._db.fetch_seed_node_results.return_value = [
        {**r, "node_id": "J1"} for r in _seed_series(40)
    ]
    svc._predictor.train.return_value = 16
    svc._train_sync()
    assert svc.state == PredictionState.READY
    assert svc.is_ready
    assert svc.status()["n_train_rows"] == 16


def test_train_sync_seeds_when_empty():
    svc = _make_service([])
    svc._db.seed_node_results_empty.return_value = True
    svc._db.seed_node_results_step_hours.return_value = None
    svc._db.fetch_seed_node_results.return_value = [
        {**r, "node_id": "J1"} for r in _seed_series(40)
    ]
    svc._predictor.train.return_value = 16
    seeder = MagicMock()
    svc._seeder = seeder
    svc._train_sync()
    seeder.assert_called_once()


def test_node_prediction_assembles_bands_and_regions():
    live = [{"sim_hour": 0.25 * i, "pressure_psi": 70.0 + i,
             "head_ft": 210.0 + i, "demand_gpm": 12.0 + i} for i in range(8)]
    svc = _make_service(live, horizon=5)
    out = svc.node_prediction("J1", "run-1")

    assert len(out["seed"]) == 40
    assert len(out["live"]) == 8
    # seed_end == last seed sim_hour == 0.25*39
    assert out["regions"]["seed_end"] == 0.25 * 39
    # live band hours are offset by seed_end
    assert out["live"][0]["hr"] == 0.25 * 39 + 0.0
    # forecast band length == horizon
    assert len(out["forecast"]) == 5
    assert out["regions"]["forecast_end"] == out["forecast"][-1]["hr"]


def test_node_prediction_no_live_returns_note():
    svc = _make_service([], horizon=5)
    out = svc.node_prediction("J1", None)
    assert out["live"] == []
    assert out["overlay"] == []
    assert out["note"]
    # forecast still produced from the seed tail
    assert len(out["forecast"]) == 5


def test_node_prediction_unknown_node_raises():
    import pytest
    svc = _make_service([])
    with pytest.raises(KeyError):
        svc.node_prediction("NOPE", "run-1")
