"""
Prediction engine HTTP routes. Reads the PredictionService from
app.state.predictor; heavy per-node computation runs in a worker thread.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    NodeListResponse, NodePrediction, PredictionStatus, ShapResponse,
)

router = APIRouter(prefix="/prediction")


def _svc(request: Request):
    return request.app.state.predictor


def _run_id(request: Request):
    runner = getattr(request.app.state, "runner", None)
    return getattr(runner, "sim_id", None) if runner else None


def _require_ready(svc):
    if not svc.is_ready:
        raise HTTPException(status_code=503, detail={"state": svc.status()["state"]})


@router.get("/status", response_model=PredictionStatus)
async def status(request: Request) -> PredictionStatus:
    return PredictionStatus(**_svc(request).status())


@router.get("/nodes", response_model=NodeListResponse)
async def nodes(request: Request) -> NodeListResponse:
    return NodeListResponse(nodes=_svc(request).nodes())


@router.get("/node/{node_id}", response_model=NodePrediction)
async def node(request: Request, node_id: str) -> NodePrediction:
    svc = _svc(request)
    _require_ready(svc)
    try:
        data = await asyncio.to_thread(svc.node_prediction, node_id, _run_id(request))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown node '{node_id}'")
    return NodePrediction(**data)


@router.get("/node/{node_id}/shap", response_model=ShapResponse)
async def node_shap(request: Request, node_id: str) -> ShapResponse:
    svc = _svc(request)
    _require_ready(svc)
    try:
        data = await asyncio.to_thread(svc.node_shap, node_id, _run_id(request))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown node '{node_id}'")
    return ShapResponse(**data)
