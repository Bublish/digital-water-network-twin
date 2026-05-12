import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


@pytest.fixture
def mock_sim():
    m = MagicMock()
    m.start_simulation.return_value = "test-sim-id"
    m.read_state.return_value = MagicMock(
        sim_time_sec=0.0, tank_levels={"T1": 35.0}, pump_states={"P1": "CLOSED"}
    )
    m.step.return_value = MagicMock(
        sim_time_sec=900.0,
        pressures={}, heads={}, demands={}, flows={}, velocities={}, headlosses={},
        pump_states={"P1": "OPEN"}, tank_levels={"T1": 35.5},
    )
    return m


@pytest.fixture
def mock_db():
    m = MagicMock()
    return m


@pytest.mark.asyncio
async def test_start_sets_status_to_running(mock_sim, mock_db):
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import SimStatus

    runner = SimulationRunner(sim=mock_sim, db=mock_db, http_client=AsyncMock())
    assert runner.status == SimStatus.NOT_STARTED
    await runner.start(time_scale=10_000)
    assert runner.status == SimStatus.RUNNING
    assert runner.sim_id == "test-sim-id"
    await runner.stop()
    assert runner.status == SimStatus.STOPPED


@pytest.mark.asyncio
async def test_double_start_raises(mock_sim, mock_db):
    from scheduler.SimulationRunner import SimulationRunner

    runner = SimulationRunner(sim=mock_sim, db=mock_db, http_client=AsyncMock())
    await runner.start(time_scale=10_000)
    with pytest.raises(RuntimeError, match="already running"):
        await runner.start(time_scale=10_000)
    await runner.stop()


def test_resolve_overrides_hand_wins_over_ml():
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import PumpMode

    runner = SimulationRunner.__new__(SimulationRunner)
    runner._pump_modes = {
        "P1": PumpMode.AUTO,
        "P2": PumpMode.HAND_OPEN,
        "P3": PumpMode.HAND_CLOSED,
    }

    ml_commands = {"P1": "OPEN", "P2": "CLOSED", "P3": "OPEN"}
    final = runner._resolve_overrides(ml_commands)

    assert final == {"P1": "OPEN", "P2": "OPEN", "P3": "CLOSED"}


def test_set_override_mutates_pump_modes():
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import PumpMode

    runner = SimulationRunner.__new__(SimulationRunner)
    runner._pump_modes = {"P1": PumpMode.AUTO, "P2": PumpMode.AUTO}

    result = runner.set_override("P1", PumpMode.HAND_OPEN)

    assert runner._pump_modes["P1"] == PumpMode.HAND_OPEN
    assert result == {"P1": PumpMode.HAND_OPEN, "P2": PumpMode.AUTO}


@pytest.mark.asyncio
async def test_tick_calls_ml_applies_override_persists(mock_sim, mock_db):
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import PumpMode, SimStatus

    http = AsyncMock()
    http.post.return_value = MagicMock(
        json=lambda: {"commands": {"P1": "OPEN"}, "model_id": "stub-v0"},
        raise_for_status=lambda: None,
    )
    mock_sim.read_state.return_value = MagicMock(
        sim_time_sec=0.0,
        tank_levels={"T1": 35.0},
        pump_states={"P1": "CLOSED"},
    )

    runner = SimulationRunner(sim=mock_sim, db=mock_db, http_client=http)
    runner._pump_modes = {"P1": PumpMode.AUTO}
    runner._sim_id = "test-sim"
    runner._status = SimStatus.RUNNING

    await runner.tick()

    mock_sim.read_state.assert_called()
    http.post.assert_awaited_once()
    mock_sim.apply_pump_commands.assert_called_with({"P1": "OPEN"})
    mock_sim.step.assert_called_once()
    mock_db.insert_live_node_results.assert_called()
    mock_db.insert_live_link_results.assert_called()
    mock_db.insert_control_decision.assert_called()
    assert runner._step_idx == 1


@pytest.mark.asyncio
async def test_tick_falls_back_when_ml_fails(mock_sim, mock_db):
    from scheduler.SimulationRunner import SimulationRunner
    from simulation.types import PumpMode, SimStatus

    http = AsyncMock()
    http.post.side_effect = httpx.HTTPError("boom")
    mock_sim.read_state.return_value = MagicMock(
        sim_time_sec=0.0, tank_levels={"T1": 35.0}, pump_states={"P1": "OPEN"}
    )

    runner = SimulationRunner(sim=mock_sim, db=mock_db, http_client=http)
    runner._pump_modes = {"P1": PumpMode.AUTO}
    runner._sim_id = "test-sim"
    runner._status = SimStatus.RUNNING
    runner._last_ml_commands = {"P1": "OPEN"}  # previous tick's decision

    await runner.tick()

    mock_sim.apply_pump_commands.assert_called_with({"P1": "OPEN"})
    # Check the control_decision row records the fallback model_id
    call_rows = mock_db.insert_control_decision.call_args[0][0]
    assert call_rows[0]["model_id"] == "stub-v0+fallback"
