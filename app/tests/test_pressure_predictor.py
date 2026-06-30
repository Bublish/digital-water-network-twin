import numpy as np
import pandas as pd

from app.ml.FeatureBuilder import FeatureBuilder
from app.ml.PressurePredictor import PressurePredictor

# Tiny params keep the test fast; structure matches DEFAULT_XGB_PARAMS.
FAST = dict(n_estimators=8, eta=0.3, tree_method="hist", n_jobs=1, random_state=42)


def _train_frame(n=80):
    rows = []
    for node in ("J1", "J2"):
        for i in range(n):
            rows.append({
                "node_id": node, "sim_hour": 0.25 * i,
                "pressure_psi": 50.0 + 5.0 * np.sin(i / 4.0),
                "head_ft": 200.0 + 5.0 * np.sin(i / 4.0),
                "demand_gpm": 10.0 + 2.0 * np.cos(i / 4.0),
            })
    static = {"J1": {"elev": 100.0, "base_demand": 20.0},
              "J2": {"elev": 120.0, "base_demand": 15.0}}
    return FeatureBuilder.build_frame(rows, static), static


def test_train_then_predict_shape():
    frame, _ = _train_frame()
    p = PressurePredictor(params=FAST)
    n = p.train(frame)
    assert p.is_trained
    assert n == len(frame)
    out = p.predict_frame(frame[FeatureBuilder.feature_columns()])
    assert out.shape == (len(frame), 3)


def test_forecast_returns_horizon_rows_increasing_hours():
    frame, static = _train_frame()
    p = PressurePredictor(params=FAST)
    p.train(frame)
    fc = p.forecast(
        demand_hist=[10.0] * 24, head_hist=[200.0] * 24, press_hist=[50.0] * 24,
        static_row=FeatureBuilder.static_row(100.0, 20.0),
        horizon_steps=10, start_hour=96.0, step_hours=0.25,
    )
    assert len(fc) == 10
    hrs = [pt["hr"] for pt in fc]
    assert hrs == sorted(hrs)
    assert hrs[0] == 96.25
    assert all(isinstance(pt["p"], float) for pt in fc)


def test_shap_returns_sorted_topn():
    frame, _ = _train_frame()
    p = PressurePredictor(params=FAST)
    p.train(frame)
    X = frame[FeatureBuilder.feature_columns()].head(20)
    feats = p.shap_for_node(X, top_n=5)
    assert len(feats) == 5
    vals = [f["mean_abs_shap"] for f in feats]
    assert vals == sorted(vals, reverse=True)
    assert all(f["name"] in FeatureBuilder.feature_columns() for f in feats)
