"""
FastAPI app factory.

Run from Backend/:
    uvicorn api.main:app --reload
"""
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from api.ml_routes import router as ml_router
from api.sim_routes import router as sim_router
from db.SupabaseClient import SupabaseDB
from scheduler.SimulationRunner import SimulationRunner
from simulation.Simulator import EPANETSimulator
from simulation.types import SimStatus

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the singleton runner. The EPANETSimulator's __init__ downloads
    # WSN1.inp from Supabase; load() opens it.
    sim = EPANETSimulator()
    sim.load()
    db = SupabaseDB()
    http_client = httpx.AsyncClient()

    app.state.runner = SimulationRunner(
        sim=sim, db=db, http_client=http_client,
        ml_url="http://localhost:8000/ml/predict",
    )
    try:
        yield
    finally:
        if app.state.runner.status == SimStatus.RUNNING:
            await app.state.runner.stop()
        await http_client.aclose()
        sim.close()


app = FastAPI(title="SCADA Simulation API", lifespan=lifespan)
app.include_router(sim_router)
app.include_router(ml_router)


@app.get("/")
def root():
    return {"status": "ok", "docs": "/docs"}
