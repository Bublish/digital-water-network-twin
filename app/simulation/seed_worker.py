"""Run an EPANET seed EPS in an isolated child process.

epyt exposes a single global EPANET project per OS process when project handles
are disabled (ph=False), and ph=False is required: the handle mode (ph=True)
breaks epyt's hydraulic stepping interface in v2.3.5 (every step returns
tstep=0). The consequence is that two EPANETSimulator instances in one process
share — and fight over — one global network. The second instance's load
overwrites the first, and its close() frees the shared project, leaving the live
simulator with "no network data available".

The training pipeline needs a throwaway simulator to generate seed data. Running
it here, in a process spawned by the live server, gives the seed its own EPANET
project so the live simulator's network is untouched.
"""
from app.simulation.Simulator import EPANETSimulator


def run_seed(days: int = 4, step_sec: int = 900) -> None:
    """Construct a standalone simulator and write `days` of seed data to Supabase.

    Intended to be the target of a spawned multiprocessing.Process, so it must
    stay importable and side-effect free at module scope.
    """
    with EPANETSimulator() as sim:
        sim.seed(days=days, step_sec=step_sec)
