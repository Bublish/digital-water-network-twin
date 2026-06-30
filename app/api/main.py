"""
FastAPI app factory.

Run from the repo root:
    uvicorn app.api.main:app --reload
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.ml_routes import router as ml_router
from app.api.network_routes import router as network_router
from app.api.prediction_routes import router as prediction_router
from app.api.pricing_routes import router as pricing_router
from app.api.sim_routes import router as sim_router
from app.api.web_routes import router as web_router
from app.db.SupabaseClient import SupabaseDB
from app.ml.PredictionService import PredictionService
from app.ml.PressurePredictor import PressurePredictor
from app.pricing.PricingEngine import PricingEngine
from app.scheduler.SimulationRunner import SimulationRunner
from app.simulation.Simulator import EPANETSimulator
from app.simulation.types import SimStatus

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

APP_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_ROOT / "web" / "templates"
STATIC_DIR = APP_ROOT / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim = EPANETSimulator()
    sim.load()
    db = SupabaseDB()
    http_client = httpx.AsyncClient()

    app.state.pricing = PricingEngine(http_client=http_client)
    app.state.runner = SimulationRunner(
        sim=sim, db=db, http_client=http_client,
        pricing=app.state.pricing,
        ml_url="http://localhost:8000/ml/predict",
    )
    app.state.network_info = sim.compute_network_info()

    def _seeder() -> None:
        # epyt/EPANET keeps ONE global project per process (ph=False, which is
        # required — ph=True breaks the hydraulic stepping interface in epyt
        # 2.3.5). A second in-process EPANETSimulator therefore clobbers the live
        # sim's network and, on close, frees it. Run the seed in its own process
        # so it gets an independent EPANET project and cannot touch the live sim.
        import multiprocessing as mp

        from app.simulation.seed_worker import run_seed

        proc = mp.get_context("spawn").Process(
            target=run_seed, args=(4, 900), name="epanet-seed",
        )
        proc.start()
        proc.join()
        if proc.exitcode != 0:
            raise RuntimeError(f"Seed subprocess failed (exitcode={proc.exitcode})")

    app.state.predictor = PredictionService(
        db=db,
        predictor=PressurePredictor(),
        static_features=sim.compute_junction_features(),
        seeder=_seeder,
    )
    training_task = asyncio.create_task(app.state.predictor.train_in_background())

    svg_light, geometry = sim.render_plot_svg(theme="light")
    svg_dark,  _        = sim.render_plot_svg(theme="dark")
    app.state.network_plot_svg_light = svg_light
    app.state.network_plot_svg_dark  = svg_dark
    app.state.network_plot_svg       = svg_light  # back-compat alias
    app.state.network_geometry       = geometry
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    try:
        yield
    finally:
        if app.state.runner.status == SimStatus.RUNNING:
            await app.state.runner.stop()
        training_task.cancel()
        try:
            await training_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await http_client.aclose()
        sim.close()
        # ============================================================
        # DEBUG: clear live tables on shutdown so test runs don't pile up.
        # REMOVE THIS BLOCK BEFORE GOING TO PRODUCTION.
        # ============================================================
        try:
            db.clear_live_tables()
            logging.info("Cleared live_* and control_decisions tables on shutdown.")
        except Exception:
            logging.exception("Failed to clear live tables on shutdown.")


app = FastAPI(title="SCADA Simulation API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(sim_router)
app.include_router(ml_router)
app.include_router(network_router)
app.include_router(pricing_router)
app.include_router(prediction_router)
app.include_router(web_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
