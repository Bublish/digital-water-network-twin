"""
SimulationRunner — singleton that owns the long-running EPANET state.

Created once at FastAPI startup; stashed on app.state.runner.

State machine:
    NOT_STARTED --start()--> RUNNING --stop()--> STOPPED
                                |                    |
                                +--- start() --------+

All access to the underlying EPANETSimulator happens under self._lock.
GET /sim/state reads self._cached_state without locking.
"""
import asyncio
import logging
from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
from typing import Any

import httpx

from simulation.types import PumpMode, SimStatus, StepResult, StepState

logger = logging.getLogger(__name__)

STEPS_PER_DAY = 96  # 24 hours / 15 minutes
SECONDS_PER_STEP = 900
ML_TIMEOUT_SEC = 2.0
HYDRAULIC_RETRY_LIMIT = 3


class SimulationRunner:
    def __init__(
        self,
        sim: Any,                       # EPANETSimulator-shaped; injectable for tests
        db: Any,                        # SupabaseDB-shaped
        http_client: httpx.AsyncClient,
        ml_url: str = "http://localhost:8000/ml/predict",
    ) -> None:
        self._sim = sim
        self._db = db
        self._http = http_client
        self._ml_url = ml_url

        self._lock = asyncio.Lock()
        self._status: SimStatus = SimStatus.NOT_STARTED
        self._sim_id: str | None = None
        self._step_idx: int = 0
        self._time_scale: int = 1
        self._pump_modes: dict[str, PumpMode] = {}
        self._cached_state: dict | None = None
        self._tick_task: asyncio.Task | None = None
        self._last_ml_commands: dict[str, str] | None = None
        self._hydraulic_failures: int = 0
        self._last_sim_hr: float = 0.0
        self._subscribers: set[asyncio.Queue] = set()

    # ---------------- public accessors ----------------

    @property
    def status(self) -> SimStatus:
        return self._status

    @property
    def sim_id(self) -> str | None:
        return self._sim_id

    @property
    def pump_modes(self) -> dict[str, PumpMode]:
        return dict(self._pump_modes)

    def set_override(self, pump_id: str, mode: PumpMode) -> dict[str, PumpMode]:
        """
        Set HAND/AUTO mode for one pump. Effective from the next tick.
        Safe to call when NOT_STARTED — modes are kept in memory.
        """
        self._pump_modes[pump_id] = mode
        return dict(self._pump_modes)

    def subscribe(self, q: asyncio.Queue) -> None:
        """Register a queue to receive cached_state dicts after each tick."""
        self._subscribers.add(q)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a queue from the broadcast set. Safe if not registered."""
        self._subscribers.discard(q)

    def _broadcast(self, state: dict) -> None:
        """Push state to every subscriber. Drop for slow consumers (no blocking)."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(state)
            except asyncio.QueueFull:
                pass

    def _resolve_overrides(self, ml_commands: dict[str, str]) -> dict[str, str]:
        """HAND_OPEN/HAND_CLOSED win over the ML command; AUTO uses ML."""
        out: dict[str, str] = {}
        for pump_id, mode in self._pump_modes.items():
            if mode == PumpMode.HAND_OPEN:
                out[pump_id] = "OPEN"
            elif mode == PumpMode.HAND_CLOSED:
                out[pump_id] = "CLOSED"
            else:
                out[pump_id] = ml_commands.get(pump_id, "CLOSED")
        return out

    # ---------------- lifecycle ----------------

    async def start(self, time_scale: int) -> str:
        if self._status == SimStatus.RUNNING:
            raise RuntimeError("Simulation already running.")

        async with self._lock:
            self._sim_id = self._sim.start_simulation()
            self._step_idx = 0
            self._time_scale = time_scale
            self._hydraulic_failures = 0

            # Initialize pump_modes = AUTO for every pump in the network
            state: StepState = self._sim.read_state()
            self._pump_modes = {p: PumpMode.AUTO for p in state.pump_states}
            self._status = SimStatus.RUNNING

        self._tick_task = asyncio.create_task(self._run_forever())
        logger.info(f"SimulationRunner started; sim_id={self._sim_id}")
        return self._sim_id

    async def stop(self) -> None:
        if self._status != SimStatus.RUNNING:
            return

        self._status = SimStatus.STOPPED
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None

        async with self._lock:
            self._sim.stop_simulation()

        logger.info("SimulationRunner stopped.")

    # ---------------- tick loop ----------------

    async def _run_forever(self) -> None:
        interval = SECONDS_PER_STEP / max(self._time_scale, 1)
        try:
            while self._status == SimStatus.RUNNING:
                await self.tick()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Tick loop crashed; stopping runner.")
            self._status = SimStatus.STOPPED

    async def tick(self) -> None:
        if self._status != SimStatus.RUNNING:
            return

        async with self._lock:
            # 1. Rotate demand pattern on day boundaries (including step 0)
            if self._step_idx % STEPS_PER_DAY == 0:
                self._sim.rotate_demand_pattern()

            # 2. Read state for the predict request
            state: StepState = self._sim.read_state()

            # 3. Build PredictRequest and call ML
            ml_commands, model_id = await self._call_ml(state)

            # 4. Resolve overrides (HAND wins AUTO)
            final_commands = self._resolve_overrides(ml_commands)

            # 5. Apply and solve
            try:
                self._sim.apply_pump_commands(final_commands)
                result: StepResult = self._sim.step()
                self._hydraulic_failures = 0
            except StopIteration:
                logger.error("EPANET reached end of simulation duration.")
                self._status = SimStatus.STOPPED
                return
            except Exception:
                self._hydraulic_failures += 1
                logger.exception(f"Hydraulic solve failed "
                                 f"(failure #{self._hydraulic_failures}/{HYDRAULIC_RETRY_LIMIT})")
                if self._hydraulic_failures >= HYDRAULIC_RETRY_LIMIT:
                    self._status = SimStatus.STOPPED
                return

            # 6. Persist
            await self._persist(result, ml_commands, final_commands, model_id)

            # 7. Cache state for GET /sim/state
            self._cached_state = self._build_cached_state(result)
            self._broadcast(self._cached_state)
            self._last_sim_hr = result.sim_time_sec / 3600.0
            self._step_idx += 1

    async def _call_ml(self, state: StepState) -> tuple[dict[str, str], str]:
        """POST to /ml/predict. On any failure, fall back to last commands."""
        payload = {
            "sim_time_hr":   state.sim_time_sec / 3600.0,
            "tank_levels":   state.tank_levels,
            "pump_modes":    {p: m.value for p, m in self._pump_modes.items()},
            "current_price": None,
        }
        try:
            resp = await self._http.post(self._ml_url, json=payload, timeout=ML_TIMEOUT_SEC)
            resp.raise_for_status()
            body = resp.json()
            commands = body["commands"]
            if set(commands.keys()) != set(self._pump_modes.keys()):
                raise ValueError("ML returned mismatched pump set")
            self._last_ml_commands = commands
            return commands, body.get("model_id", "unknown")
        except Exception as exc:
            logger.warning(f"ML predict failed: {exc!r}; using fallback")
            fallback = self._last_ml_commands or {p: "CLOSED" for p in self._pump_modes}
            return fallback, "stub-v0+fallback"

    async def _persist(
        self,
        result: StepResult,
        ml_commands: dict[str, str],
        final_commands: dict[str, str],
        model_id: str,
    ) -> None:
        sim_hr = result.sim_time_sec / 3600.0

        node_rows = [
            {"run_id": self._sim_id, "sim_hour": sim_hr, "node_id": nid,
             "pressure_psi": p, "head_ft": result.heads.get(nid, 0.0),
             "demand_gpm": result.demands.get(nid, 0.0)}
            for nid, p in result.pressures.items()
        ]
        link_rows = [
            {"run_id": self._sim_id, "sim_hour": sim_hr, "link_id": lid,
             "flow_gpm": f, "velocity_fps": result.velocities.get(lid, 0.0),
             "headloss_ft_per_kft": result.headlosses.get(lid, 0.0)}
            for lid, f in result.flows.items()
        ]
        decision_rows = [
            {"sim_id": self._sim_id, "sim_time_hr": sim_hr, "pump_id": pump_id,
             "ml_commanded": ml_commands.get(pump_id, "CLOSED"),
             "applied_status": final_commands[pump_id],
             "mode": self._pump_modes[pump_id].value,
             "model_id": model_id, "current_price": None, "explanation": None}
            for pump_id in self._pump_modes
        ]

        try:
            self._db.insert_live_node_results(node_rows)
            self._db.insert_live_link_results(link_rows)
            self._db.insert_control_decision(decision_rows)
        except Exception:
            logger.exception("Supabase insert failed; dropping rows for this step")

    def _build_cached_state(self, result: StepResult) -> dict:
        return {
            "status":       self._status.value,
            "sim_id":       self._sim_id,
            "sim_time_hr":  result.sim_time_sec / 3600.0,
            "wall_time":    _now_iso(),
            "time_scale":   self._time_scale,
            "tank_levels":  result.tank_levels,
            "pump_states":  result.pump_states,
            "pump_modes":   {p: m.value for p, m in self._pump_modes.items()},
            "last_step_at": _now_iso(),
        }

    def get_cached_state(self) -> dict:
        if self._cached_state is None:
            return {
                "status":       self._status.value,
                "sim_id":       self._sim_id,
                "sim_time_hr":  0.0,
                "wall_time":    _now_iso(),
                "time_scale":   self._time_scale,
                "tank_levels":  {},
                "pump_states":  {},
                "pump_modes":   {p: m.value for p, m in self._pump_modes.items()},
                "last_step_at": None,
            }
        return dict(self._cached_state)

    @property
    def last_sim_hr(self) -> float:
        return self._last_sim_hr
