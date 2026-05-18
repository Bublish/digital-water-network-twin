"""
Server-rendered page routes (Jinja2). All three nav pages live here.
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    runner = request.app.state.runner
    initial_sim_state = runner.get_cached_state()
    network_info = request.app.state.network_info
    raw_tanks = network_info.get("tanks") if isinstance(network_info, dict) else getattr(network_info, "tanks", {})
    raw_tanks = raw_tanks or {}
    def _get(t, k, default=0.0):
        return t.get(k, default) if isinstance(t, dict) else getattr(t, k, default)
    tanks_meta = {
        tid: {"min_level_ft": _get(t, "min_level_ft"),
              "max_level_ft": _get(t, "max_level_ft")}
        for tid, t in raw_tanks.items()
    }
    return request.app.state.templates.TemplateResponse(
        request, "overview.html",
        {
            "active":                "overview",
            "network_info":          network_info,
            "initial_sim_state":     initial_sim_state,
            "network_plot_svg_light": request.app.state.network_plot_svg_light,
            "network_plot_svg_dark":  request.app.state.network_plot_svg_dark,
            "network_geometry_json": json.dumps(request.app.state.network_geometry),
            "tanks_meta_json":       json.dumps(tanks_meta),
        },
    )


@router.get("/pump-control", response_class=HTMLResponse)
async def pump_control(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "pump_control.html", {"active": "pump"},
    )


@router.get("/xai", response_class=HTMLResponse)
async def xai(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "xai.html", {"active": "xai"},
    )
