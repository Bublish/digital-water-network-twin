"""
Debug / test runner for EPANETSimulator.

Run from the Backend/ directory:

"""

import argparse
import sys

from db.SupabaseClient import SupabaseDB
from simulation.Simulator import EPANETSimulator



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    _sim = EPANETSimulator()
    _sim.load()
    try:
        _sim.run_live()
    except KeyboardInterrupt:
        print("\n[main] Stopped by user.")
    finally:
        _sim.close()





if __name__ == "__main__":
    main()
