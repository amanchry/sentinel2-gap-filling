"""Registry of gap-filling methods. Register new methods in METHODS."""

from __future__ import annotations

from .base import EDGE, FILLED, NO_VALUE, OBSERVED, FillResult, GapFillMethod
from .linear import LinearInterpolation
from .without_gapfilling import WithoutGapFilling

METHODS: dict[str, type[GapFillMethod]] = {
    WithoutGapFilling.name: WithoutGapFilling,
    LinearInterpolation.name: LinearInterpolation,
}


def create(name: str, params: dict) -> GapFillMethod:
    if name not in METHODS:
        raise SystemExit(f"Unknown method '{name}'. Available: {sorted(METHODS)}")
    return METHODS[name](**params)


__all__ = ["METHODS", "create", "FillResult", "GapFillMethod", "NO_VALUE", "OBSERVED", "FILLED", "EDGE"]
