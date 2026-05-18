"""
/pricing/* HTTP endpoints. Thin layer over PricingEngine.
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request

router = APIRouter(prefix="/pricing")


def _engine(request: Request):
    return request.app.state.pricing


def _runner(request: Request):
    return request.app.state.runner


def _current_sim_dt(request: Request) -> datetime:
    """Derive the simulation's current wall-clock-equivalent datetime."""
    engine = _engine(request)
    runner = _runner(request)
    anchor = engine._anchor_utc  # set on /sim/start
    if anchor is None:
        return datetime.now(UTC)
    sim_time_hr = runner.last_sim_hr
    return anchor + timedelta(hours=sim_time_hr)


@router.get("/current")
async def current_price(request: Request) -> dict:
    engine = _engine(request)
    sim_dt = _current_sim_dt(request)
    price_eur_per_kwh = engine.get_current_price(sim_dt)
    return {
        "sim_dt_utc":        sim_dt.isoformat(),
        "price_eur_per_kwh": price_eur_per_kwh,
        "price_eur_per_mwh": price_eur_per_kwh * 1000.0,
        "enabled":           engine.enabled,
        "flat_rate_fallback": price_eur_per_kwh == engine.flat_rate,
        "license_info":      engine.license_info,
    }


@router.get("/forecast")
async def forecast(request: Request) -> dict:
    engine = _engine(request)
    return {
        "enabled":      engine.enabled,
        "license_info": engine.license_info,
        "slots":        engine.forecast(),
    }
