"""Linear temporal interpolation.

Open-source port of the Spatial Thoughts Earth Engine recipe
(https://spatialthoughts.com/2021/11/08/temporal-interpolation-gee/):
for each pixel take the nearest clear observation before and after the
target time, within max_gap_days, and interpolate linearly between them.

Per output period:
  * clear observations inside the period -> their median (flag 1)
  * otherwise -> linear interpolation at the period centre from the nearest
    clear observation before and after it, each within max_gap_days (flag 2)
  * edge="nearest": if only one side exists within max_gap_days, copy it (flag 3)
"""

from __future__ import annotations

import numpy as np

from .base import EDGE, FILLED, NO_VALUE, OBSERVED, FillResult, GapFillMethod, period_median


class LinearInterpolation(GapFillMethod):
    name = "linear"

    def __init__(self, max_gap_days: float = 45, edge: str = "leave_empty"):
        if edge not in ("leave_empty", "nearest"):
            raise ValueError("edge must be 'leave_empty' or 'nearest'")
        super().__init__(max_gap_days=max_gap_days, edge=edge)
        self.max_gap_days = float(max_gap_days)
        self.edge = edge

    def fill(self, values, obs_days, periods) -> FillResult:
        n_obs, ny, nx = values.shape
        n_per = len(periods)
        out = np.full((n_per, ny, nx), np.nan, dtype=np.float32)
        flag = np.zeros((n_per, ny, nx), dtype=np.uint8)
        gap = np.zeros((n_per, ny, nx), dtype=np.uint16)
        if n_obs == 0:
            return FillResult(out, flag, gap)

        days = np.asarray(obs_days, dtype=np.float64)
        valid = np.isfinite(values)
        # int16 slot indices keep these two cubes at half the size of the values.
        steps = np.arange(n_obs, dtype=np.int16)[:, None, None]
        # For every slot t: index of the last clear observation at or before t,
        # and of the first clear observation at or after t (per pixel).
        last_before = np.maximum.accumulate(np.where(valid, steps, np.int16(-1)), axis=0)
        first_after = np.minimum.accumulate(np.where(valid, steps, np.int16(n_obs))[::-1], axis=0)[::-1]
        rows, cols = np.indices((ny, nx))

        for p, period in enumerate(periods):
            observed = period_median(values, obs_days, period)
            has_obs = np.isfinite(observed)
            out[p][has_obs] = observed[has_obs]
            flag[p][has_obs] = OBSERVED

            centre = period.center
            k_prev = int(np.searchsorted(days, centre, side="right")) - 1
            k_next = int(np.searchsorted(days, centre, side="left"))

            i_prev = last_before[k_prev] if k_prev >= 0 else np.full((ny, nx), -1, np.int16)
            i_next = first_after[k_next] if k_next < n_obs else np.full((ny, nx), n_obs, np.int16)
            ok_prev = i_prev >= 0
            ok_next = i_next < n_obs
            d_prev = np.where(ok_prev, days[np.clip(i_prev, 0, n_obs - 1)], np.nan)
            d_next = np.where(ok_next, days[np.clip(i_next, 0, n_obs - 1)], np.nan)
            ok_prev &= (centre - d_prev) <= self.max_gap_days
            ok_next &= (d_next - centre) <= self.max_gap_days
            v_prev = values[np.clip(i_prev, 0, n_obs - 1), rows, cols]
            v_next = values[np.clip(i_next, 0, n_obs - 1), rows, cols]

            todo = ~has_obs
            both = todo & ok_prev & ok_next
            with np.errstate(invalid="ignore", divide="ignore"):
                span = d_next - d_prev
                weight = np.where(span > 0, (centre - d_prev) / span, 0.0)
            interp = v_prev + weight * (v_next - v_prev)
            out[p][both] = interp[both]
            flag[p][both] = FILLED
            gap[p][both] = np.minimum(span[both], 65535).astype(np.uint16)

            if self.edge == "nearest":
                only_prev = todo & ok_prev & ~ok_next
                only_next = todo & ok_next & ~ok_prev
                out[p][only_prev] = v_prev[only_prev]
                out[p][only_next] = v_next[only_next]
                flag[p][only_prev | only_next] = EDGE
                gap[p][only_prev] = np.minimum(centre - d_prev[only_prev], 65535).astype(np.uint16)
                gap[p][only_next] = np.minimum(d_next[only_next] - centre, 65535).astype(np.uint16)

        flag[~np.isfinite(out)] = NO_VALUE
        return FillResult(out, flag, gap)
