from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_sets_predictor_and_starts_training():
    import app.api.main as main

    fake_sim = MagicMock()
    fake_sim.compute_network_info.return_value = {}
    fake_sim.render_plot_svg.return_value = ("<svg/>", {"nodes": [], "links": []})
    fake_sim.compute_junction_features.return_value = {"J1": {"elev": 1.0, "base_demand": 1.0}}

    fake_predictor_svc = MagicMock()
    fake_predictor_svc.train_in_background = AsyncMock()

    with patch.object(main, "EPANETSimulator", return_value=fake_sim), \
         patch.object(main, "SupabaseDB", return_value=MagicMock()), \
         patch.object(main, "PricingEngine", return_value=MagicMock()), \
         patch.object(main, "SimulationRunner", return_value=MagicMock()), \
         patch.object(main, "PressurePredictor", return_value=MagicMock()), \
         patch.object(main, "PredictionService", return_value=fake_predictor_svc):
        async with main.lifespan(main.app):
            assert main.app.state.predictor is fake_predictor_svc
            fake_predictor_svc.train_in_background.assert_called_once()
