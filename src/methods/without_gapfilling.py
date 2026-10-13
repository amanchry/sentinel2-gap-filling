"""No gap-filling: the per-period median of clear observations, gaps left empty.

This is the baseline every other method is compared against.
"""

from __future__ import annotations

import numpy as np

from .base import OBSERVED, FillResult, GapFillMethod, period_median


class WithoutGapFilling(GapFillMethod):
    name = "without_gapfilling"

    def fill(self, values, obs_days, periods) -> FillResult:
        n_per = len(periods)
        _, ny, nx = values.shape
        out = np.full((n_per, ny, nx), np.nan, dtype=np.float32)
        flag = np.zeros((n_per, ny, nx), dtype=np.uint8)
        for p, period in enumerate(periods):
            out[p] = period_median(values, obs_days, period)
            flag[p][np.isfinite(out[p])] = OBSERVED
        return FillResult(out, flag, np.zeros((n_per, ny, nx), dtype=np.uint16))
