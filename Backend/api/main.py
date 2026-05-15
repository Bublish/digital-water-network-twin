"""
FastAPI app factory.

Run from Backend/:
    uvicorn api.main:app --reload
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.ml_routes import router as ml_router
from api.network_routes import router as network_router
from api.sim_routes import router as sim_router
from api.web_routes import router as web_router
from db.SupabaseClient import SupabaseDB
from scheduler.SimulationRunner import SimulationRunner
from simulation.Simulator import EPANETSimulator
from simulation.types import SimStatus

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BACKEND_ROOT / "templates"
STATIC_DIR = BACKEND_ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim = EPANETSimulator()
    sim.load()
    db = SupabaseDB()
    http_client = httpx.AsyncClient()

    app.state.runner = SimulationRunner(
        sim=sim, db=db, http_client=http_client,
        ml_url="http://localhost:8000/ml/predict",
    )
    app.state.network_info = sim.compute_network_info()
    app.state.network_plot_png = sim.render_plot_png()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    try:
        yield
    finally:
        if app.state.runner.status == SimStatus.RUNNING:
            await app.state.runner.stop()
        await http_client.aclose()
        sim.close()


app = FastAPI(title="SCADA Simulation API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(sim_router)
app.include_router(ml_router)
app.include_router(network_router)
app.include_router(web_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
