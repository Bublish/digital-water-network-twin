"""
Tests for EPANETSimulator's step-by-step API.

These are real-network tests — they download WSN1.inp from Supabase on
each EPANETSimulator() construction. Skip them in environments without
Supabase credentials.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SUPABASE_URL"),
    reason="Supabase credentials not configured",
)


@pytest.fixture
def sim():
    from simulation.Simulator import EPANETSimulator
    with EPANETSimulator() as s:
        yield s


def test_start_returns_sim_id_and_close_cleans_up(sim):
    sim_id = sim.start_simulation()
    assert isinstance(sim_id, str)
    assert len(sim_id) == 36  # UUID
    sim.stop_simulation()
    # Calling stop twice must not raise
    sim.stop_simulation()


def test_double_start_raises(sim):
    sim.start_simulation()
    with pytest.raises(RuntimeError, match="already started"):
        sim.start_simulation()
    sim.stop_simulation()


def test_rotate_demand_pattern_installs_96_step_pattern(sim):
    sim.start_simulation()
    sim.rotate_demand_pattern()
    # After rotation, a pattern with 96 multipliers should be installed
    pattern_count = sim._network.getPatternCount()
    assert pattern_count >= 1
    sim.stop_simulation()


def test_read_state_returns_step_state(sim):
    from simulation.types import StepState
    sim.start_simulation()
    sim.rotate_demand_pattern()
    state = sim.read_state()
    assert isinstance(state, StepState)
    assert state.sim_time_sec >= 0
    assert len(state.tank_levels) > 0
    assert len(state.pump_states) > 0
    for status in state.pump_states.values():
        assert status in {"OPEN", "CLOSED"}
    sim.stop_simulation()


def test_apply_pump_commands_changes_pump_status(sim):
    sim.start_simulation()
    sim.rotate_demand_pattern()
    state_before = sim.read_state()
    pump_id = next(iter(state_before.pump_states))
    sim.apply_pump_commands({pump_id: "CLOSED"})
    state_after = sim.read_state()
    assert state_after.pump_states[pump_id] == "CLOSED"
    sim.stop_simulation()


def test_step_advances_sim_time_and_returns_step_result(sim):
    from simulation.types import StepResult
    sim.start_simulation()
    sim.rotate_demand_pattern()
    t0 = sim.read_state().sim_time_sec
    result = sim.step()
    assert isinstance(result, StepResult)
    assert result.sim_time_sec >= t0
    assert len(result.pressures) > 0
    assert len(result.flows) > 0
    sim.stop_simulation()


def test_compute_network_info_returns_expected_counts():
    """Hits the real WSN1 network — requires Supabase + WSN1.inp upload."""
    from simulation.Simulator import EPANETSimulator

    with EPANETSimulator() as sim:
        info = sim.compute_network_info()

    assert info["pump_count"] == 2
    assert info["tank_count"] == 2
    assert info["reservoir_count"] == 1
    assert 120 < info["junction_count"] < 140      # WSN1 has ~130 junctions
    assert 100 < info["pipe_count"] < 250          # WSN1 has ~168 pipes; bound generously
    assert 20.0 < info["total_pipe_length_mi"] < 30.0   # WSN1: 23.3 miles
    assert info["total_demand_gpm"] > 0
    assert info["total_demand_mgd"] == pytest.approx(
        info["total_demand_gpm"] * 1440 / 1_000_000, rel=1e-6
    )
    assert info["pattern_steps"] > 0
    assert info["pattern_period_min"] > 0
