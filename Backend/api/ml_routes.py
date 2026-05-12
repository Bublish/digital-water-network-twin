"""
Stub /ml/predict endpoint. Replaced by the real model after the ML brainstorm.

Trivial rule: if any tank is below 30 ft, open all pumps; else close all.
The point is to make the end-to-end loop work — not to be smart.
"""
from fastapi import APIRouter

from api.schemas import PredictRequest, PredictResponse

router = APIRouter()

TANK_LOW_THRESHOLD_FT = 30.0


@router.post("/ml/predict", response_model=PredictResponse)
async def predict(req: PredictRequest) -> PredictResponse:
    any_low = any(level < TANK_LOW_THRESHOLD_FT for level in req.tank_levels.values())
    decision = "OPEN" if any_low else "CLOSED"
    commands = {pump_id: decision for pump_id in req.pump_modes}
    return PredictResponse(commands=commands, model_id="stub-v0")
