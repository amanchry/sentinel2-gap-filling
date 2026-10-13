#!/usr/bin/env python3
"""Gap-filled Sentinel-2 index time series. Settings live in config.py.

    conda activate gdal
    python run_pipeline.py

Stages (config.STAGES):
  extract  download clear-sky index observations per tile and solar day (cached)
  gapfill  run every method in config.METHODS on the cached observations
  mosaic   assemble tiles into one COG per method / index / period
  report   charts and CSV summaries for checking the result
Every stage is resumable: finished work is skipped on the next run.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

import config  # noqa: E402
from src import catalog, extract, gapfill, mosaic, report  # noqa: E402
from src.common import Settings, log, set_log_file  # noqa: E402
from src.grid import build_grid  # noqa: E402
from src.periods import build_periods  # noqa: E402


def main() -> None:
    settings = Settings.from_module(config, PROJECT_DIR)
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    set_log_file(settings.out_dir / "run.log")
    log(f"Python {sys.executable}")
    log(f"Stages {settings.stages} | methods {list(settings.methods)} | indices {settings.indices}")

    grid = build_grid(settings)
    periods = build_periods(settings.start, settings.end, settings.period)
    log(f"AOI {grid.aoi_ll['name'].iloc[0]}: {grid.area_km2:,.1f} km², {grid.crs}, "
        f"{grid.width} x {grid.height} px at {settings.resolution_m:g} m, {len(grid.tiles)} tile(s)")
    log(f"Periods: {len(periods)} ({periods[0].label} .. {periods[-1].label}); "
        f"imagery {settings.search_start} .. {settings.search_end}")

    if "extract" in settings.stages:
        by_day = catalog.search(settings, grid.aoi_ll)
        catalog.write_scene_table(by_day, settings.out_dir / "catalog" / "scenes.csv")
        log(f"Solar days: {len(by_day)}")
        extract.run(settings, grid, by_day)
    if "gapfill" in settings.stages:
        gapfill.run(settings, grid, periods)
    if "mosaic" in settings.stages:
        mosaic.run(settings, periods)
    if "report" in settings.stages:
        report.run(settings, grid, periods)
    log("Done.")


if __name__ == "__main__":
    main()
