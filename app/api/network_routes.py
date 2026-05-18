"""
/network/* HTTP endpoints. Serve cached topology data computed once at startup.
"""
from fastapi import APIRouter, Request, Response

from app.api.schemas import NetworkInfo

router = APIRouter(prefix="/network")


@router.get("/info", response_model=NetworkInfo)
async def get_info(request: Request) -> NetworkInfo:
    return NetworkInfo(**request.app.state.network_info)


@router.get("/plot.svg")
async def get_plot_svg(request: Request) -> Response:
    return Response(
        content=request.app.state.network_plot_svg,
        media_type="image/svg+xml",
    )
