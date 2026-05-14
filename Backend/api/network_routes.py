"""
/network/* HTTP endpoints. Serve cached topology data computed once at startup.
"""
from fastapi import APIRouter, Request, Response

from api.schemas import NetworkInfo

router = APIRouter(prefix="/network")


@router.get("/info", response_model=NetworkInfo)
async def get_info(request: Request) -> NetworkInfo:
    return NetworkInfo(**request.app.state.network_info)


@router.get("/plot.png")
async def get_plot(request: Request) -> Response:
    return Response(
        content=request.app.state.network_plot_png,
        media_type="image/png",
    )
