"""Spectral index definitions.

Inputs are surface reflectance (0-1) arrays keyed by earth-search asset name:
blue (B02), green (B03), red (B04), nir (B08), swir16 (B11), swir22 (B12).
To add an index, add one entry to INDICES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


def _nd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def _evi(r: dict) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 2.5 * (r["nir"] - r["red"]) / (r["nir"] + 6.0 * r["red"] - 7.5 * r["blue"] + 1.0)


@dataclass(frozen=True)
class Index:
    name: str
    bands: tuple
    func: Callable[[dict], np.ndarray]
    valid_range: tuple
    description: str
    cmap: str = "RdYlGn"
    display_range: tuple = (-0.1, 0.9)


INDICES = {
    "NDVI": Index(
        "NDVI", ("red", "nir"), lambda r: _nd(r["nir"], r["red"]), (-1.0, 1.0),
        "(B08 - B04) / (B08 + B04)",
    ),
    "NDWI": Index(
        "NDWI", ("green", "nir"), lambda r: _nd(r["green"], r["nir"]), (-1.0, 1.0),
        "McFeeters: (B03 - B08) / (B03 + B08)", cmap="RdBu", display_range=(-0.8, 0.4),
    ),
    "NDMI": Index(
        "NDMI", ("nir", "swir16"), lambda r: _nd(r["nir"], r["swir16"]), (-1.0, 1.0),
        "(B08 - B11) / (B08 + B11)", cmap="BrBG", display_range=(-0.4, 0.6),
    ),
    "EVI": Index(
        "EVI", ("blue", "red", "nir"), _evi, (-1.0, 1.0),
        "2.5 (B08 - B04) / (B08 + 6 B04 - 7.5 B02 + 1)", display_range=(-0.1, 0.8),
    ),
    "SWIR1": Index(
        "SWIR1", ("swir16",), lambda r: r["swir16"].copy(), (0.0, 1.0),
        "B11 surface reflectance (20 m, bilinear to grid)", cmap="magma", display_range=(0.0, 0.4),
    ),
    "SWIR2": Index(
        "SWIR2", ("swir22",), lambda r: r["swir22"].copy(), (0.0, 1.0),
        "B12 surface reflectance (20 m, bilinear to grid)", cmap="magma", display_range=(0.0, 0.35),
    ),
}

# Bands that come in at 20 m and are resampled, everything else is native 10 m.
COARSE_BANDS = {"swir16", "swir22", "scl"}


def get(names: list[str]) -> list[Index]:
    unknown = [n for n in names if n not in INDICES]
    if unknown:
        raise SystemExit(f"Unknown index {unknown}. Available: {sorted(INDICES)}")
    return [INDICES[n] for n in names]


def required_bands(names: list[str]) -> list[str]:
    bands: list[str] = []
    for index in get(names):
        for band in index.bands:
            if band not in bands:
                bands.append(band)
    return bands


def compute(index: Index, refl: dict) -> np.ndarray:
    """Index values with NaN where any input band is missing or non-positive."""
    ok = np.ones(next(iter(refl.values())).shape, dtype=bool)
    for band in index.bands:
        ok &= np.isfinite(refl[band]) & (refl[band] > 0)
    out = index.func(refl).astype(np.float32)
    lo, hi = index.valid_range
    out[~ok | ~np.isfinite(out) | (out < lo) | (out > hi)] = np.nan
    return out
