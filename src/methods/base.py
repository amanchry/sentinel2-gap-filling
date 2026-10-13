"""Interface every gap-filling method implements.

A method receives one index for one tile as an irregular observation cube
and returns one gap-free(ish) layer per output period. It never touches files;
the gapfill stage handles reading, writing and parallelism.

QA flag values written next to every output:
    0  no value (could not be filled)
    1  observed   - at least one clear observation inside the period
    2  filled     - estimated by the method from neighbouring observations
    3  edge fill  - estimated from one side only (method-specific fallback)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from ..periods import Period

NO_VALUE, OBSERVED, FILLED, EDGE = 0, 1, 2, 3


@dataclass
class FillResult:
    values: np.ndarray  # (n_periods, y, x) float32, NaN where flag == 0
    flag: np.ndarray  # (n_periods, y, x) uint8, codes above
    gap_days: np.ndarray  # (n_periods, y, x) uint16, days between the observations used (0 = observed)


class GapFillMethod:
    name = "base"

    def __init__(self, **params):
        self.params = params

    def fill(self, values: np.ndarray, obs_days: np.ndarray, periods: list[Period]) -> FillResult:
        """values: (n_obs, y, x) float32 with NaN for missing; obs_days: (n_obs,) day ordinals, ascending."""
        raise NotImplementedError


def period_median(values: np.ndarray, obs_days: np.ndarray, period: Period) -> np.ndarray:
    """Per-pixel median of the clear observations inside a period (NaN if none)."""
    inside = (obs_days >= period.start.toordinal()) & (obs_days <= period.end.toordinal())
    if not inside.any():
        return np.full(values.shape[1:], np.nan, dtype=np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(values[inside], axis=0).astype(np.float32)
