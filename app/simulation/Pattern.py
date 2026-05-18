import numpy as np
import datetime
import time
from app.simulation.Randomizer import Randomizer


class DemandPattern:
    """
    Generates demand multiplier arrays for EPANET hydraulic simulations.

    All patterns cover exactly one 24-hour cycle at 15-minute resolution (96 steps).
    Multiplier values are dimensionless factors applied to each node's base demand.
    """
    def __init__(self) -> None:
        self._id = datetime.datetime.now().isoformat()
        self._demand = Randomizer.randomize_pattern(self.sinusoidal())


    STEPS_PER_HOUR: int = 4
    HOURS_PER_CYCLE: int = 24
    TOTAL_STEPS: int = STEPS_PER_HOUR * HOURS_PER_CYCLE  # 96

    @property
    def multipliers(self) -> np.ndarray:
        """The 96-step randomized demand multiplier array for this cycle."""
        return self._demand

    @staticmethod
    def sinusoidal() -> np.ndarray:
        """
        Return 96 multiplier values sampled from a single-cycle sine wave.

        Shape:   baseline=0.9, amplitude=0.5  →  range [0.4, 1.4]
        Phase:   y(t=0) = 0.9 (sine starts at zero-crossing ascending)
        Timing:  peak at t=6 h, trough at t=18 h

        Returns
        -------
        np.ndarray, shape (96,), dtype float64
        """
        steps = np.arange(DemandPattern.TOTAL_STEPS)
        return 0.9 + 0.5 * np.sin(2 * np.pi * steps / DemandPattern.TOTAL_STEPS)
