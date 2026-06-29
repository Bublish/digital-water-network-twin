from unittest.mock import MagicMock

import numpy as np

from app.simulation.Simulator import EPANETSimulator


def _bare_sim():
    """An EPANETSimulator with __init__ bypassed and a mocked epyt network."""
    sim = EPANETSimulator.__new__(EPANETSimulator)
    sim._network = MagicMock()
    sim._base_demands = [10.0, 20.0, 30.0]
    sim._base_pattern = [1.0] * 96
    sim._pattern0_index = 1
    sim._pattern_rotations = 0
    sim._current_pattern_id = None
    sim._current_pattern_multipliers = None
    return sim


def test_first_rotate_is_randomised_not_raw():
    sim = _bare_sim()
    np.random.seed(0)
    sim.rotate_demand_pattern()

    # setPattern called once; the installed multipliers must differ from raw base
    assert sim._network.setPattern.call_count == 1
    installed = sim._network.setPattern.call_args[0][1]
    assert len(installed) == 96
    assert installed != [1.0] * 96
    # per-node base demands were re-randomised too
    sim._network.setNodeBaseDemands.assert_called_once()


def test_compute_junction_features_maps_elev_and_base_demand():
    sim = EPANETSimulator.__new__(EPANETSimulator)
    net = MagicMock()
    net.getNodeNameID.return_value = ["J1", "TANK-1", "J2"]
    net.getNodeJunctionNameID.return_value = ["J1", "J2"]
    net.getNodeElevations.return_value = [100.0, 0.0, 120.0]
    net.getNodeBaseDemands.return_value = {1: [20.0, 0.0, 15.0]}
    sim._network = net

    feats = sim.compute_junction_features()
    assert set(feats) == {"J1", "J2"}
    assert feats["J1"] == {"elev": 100.0, "base_demand": 20.0}
    assert feats["J2"] == {"elev": 120.0, "base_demand": 15.0}


def test_seed_uses_step_sec_for_timesteps():
    sim = EPANETSimulator.__new__(EPANETSimulator)
    net = MagicMock()
    net.getComputedHydraulicTimeSeries.return_value = MagicMock(Time=[])
    net.getNodeNameID.return_value = []
    net.getLinkNameID.return_value = []
    sim._network = net
    sim._db = MagicMock()

    sim.seed(days=4, step_sec=900)

    net.setTimePatternStep.assert_called_with(900)
    net.setTimeHydraulicStep.assert_called_with(900)
    net.setTimeReportingStep.assert_called_with(900)
    net.setTimeSimulationDuration.assert_called_with(4 * 24 * 900)
