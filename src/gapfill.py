"""Stage 2: run each configured gap-filling method on each tile.

Reads the observation cache and writes, per method / index / period / tile:

    OUT_DIR/methods/<method>/<index>/tiles/<period>/<tile>.tif     values
    OUT_DIR/methods/<method>/<index>/qa_tiles/<period>/<tile>.tif  band 1 flag, band 2 gap days
    OUT_DIR/methods/<method>/<index>/stats/<tile>.json             per-period summary; marks done

Tiles run in parallel processes; inside a tile everything is vectorised numpy.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

from . import methods as gm
from .common import Settings, log, read_band, read_json, write_json, write_raster
from .grid import Tile
from .periods import Period


def observation_list(settings: Settings, tile_id: str) -> list[tuple[str, int]]:
    """(date, ordinal) of non-empty cached observations for a tile, ascending."""
    out = []
    for path in sorted((settings.obs_dir / tile_id).glob("*.json")):
        stats = read_json(path)
        if not stats.get("empty") and (path.with_suffix(".tif")).exists():
            out.append((stats["date"], dt.date.fromisoformat(stats["date"]).toordinal()))
    return out


def load_cube(settings: Settings, tile: Tile, index: str, obs: list[tuple[str, int]]) -> np.ndarray:
    cube = np.full((len(obs), tile.height, tile.width), np.nan, dtype=np.float32)
    for i, (day, _) in enumerate(obs):
        cube[i] = read_band(settings.obs_dir / tile.id / f"{day}.tif", index)
    return cube


def _stats_key(method: str, params: dict, periods: list[Period], obs: list) -> dict:
    return {
        "method": method,
        "params": params,
        "periods": [p.label for p in periods],
        "observations": [d for d, _ in obs],
    }


def _run_tile(settings: Settings, tile: Tile, periods: list[Period]) -> list[str]:
    messages = []
    obs = observation_list(settings, tile.id)
    obs_days = np.array([o for _, o in obs], dtype=np.int64)
    for method_name, params in settings.methods.items():
        method = gm.create(method_name, params)
        for index in settings.indices:
            base = settings.method_dir(method_name) / index
            stats_path = base / "stats" / f"{tile.id}.json"
            key = _stats_key(method_name, params, periods, obs)
            if stats_path.exists() and not settings.overwrite and read_json(stats_path).get("key") == key:
                continue
            started = time.time()
            cube = load_cube(settings, tile, index, obs)
            result = method.fill(cube, obs_days, periods)
            del cube
            per_period = {}
            for p, period in enumerate(periods):
                values = result.values[p]
                write_raster(base / "tiles" / period.label / f"{tile.id}.tif", [values], tile.crs,
                             tile.transform, [f"{index} {period.label}"], dtype=settings.output_dtype,
                             tags={"method": method_name, "index": index, "period": period.label})
                write_raster(base / "qa_tiles" / period.label / f"{tile.id}.tif",
                             [result.flag[p].astype(np.uint16), result.gap_days[p]], tile.crs,
                             tile.transform, ["flag", "gap_days"], dtype="uint16")
                ok = np.isfinite(values)
                per_period[period.label] = {
                    "count": int(ok.sum()),
                    "sum": float(values[ok].sum()),
                    "flags": {str(c): int((result.flag[p] == c).sum()) for c in range(4)},
                }
            write_json(stats_path, {"key": key, "tile": tile.id, "periods": per_period})
            messages.append(f"{tile.id} {method_name}/{index}: {len(obs)} obs -> {len(periods)} periods "
                            f"in {time.time() - started:.1f}s")
    return messages


def run(settings: Settings, grid, periods: list[Period]) -> None:
    log(f"Gap-fill: methods {list(settings.methods)}, {len(settings.indices)} indices, "
        f"{len(periods)} periods, {len(grid.tiles)} tiles, {settings.cpu_workers} processes")
    started = time.time()
    if settings.cpu_workers <= 1 or len(grid.tiles) == 1:
        for tile in grid.tiles:
            for line in _run_tile(settings, tile, periods):
                log("  " + line)
    else:
        with ProcessPoolExecutor(max_workers=settings.cpu_workers) as pool:
            futures = {pool.submit(_run_tile, settings, tile, periods): tile.id for tile in grid.tiles}
            for n, future in enumerate(as_completed(futures), start=1):
                for line in future.result():
                    log(f"  [{n}/{len(futures)}] " + line)
    log(f"Gap-fill finished in {(time.time() - started) / 60:.1f} min")
