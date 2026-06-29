"""
XGBoost multi-output regressor (pressure, demand, head) re-implementing the
notebook pipeline. Pure ML — no I/O. Used by PredictionService.
"""
import numpy as np
import pandas as pd
import shap
from sklearn.multioutput import MultiOutputRegressor
from xgboost import XGBRegressor

from app.ml.FeatureBuilder import FeatureBuilder

DEFAULT_XGB_PARAMS = dict(
    n_estimators=1353,
    subsample=0.8231471789648677,
    eta=0.3,
    colsample_bytree=0.5,
    min_child_weight=10,
    reg_lambda=0.001,
    tree_method="hist",
    n_jobs=-1,
    random_state=42,
)


class PressurePredictor:
    def __init__(self, params: dict | None = None) -> None:
        self._params = dict(params) if params else dict(DEFAULT_XGB_PARAMS)
        self._model: MultiOutputRegressor | None = None

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, frame: pd.DataFrame) -> int:
        X = frame[FeatureBuilder.feature_columns()]
        Y = frame[FeatureBuilder.TARGET_COLUMNS]
        self._model = MultiOutputRegressor(XGBRegressor(**self._params))
        self._model.fit(X, Y)
        return len(X)

    def predict_frame(self, X) -> np.ndarray:
        return np.asarray(self._model.predict(X))

    def forecast(self, demand_hist, head_hist, press_hist, static_row,
                 horizon_steps, start_hour, step_hours) -> list[dict]:
        d, h, p = list(demand_hist), list(head_hist), list(press_hist)
        cols = FeatureBuilder.feature_columns()
        out: list[dict] = []
        for k in range(horizon_steps):
            row = dict(static_row)
            for lag in FeatureBuilder.LAGS:
                row[f"Demand_lag{lag}"] = d[-lag]
                row[f"Head_lag{lag}"] = h[-lag]
                row[f"Press_lag{lag}"] = p[-lag]
            X = pd.DataFrame([[row[c] for c in cols]], columns=cols)
            pred = self.predict_frame(X)[0]
            pressure, demand, head = float(pred[0]), float(pred[1]), float(pred[2])
            p.append(pressure)
            d.append(demand)
            h.append(head)
            out.append({"hr": start_hour + (k + 1) * step_hours, "p": pressure})
        return out

    def shap_for_node(self, X, top_n: int = 15) -> list[dict]:
        pressure_model = self._model.estimators_[0]
        explainer = shap.TreeExplainer(pressure_model)
        values = X.values if hasattr(X, "values") else np.asarray(X)
        sv = np.asarray(explainer.shap_values(values))
        mean_abs = np.abs(sv).mean(axis=0)
        cols = FeatureBuilder.feature_columns()
        ranked = sorted(zip(cols, mean_abs), key=lambda t: t[1], reverse=True)[:top_n]
        return [{"name": name, "mean_abs_shap": float(val)} for name, val in ranked]
