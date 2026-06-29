import numpy as np
import pandas as pd

from app.ml.FeatureBuilder import FeatureBuilder


def _series(node_id, n):
    # Deterministic, strictly varying values so lag checks are unambiguous.
    return [
        {"node_id": node_id, "sim_hour": 0.25 * i,
         "pressure_psi": 50.0 + i, "head_ft": 200.0 + i, "demand_gpm": 10.0 + i}
        for i in range(n)
    ]


def test_feature_columns_are_18_without_hours():
    cols = FeatureBuilder.feature_columns()
    assert len(cols) == 18
    assert "hours" not in cols
    assert cols[:3] == ["Elev", "Base_Demand_GPM", "Demand_to_Elev_ratio"]
    assert "Press_lag24" in cols and "Demand_lag1" in cols and "Head_lag6" in cols


def test_static_row_ratio():
    row = FeatureBuilder.static_row(100.0, 20.0)
    assert row["Elev"] == 100.0
    assert row["Base_Demand_GPM"] == 20.0
    assert row["Demand_to_Elev_ratio"] == 20.0 / (100.0 + 1e-6)


def test_build_frame_drops_lag_rows_and_lags_align():
    rows = _series("J1", 30)
    static = {"J1": {"elev": 100.0, "base_demand": 20.0}}
    frame = FeatureBuilder.build_frame(rows, static)

    # 30 rows minus the 24 dropped for the max lag = 6 usable rows
    assert len(frame) == 30 - 24
    # Press_lag1 of a row equals the pressure one step earlier
    first = frame.iloc[0]
    assert first["Press_lag1"] == first["pressure_psi"] - 1
    assert first["Demand_lag24"] == first["demand_gpm"] - 24
    # Column set matches contract
    for c in FeatureBuilder.feature_columns() + FeatureBuilder.TARGET_COLUMNS:
        assert c in frame.columns


def test_build_frame_filters_to_static_nodes():
    rows = _series("J1", 30) + _series("TANK-1", 30)
    static = {"J1": {"elev": 100.0, "base_demand": 20.0}}
    frame = FeatureBuilder.build_frame(rows, static)
    assert set(frame["node_id"].unique()) == {"J1"}


def test_build_frame_empty_input_returns_empty():
    frame = FeatureBuilder.build_frame([], {"J1": {"elev": 1.0, "base_demand": 1.0}})
    assert frame.empty
