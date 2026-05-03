"""
Usage:
    from simulation.Simulator import EPANETSimulator

    # Seed 3 days of data as fast as possible:
    with EPANETSimulator() as sim:
        run_id = sim.seed(days=3)
        print(f"Seeded run_id: {run_id}")

    # Run indefinitely in real-time (1 simulated hour = 1 real hour):
    with EPANETSimulator() as sim:
        sim.run_live()
"""

import shutil
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np

from epyt import epanet

from db.SupabaseClient import SupabaseDB


class EPANETSimulator:
    """
    Runs hydraulic EPS on the WSN1 network and streams results to Supabase.

    Two modes:
      seed(days=3)  — run N days as fast as possible, inserting hourly rows
      run_live()    — run one simulated hour, insert, sleep 1 real hour, repeat forever
    """

    def __init__(self) -> None:
        self._db = SupabaseDB()

        inp_bytes = self._db.download_network()
        self._tmpdir: Path = Path(tempfile.mkdtemp())
        self._inp_path: Path = self._tmpdir / "network.inp"
        self._inp_path.write_bytes(inp_bytes)
        self._network: epanet | None = None
        self._base_demands: list[float] | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def load(self) -> "EPANETSimulator":
        """Open the network file. Returns self for use as a context manager."""
        try:
            self._network = epanet(str(self._inp_path), loadfile=True)
            raw = self._network.getNodeBaseDemands()   # {1: [d0, d1, ..., dN]}
            self._base_demands = list(raw[1])          # category 1; defensive copy
            print("Network loaded successfully.")
        except Exception as exc:
            raise RuntimeError(f"Failed to load network: {exc}") from exc
        return self

    def seed(self, days: int = 4) -> str:
        """
        Run a full EPS for `days` days as fast as EPANET can compute.
        Inserts one batch of node + link rows per simulated hour.
        Returns the run_id (UUID string) for querying results in Supabase.
        """
        if self._network is None:
            raise RuntimeError("Network not loaded. Use as a context manager or call load() first.")
        run_id = str(uuid.uuid4())

        # Order matters: EPANET clamps hydraulic step to ≤ pattern step,
        # so set pattern step first. Reporting step controls getComputedHydraulicTimeSeries sampling.
        hstep = 3600  # 1 hour in seconds
        self._network.setTimePatternStep(hstep)
        self._network.setTimeHydraulicStep(hstep)
        self._network.setTimeReportingStep(hstep)
        self._network.setTimeSimulationDuration(days * 24 * hstep)

        print(f"[seed] Simulating {days} day(s)  run_id={run_id} ...")
        ts = self._network.getComputedHydraulicTimeSeries()

        node_ids = self._network.getNodeNameID()
        link_ids = self._network.getLinkNameID()

        node_rows, link_rows = self._build_seed_rows(run_id, ts, node_ids, link_ids)

        print(f"[seed] Inserting {len(node_rows)} node rows ...")
        self._chunked_insert(self._db.insert_seed_node_results, node_rows)
        print(f"[seed] Inserting {len(link_rows)} link rows ...")
        self._chunked_insert(self._db.insert_seed_link_results, link_rows)
        print(f"[seed] Done.")
        return run_id



    def run_live(self) -> None:
        """Step the EPS indefinitely, inserting every hydraulic step to Supabase."""
        hstep = 3600
        self._network.setTimePatternStep(hstep)   # type: ignore
        self._network.setTimeHydraulicStep(hstep)  # type: ignore

        self._log_control_definitions()

        node_ids   = self._network.getNodeNameID()   # type: ignore
        link_ids   = self._network.getLinkNameID()   # type: ignore
        pump_names = self._network.getLinkPumpNameID()  # type: ignore

        while True:
            run_id = str(uuid.uuid4())
            print(f"[run_live] Starting cycle  run_id={run_id}")
            self._network.openHydraulicAnalysis()   # type: ignore
            self._network.initializeHydraulicAnalysis(0)  # type: ignore
            prev_pump_states = None
            try:
                while True:
                    t_sec = self._network.runHydraulicAnalysis()  # type: ignore
                    t_hr  = t_sec / 3600.0

                    curr_pump_states = self._network.getLinkPumpState()  # type: ignore
                    if prev_pump_states is not None:
                        self._log_pump_state_changes(t_hr, curr_pump_states, prev_pump_states, pump_names)
                    prev_pump_states = curr_pump_states

                    self._insert_snapshot(self._network, run_id, t_hr, node_ids, link_ids)

                    self._randomizeDemand()

                    tstep = self._network.nextHydraulicAnalysisStep()  # type: ignore
                    if tstep <= 0:
                        break
            finally:
                self._network.closeHydraulicAnalysis()  # type: ignore

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


    def _build_seed_rows(
        self, run_id: str, ts, node_ids, link_ids
    ) -> tuple[list[dict], list[dict]]:
        """Convert a getComputedHydraulicTimeSeries result into Supabase-ready row dicts."""
        node_rows: list[dict] = []
        link_rows: list[dict] = []
        for step_idx, t_sec in enumerate(ts.Time):
            t_hr = float(t_sec) / 3600.0
            for node_idx, nid in enumerate(node_ids):
                node_rows.append({
                    "run_id":       run_id,
                    "sim_hour":     t_hr,
                    "node_id":      nid,
                    "pressure_psi": float(ts.Pressure[step_idx][node_idx]),
                    "head_ft":      float(ts.Head[step_idx][node_idx]),
                    "demand_gpm":   float(ts.Demand[step_idx][node_idx]),
                })
            for link_idx, lid in enumerate(link_ids):
                link_rows.append({
                    "run_id":              run_id,
                    "sim_hour":            t_hr,
                    "link_id":             lid,
                    "flow_gpm":            float(ts.Flow[step_idx][link_idx]),
                    "velocity_fps":        float(ts.Velocity[step_idx][link_idx]),
                    "headloss_ft_per_kft": float(ts.HeadLoss[step_idx][link_idx]),
                })
        return node_rows, link_rows

    def _chunked_insert(self, insert_fn, rows: list[dict], chunk_size: int = 500) -> None:
        """Call insert_fn in chunks to stay within Supabase request-size limits."""
        for i in range(0, len(rows), chunk_size):
            insert_fn(rows[i : i + chunk_size])


    def _randomizeDemand(self) -> None:
        """Per-node lognormal demand multiplier, always applied to original base demands."""
        if self._base_demands is None:
            raise RuntimeError("_randomizeDemand called before load().")

        sigma = 0.15                  # 15% node-level coefficient of variation
        mu = -0.5 * sigma ** 2        # ensures E[multiplier] = 1 (no bias)

        new_demands: list[float] = []
        for base in self._base_demands:
            multiplier = np.random.lognormal(mean=mu, sigma=sigma)
            new_demands.append(base * multiplier)

        self._network.setNodeBaseDemands(new_demands)  # type: ignore

    def _insert_snapshot(self, network, run_id: str, t_hr: float, node_ids, link_ids) -> None:
        """Collect hydraulic results for the current timestep and bulk-insert to Supabase."""
        pressures  = network.getNodePressure()
        heads      = network.getNodeHydraulicHead()
        demands    = network.getNodeActualDemand()
        flows      = network.getLinkFlow()
        velocities = network.getLinkVelocity()
        headlosses = network.getLinkHeadloss()

        node_rows = [
            {
                "run_id":       run_id,
                "sim_hour":     t_hr,
                "node_id":      nid,
                "pressure_psi": float(pressures[i]),
                "head_ft":      float(heads[i]),
                "demand_gpm":   float(demands[i]),
            }
            for i, nid in enumerate(node_ids)
        ]
        link_rows = [
            {
                "run_id":               run_id,
                "sim_hour":             t_hr,
                "link_id":              lid,
                "flow_gpm":             float(flows[i]),
                "velocity_fps":         float(velocities[i]),
                "headloss_ft_per_kft":  float(headlosses[i]),
            }
            for i, lid in enumerate(link_ids)
        ]

        self._db.insert_live_node_results(node_rows)
        self._db.insert_live_link_results(link_rows)

    def _log_control_definitions(self) -> None:
        controls = self._network.getControls()   # type: ignore
        rules    = self._network.getRules()      # type: ignore
        print("[init] Simple controls:")
        for c in controls:  # type: ignore
            print(f"  {c.Control}")  # type: ignore
        print("[init] Rule-based controls:")
        for r in rules:  # type: ignore
            print(f"  {r.Rule}")  # type: ignore

    def _log_pump_state_changes(self, t_hr: float, curr, prev, pump_names) -> None:
        for i, (c, p) in enumerate(zip(curr, prev)):
            if c != p:
                name = pump_names[i] if i < len(pump_names) else str(i)
                print(f"[control] t={t_hr:.4f}h  Pump {name}  {p} → {c}")

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release EPANET resources and clean up temp files. Safe to call multiple times."""
        if self._network is not None:
            try:
                self._network.unload()
            except Exception:
                pass
            self._network = None
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def __enter__(self) -> "EPANETSimulator":
        return self.load()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
