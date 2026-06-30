"""
Canonical feature/lag frame construction for the pressure predictor.

Identical logic is used for training (on seed rows) and inference (on stitched
seed+live rows), so the feature columns and their order are defined once here.
No I/O, no ML — pure pandas transforms.
"""
import pandas as pd


class FeatureBuilder:
    LAGS = [1, 2, 3, 6, 24]
    STATIC_COLUMNS = ["Elev", "Base_Demand_GPM", "Demand_to_Elev_ratio"]
    TARGET_COLUMNS = ["pressure_psi", "demand_gpm", "head_ft"]
    # (source result column, feature-name prefix)
    _LAG_SOURCES = [("demand_gpm", "Demand"), ("head_ft", "Head"), ("pressure_psi", "Press")]

    @classmethod
    def feature_columns(cls) -> list[str]:
        cols = list(cls.STATIC_COLUMNS)
        for _src, prefix in cls._LAG_SOURCES:
            for lag in cls.LAGS:
                cols.append(f"{prefix}_lag{lag}")
        return cols

    @staticmethod
    def static_row(elev: float, base_demand: float) -> dict:
        elev = float(elev)
        base_demand = float(base_demand)
        return {
            "Elev": elev,
            "Base_Demand_GPM": base_demand,
            "Demand_to_Elev_ratio": base_demand / (elev + 1e-6),
        }

    @classmethod
    def build_frame(cls, rows, static_features: dict) -> pd.DataFrame:
        df = pd.DataFrame(rows).copy()
        if df.empty:
            return df
        df = df[df["node_id"].isin(static_features)].copy()
        if df.empty:
            return df
        df = df.sort_values(["node_id", "sim_hour"]).reset_index(drop=True)

        for src, prefix in cls._LAG_SOURCES:
            grouped = df.groupby("node_id")[src]
            for lag in cls.LAGS:
                df[f"{prefix}_lag{lag}"] = grouped.shift(lag)

        df["Elev"] = df["node_id"].map(lambda n: float(static_features[n]["elev"]))
        df["Base_Demand_GPM"] = df["node_id"].map(lambda n: float(static_features[n]["base_demand"]))
        df["Demand_to_Elev_ratio"] = df["Base_Demand_GPM"] / (df["Elev"] + 1e-6)

        lag_cols = [c for c in cls.feature_columns() if "lag" in c]
        return df.dropna(subset=lag_cols).reset_index(drop=True)
