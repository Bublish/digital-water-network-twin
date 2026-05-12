import pytest
from pydantic import ValidationError


def test_override_request_accepts_valid_mode():
    from api.schemas import OverrideRequest, PumpMode
    req = OverrideRequest(pump_id="P1", mode=PumpMode.HAND_OPEN)
    assert req.pump_id == "P1"
    assert req.mode == PumpMode.HAND_OPEN


def test_override_request_rejects_unknown_mode():
    from api.schemas import OverrideRequest
    with pytest.raises(ValidationError):
        OverrideRequest(pump_id="P1", mode="FROBNICATE")


def test_predict_response_round_trips():
    from api.schemas import PredictResponse
    r = PredictResponse(commands={"P1": "OPEN", "P2": "CLOSED"}, model_id="stub-v0")
    assert r.model_dump()["commands"]["P1"] == "OPEN"
    assert r.model_dump()["model_id"] == "stub-v0"
