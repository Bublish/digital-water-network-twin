"""
Stub /ml/predict endpoint. Replaced by the real model after the ML brainstorm.

The stub defers every pump to the EPANET network's own rule-based controls
by emitting "NOP" — the runner forwards this verbatim and the simulator
skips setLinkStatus for NOP entries, leaving the .inp rules in charge.
"""
from fastapi import APIRouter

from app.api.schemas import PredictRequest, PredictResponse

router = APIRouter()


@router.post("/ml/predict", response_model=PredictResponse)
async def predict(req: PredictRequest) -> PredictResponse:
    commands = {pump_id: "NOP" for pump_id in req.pump_modes}
    return PredictResponse(commands=commands, model_id="stub-v0")
