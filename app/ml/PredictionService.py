"""
Prediction engine orchestrator (singleton on app.state.predictor).

Owns the static training lifecycle (seed → train), assembles per-node
seed/live/overlay/forecast series for the chart, and runs SHAP. Bridges
SupabaseDB (reads) and PressurePredictor (ML). Heavy calls are synchronous and
meant to be invoked via asyncio.to_thread from the async routes.
"""
import asyncio
import logging
from datetime import UTC, datetime
from enum import Enum

import numpy as np

from app.ml.FeatureBuilder import FeatureBuilder

logger = logging.getLogger(__name__)

FIFTEEN_MIN_HR = 0.25
MAX_LAG = max(FeatureBuilder.LAGS)


class PredictionState(str, Enum):
    NOT_TRAINED = "not_trained"
    SEEDING = "seeding"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


class PredictionService:
    def __init__(self, db, predictor, static_features, seeder=None,
                 horizon_steps: int = 384, step_hours: float = 0.25) -> None:
        self._db = db
        self._predictor = predictor
        self._static = dict(static_features)
        self._seeder = seeder
        self._horizon = horizon_steps
        self._step_hours = step_hours
        self._state = PredictionState.NOT_TRAINED
        self._model_id: str | None = None
        self._n_train_rows = 0
        self._trained_at: datetime | None = None
        self._error: str | None = None

    # ---------------- status ----------------

    @property
    def state(self) -> PredictionState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state == PredictionState.READY

    def status(self) -> dict:
        return {
            "state": self._state.value,
            "model_id": self._model_id,
            "n_train_rows": self._n_train_rows,
            "trained_at": self._trained_at,
            "error": self._error,
        }

    def nodes(self) -> list[str]:
        return sorted(self._static.keys())

    # ---------------- training ----------------

    async def train_in_background(self) -> None:
        try:
            await asyncio.to_thread(self._train_sync)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Prediction training failed")
            self._state = PredictionState.FAILED
            self._error = repr(exc)

    def _train_sync(self) -> None:
        if self._db.seed_node_results_empty() or not self._seed_is_15min():
            if self._seeder is None:
                raise RuntimeError("Seed table empty/wrong-resolution and no seeder provided")
            self._state = PredictionState.SEEDING
            logger.info("Seeding 15-min training data ...")
            self._seeder()
        self._state = PredictionState.TRAINING
        rows = self._db.fetch_seed_node_results()
        frame = FeatureBuilder.build_frame(rows, self._static)
        n = self._predictor.train(frame)
        self._n_train_rows = int(n)
        self._model_id = "xgb-seed-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        self._trained_at = datetime.now(UTC)
        self._state = PredictionState.READY
        logger.info(f"Prediction model trained on {n} rows: {self._model_id}")

    def _seed_is_15min(self) -> bool:
        try:
            step = self._db.seed_node_results_step_hours()
        except Exception:  # noqa: BLE001
            return False
        return step is not None and abs(step - FIFTEEN_MIN_HR) < 0.05

    # ---------------- inference ----------------

    def _stitched_series(self, node_id: str, run_id):
        seed_rows = self._db.fetch_seed_node_series(node_id)
        live_rows = self._db.fetch_live_node_series(node_id, run_id) if run_id else []
        seed_end = float(max((float(r["sim_hour"]) for r in seed_rows), default=0.0))
        stitched = []
        for r in seed_rows:
            stitched.append({"node_id": node_id, "sim_hour": float(r["sim_hour"]),
                             "pressure_psi": float(r["pressure_psi"]),
                             "head_ft": float(r["head_ft"]),
                             "demand_gpm": float(r["demand_gpm"])})
        for r in live_rows:
            stitched.append({"node_id": node_id, "sim_hour": seed_end + float(r["sim_hour"]),
                             "pressure_psi": float(r["pressure_psi"]),
                             "head_ft": float(r["head_ft"]),
                             "demand_gpm": float(r["demand_gpm"])})
        return seed_rows, live_rows, seed_end, stitched

    def node_prediction(self, node_id: str, run_id) -> dict:
        if node_id not in self._static:
            raise KeyError(node_id)
        seed_rows, live_rows, seed_end, stitched = self._stitched_series(node_id, run_id)

        seed_band = [{"hr": float(r["sim_hour"]), "p": float(r["pressure_psi"])} for r in seed_rows]
        live_band = [{"hr": seed_end + float(r["sim_hour"]), "p": float(r["pressure_psi"])}
                     for r in live_rows]

        overlay: list[dict] = []
        metrics = {"live_rmse": None, "live_r2": None}
        if live_rows:
            frame = FeatureBuilder.build_frame(stitched, {node_id: self._static[node_id]})
            if not frame.empty:
                preds = self._predictor.predict_frame(frame[FeatureBuilder.feature_columns()])
                pred_press = np.asarray(preds)[:, 0]
                live_mask = (frame["sim_hour"] > seed_end + 1e-9).to_numpy()
                overlay = [{"hr": float(hr), "p": float(pp)}
                           for hr, pp, m in zip(frame["sim_hour"], pred_press, live_mask) if m]
                actual = frame.loc[live_mask, "pressure_psi"].to_numpy()
                pp = pred_press[live_mask]
                if len(actual) >= 2:
                    metrics["live_rmse"] = float(np.sqrt(np.mean((actual - pp) ** 2)))
                    ss_res = float(np.sum((actual - pp) ** 2))
                    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
                    metrics["live_r2"] = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None

        forecast: list[dict] = []
        if len(stitched) >= MAX_LAG:
            d = [r["demand_gpm"] for r in stitched][-MAX_LAG:]
            h = [r["head_ft"] for r in stitched][-MAX_LAG:]
            p = [r["pressure_psi"] for r in stitched][-MAX_LAG:]
            start_hr = stitched[-1]["sim_hour"]
            static_row = FeatureBuilder.static_row(
                self._static[node_id]["elev"], self._static[node_id]["base_demand"])
            forecast = self._predictor.forecast(
                d, h, p, static_row, self._horizon, start_hr, self._step_hours)

        live_end = (seed_end + float(live_rows[-1]["sim_hour"])) if live_rows else seed_end
        forecast_end = forecast[-1]["hr"] if forecast else live_end
        return {
            "node_id": node_id,
            "seed": seed_band,
            "live": live_band,
            "overlay": overlay,
            "forecast": forecast,
            "regions": {"seed_end": seed_end, "live_end": float(live_end),
                        "forecast_end": float(forecast_end)},
            "metrics": metrics,
            "note": None if live_rows else "No live run yet; forecast continues from seed data.",
        }

    def node_shap(self, node_id: str, run_id, top_n: int = 15) -> dict:
        if node_id not in self._static:
            raise KeyError(node_id)
        _seed_rows, live_rows, seed_end, stitched = self._stitched_series(node_id, run_id)
        frame = FeatureBuilder.build_frame(stitched, {node_id: self._static[node_id]})
        if frame.empty:
            return {"node_id": node_id, "features": [], "top_n": top_n}
        if live_rows:
            sub = frame[frame["sim_hour"] > seed_end + 1e-9]
            if sub.empty:
                sub = frame.tail(96)
        else:
            sub = frame.tail(96)
        X = sub[FeatureBuilder.feature_columns()]
        feats = self._predictor.shap_for_node(X, top_n=top_n)
        return {"node_id": node_id, "features": feats, "top_n": top_n}
