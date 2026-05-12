"""
Usage:
    from simulation.Simulator import EPANETSimulator

    # Seed 3 days of data as fast as possible:
    with EPANETSimulator() as sim:
        run_id = sim.seed(days=3)
        print(f"Seeded run_id: {run_id}")

    # Step-by-step interface (driven by SimulationRunner):
    with EPANETSimulator() as sim:
        sim_id = sim.start_simulation()
        sim.rotate_demand_pattern()
        state  = sim.read_state()
        sim.apply_pump_commands({"P1": "OPEN"})
        result = sim.step()
        sim.stop_simulation()
"""

import shutil
import tempfile
import uuid
from pathlib import Path

import numpy as np

from epyt import epanet

from db.SupabaseClient import SupabaseDB
from simulation.types import StepResult, StepState


class EPANETSimulator:
    """
    Runs hydraulic EPS on the WSN1 network and streams results to Supabase.

    Two modes:
      seed(days=3)        — run N days as fast as possible, inserting hourly rows
      start_simulation()  — step-by-step interface driven by SimulationRunner
    """

    def __init__(self) -> None:
        self._db = SupabaseDB()

        inp_bytes = self._db.download_network()
        self._tmpdir: Path = Path(tempfile.mkdtemp())
        self._inp_path: Path = self._tmpdir / "network.inp"
        self._inp_path.write_bytes(inp_bytes)
        self._network: epanet | None = None
        self._base_demands: list[float] | None = None
        self._sim_started: bool = False
        self._current_t_sec: float = 0.0

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



    # ------------------------------------------------------------------
    # Step-by-step interface (replaces run_24_hour_cycle / run_live)
    # ------------------------------------------------------------------

    def start_simulation(self) -> str:
        """
        Configure timesteps, open hydraulic analysis, initialize at t=0.
        Returns a sim_id (UUID) for tagging results.

        For continuous operation we set simulation duration to 365 days;
        if the runner is still going past then, a restart is required.
        """
        if self._network is None:
            raise RuntimeError("Network not loaded. Call load() first.")
        if self._sim_started:
            raise RuntimeError("Simulation already started. Call stop_simulation() first.")

        hstep = 900  # 15 minutes in seconds
        self._network.setTimePatternStep(hstep)
        self._network.setTimeHydraulicStep(hstep)
        self._network.setTimeReportingStep(hstep)
        self._network.setTimeSimulationDuration(365 * 24 * 3600)  # 1 year

        self._log_control_definitions()

        self._network.openHydraulicAnalysis()
        self._network.initializeHydraulicAnalysis(0)
        self._current_t_sec = 0.0

        self._sim_started = True
        sim_id = str(uuid.uuid4())
        print(f"[start_simulation] sim_id={sim_id}")
        return sim_id

    def stop_simulation(self) -> None:
        """
        Close hydraulic analysis. Safe to call when not started (no-op).
        """
        if not self._sim_started:
            return
        try:
            self._network.closeHydraulicAnalysis()
        finally:
            self._sim_started = False

    def rotate_demand_pattern(self) -> None:
        """
        Install a fresh 96-step demand multiplier pattern and re-randomize
        per-node base demands. Called by the scheduler at start AND every
        96 steps (every simulated 24h).

        The pattern multipliers are lognormal around 1.0 with sigma=0.10,
        which is a system-wide diurnal jitter applied on top of the
        per-node lognormal base-demand randomization.
        """
        if self._network is None:
            raise RuntimeError("Network not loaded.")

        # 96 steps × 15 min = 24 h
        sigma = 0.10
        mu = -0.5 * sigma ** 2
        multipliers = np.random.lognormal(mean=mu, sigma=sigma, size=96).tolist()
        self._network.addPattern("DMA_AUTO", multipliers)

        # Re-randomize the per-node base demands too.
        self._randomizeDemand()

    def read_state(self) -> StepState:
        """
        Snapshot current sim time + tank levels + pump statuses (no I/O).
        Used by the scheduler to build the ML predict request before a step.

        Tank levels are computed as (hydraulic head − tank elevation), which
        gives the live water-surface height above the tank floor. Reading
        getNodeTankInitialLevel during a run returns the initial level, not
        the current one — hence the head-minus-elevation derivation.
        """
        if self._network is None or not self._sim_started:
            raise RuntimeError("Simulation not started.")

        node_ids = self._network.getNodeNameID()
        tank_ids = self._network.getNodeTankNameID()
        node_id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

        heads      = self._network.getNodeHydraulicHead()
        elevations = self._network.getNodeElevations()

        tank_levels: dict[str, float] = {}
        for tid in tank_ids:
            i = node_id_to_idx.get(tid)
            if i is None:
                continue
            tank_levels[tid] = float(heads[i]) - float(elevations[i])

        # Use getLinkStatus (commanded status, synchronous with setLinkStatus)
        # rather than getLinkPumpState (last-solved operational state).
        pump_ids = self._network.getLinkPumpNameID()
        pump_states: dict[str, str] = {}
        for pid in pump_ids:
            link_idx = self._network.getLinkIndex(pid)
            status = int(self._network.getLinkStatus(link_idx))
            pump_states[pid] = "OPEN" if status > 0 else "CLOSED"

        return StepState(
            sim_time_sec=float(self._current_t_sec),
            tank_levels=tank_levels,
            pump_states=pump_states,
        )

    def apply_pump_commands(self, commands: dict[str, str]) -> None:
        """
        Apply pump status commands via setLinkStatus.

        commands: {pump_id: "OPEN" | "CLOSED"}.

        Pumps absent from commands are left untouched. Called AFTER override
        resolution, BEFORE step().
        """
        if self._network is None or not self._sim_started:
            raise RuntimeError("Simulation not started.")

        pump_ids = self._network.getLinkPumpNameID()
        pump_name_to_link_index = {
            pid: self._network.getLinkIndex(pid) for pid in pump_ids
        }
        for pump_id, status in commands.items():
            if pump_id not in pump_name_to_link_index:
                continue
            link_index = pump_name_to_link_index[pump_id]
            value = 1 if status == "OPEN" else 0
            self._network.setLinkStatus(link_index, value)

    def step(self) -> StepResult:
        """
        Run one hydraulic solve and advance to the next step. Returns all
        per-node and per-link values for the just-computed timestep.

        Raises StopIteration if EPANET reports tstep <= 0 (sim duration
        reached — shouldn't happen for ~1 year in continuous mode).
        """
        if self._network is None or not self._sim_started:
            raise RuntimeError("Simulation not started.")

        t_sec = self._network.runHydraulicAnalysis()
        self._current_t_sec = float(t_sec)

        node_ids = self._network.getNodeNameID()
        link_ids = self._network.getLinkNameID()
        tank_ids = self._network.getNodeTankNameID()
        pump_ids = self._network.getLinkPumpNameID()
        node_id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

        pressures      = self._network.getNodePressure()
        heads          = self._network.getNodeHydraulicHead()
        demands        = self._network.getNodeActualDemand()
        elevations     = self._network.getNodeElevations()
        flows          = self._network.getLinkFlows()
        velocities     = self._network.getLinkVelocity()
        headlosses     = self._network.getLinkHeadloss()
        pump_state_raw = self._network.getLinkPumpState()

        tank_levels: dict[str, float] = {}
        for tid in tank_ids:
            i = node_id_to_idx.get(tid)
            if i is None:
                continue
            tank_levels[tid] = float(heads[i]) - float(elevations[i])

        result = StepResult(
            sim_time_sec=float(t_sec),
            pressures={nid: float(pressures[i])   for i, nid in enumerate(node_ids)},
            heads={nid: float(heads[i])           for i, nid in enumerate(node_ids)},
            demands={nid: float(demands[i])       for i, nid in enumerate(node_ids)},
            flows={lid: float(flows[i])           for i, lid in enumerate(link_ids)},
            velocities={lid: float(velocities[i]) for i, lid in enumerate(link_ids)},
            headlosses={lid: float(headlosses[i]) for i, lid in enumerate(link_ids)},
            pump_states={
                pid: ("OPEN" if int(pump_state_raw[i]) > 0 else "CLOSED")
                for i, pid in enumerate(pump_ids)
            },
            tank_levels=tank_levels,
        )

        tstep = self._network.nextHydraulicAnalysisStep()
        if tstep <= 0:
            raise StopIteration("EPANET reached end of simulation duration.")

        return result

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

    def _log_control_definitions(self) -> None:
        """Best-effort dump of simple controls + rules. Tolerant of epyt API drift."""
        try:
            controls = self._network.getControls()   # type: ignore
            print("[init] Simple controls:")
            if isinstance(controls, dict):
                for k, v in controls.items():
                    print(f"  {k}: {getattr(v, 'Control', v)}")
            elif isinstance(controls, (list, tuple)):
                for c in controls:
                    print(f"  {getattr(c, 'Control', c)}")
            else:
                print(f"  (count={controls})")
        except Exception as exc:
            print(f"[init] WARN: could not enumerate controls: {exc!r}")
        try:
            rules = self._network.getRules()      # type: ignore
            print("[init] Rule-based controls:")
            if isinstance(rules, dict):
                for k, v in rules.items():
                    print(f"  {k}: {getattr(v, 'Rule', v)}")
            elif isinstance(rules, (list, tuple)):
                for r in rules:
                    print(f"  {getattr(r, 'Rule', r)}")
            else:
                print(f"  (count={rules})")
        except Exception as exc:
            print(f"[init] WARN: could not enumerate rules: {exc!r}")

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
