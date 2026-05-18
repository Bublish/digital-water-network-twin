import numpy as np


class Randomizer:
    """
    Applies lognormal noise to EPANET demand values.

    Both methods use the same distribution: lognormal(mu, sigma) where
    sigma=0.15 gives a 15% per-node coefficient of variation and
    mu = -sigma²/2 keeps the expected multiplier at exactly 1 (no bias).
    """

    SIGMA: float = 0.15
    MU: float = -0.5 * SIGMA ** 2

    @staticmethod
    def randomize_base_demands(base_demands: list[float]) -> list[float]:
        """
        Apply an independent lognormal multiplier to each node's base demand.

        Always operates on the original base demands so noise does not
        accumulate across successive calls (no drift).
        Returns a new list; the input is not mutated.
        """
        return [
            base * np.random.lognormal(mean=Randomizer.MU, sigma=Randomizer.SIGMA)
            for base in base_demands
        ]

    @staticmethod
    def randomize_pattern(pattern: np.ndarray) -> np.ndarray:
        """
        Apply an independent lognormal multiplier to each step in a demand pattern array.

        Parameters
        ----------
        pattern : np.ndarray, shape (N,)
            Demand multiplier values to perturb (e.g. from DemandPattern.sinusoidal()).

        Returns
        -------
        np.ndarray, shape (N,)
            New array with per-step noise applied; the input is not mutated.
        """
        multipliers = np.random.lognormal(
            mean=Randomizer.MU,
            sigma=Randomizer.SIGMA,
            size=len(pattern),
        )
        return pattern * multipliers
