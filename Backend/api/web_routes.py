"""
Server-rendered page routes (Jinja2). All three nav pages live here.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request) -> HTMLResponse:
    runner = request.app.state.runner
    initial_sim_state = runner.get_cached_state()
    return request.app.state.templates.TemplateResponse(
        request, "overview.html",
        {
            "active":            "overview",
            "network_info":      request.app.state.network_info,
            "initial_sim_state": initial_sim_state,
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
