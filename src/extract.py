"""Stage 1: clear-sky index observations per tile and solar day.

For every (tile, solar day) the scenes are read straight from the public COGs
onto the tile grid, cloud-masked with SCL, mosaicked (least cloudy scene
first), and turned into the configured indices. The result is cached as one
multi-band int16 GeoTIFF:

    OUT_DIR/observations/<tile>/<YYYY-MM-DD>.tif   one band per index
    OUT_DIR/observations/<tile>/<YYYY-MM-DD>.json  stats; marks the pair done

Every gap-filling method reads this cache, so imagery is downloaded once.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from shapely.geometry import shape

from . import indices as idx
from .common import GDAL_ENV, Settings, log, read_json, write_json, write_raster
from .grid import Grid, Tile


def _scale_offset(asset) -> tuple[float, float]:
    bands = asset.extra_fields.get("raster:bands") or [{}]
    return float(bands[0].get("scale", 1.0)), float(bands[0].get("offset", 0.0))


def _read_asset(href: str, tile: Tile, resampling: Resampling, retries: int = 3) -> np.ndarray:
    """Asset resampled onto the tile grid. 0 (the S2 nodata value) stays 0."""
    for attempt in range(1, retries + 1):
        try:
            with rasterio.open(href) as src:
                with WarpedVRT(
                    src,
                    crs=tile.crs,
                    transform=tile.transform,
                    width=tile.width,
                    height=tile.height,
                    resampling=resampling,
                    src_nodata=0,
                    nodata=0,
                ) as vrt:
                    return vrt.read(1)
        except rasterio.errors.RasterioIOError:
            if attempt == retries:
                raise
            time.sleep(3 * attempt)
    raise RuntimeError("unreachable")


def _read_scene(item, tile: Tile, bands: list[str], bad_scl: tuple) -> tuple[dict, np.ndarray]:
    """Reflectance per band (NaN = missing) and the clear-pixel mask for one scene."""
    scl = _read_asset(item.assets["scl"].href, tile, Resampling.nearest)
    clear = (scl != 0) & ~np.isin(scl, bad_scl)
    refl: dict[str, np.ndarray] = {}
    if not clear.any():
        return refl, clear
    for band in bands:
        asset = item.assets[band]
        scale, offset = _scale_offset(asset)
        resampling = Resampling.bilinear if band in idx.COARSE_BANDS else Resampling.nearest
        dn = _read_asset(asset.href, tile, resampling)
        values = dn.astype(np.float32) * scale + offset
        values[dn == 0] = np.nan
        refl[band] = values
        clear &= dn != 0
    return refl, clear


def _process(settings: Settings, grid: Grid, tile: Tile, day: str, items: list, aoi: np.ndarray) -> dict:
    out_dir = settings.obs_dir / tile.id
    tif_path, json_path = out_dir / f"{day}.tif", out_dir / f"{day}.json"
    bands = idx.required_bands(settings.indices)

    footprint = tile.geom_ll
    items = [i for i in items if shape(i.geometry).intersects(footprint)]
    shape_2d = (tile.height, tile.width)
    mosaic = {b: np.full(shape_2d, np.nan, dtype=np.float32) for b in bands}
    taken = np.zeros(shape_2d, dtype=bool)
    with rasterio.Env(**GDAL_ENV):
        for item in items:
            refl, clear = _read_scene(item, tile, bands, settings.bad_scl)
            use = clear & ~taken & aoi
            if not use.any():
                continue
            for band in bands:
                mosaic[band][use] = refl[band][use]
            taken |= use
            if taken[aoi].all():
                break

    n_aoi = int(aoi.sum())
    stats = {"date": day, "tile": tile.id, "n_scenes": len(items), "n_aoi": n_aoi,
             "scenes": [i.id for i in items], "indices": {}}
    layers = []
    for index in idx.get(settings.indices):
        values = idx.compute(index, mosaic)
        values[~aoi] = np.nan
        ok = np.isfinite(values)
        stats["indices"][index.name] = {"count": int(ok.sum()), "sum": float(values[ok].sum())}
        layers.append(values)
    stats["clear_fraction"] = float(taken.sum() / n_aoi) if n_aoi else 0.0
    stats["empty"] = not any(v["count"] for v in stats["indices"].values())
    if not stats["empty"]:
        write_raster(tif_path, layers, tile.crs, tile.transform, settings.indices, dtype="int16",
                     tags={"date": day, "scenes": ",".join(stats["scenes"])})
    elif tif_path.exists():
        tif_path.unlink()
    write_json(json_path, stats)
    return stats


def is_done(settings: Settings, tile: Tile, day: str) -> bool:
    path = settings.obs_dir / tile.id / f"{day}.json"
    if settings.overwrite or not path.exists():
        return False
    try:
        stats = read_json(path)
    except ValueError:
        return False
    return set(settings.indices) <= set(stats.get("indices", {}))


def run(settings: Settings, grid: Grid, by_day: dict[str, list]) -> None:
    masks = {tile.id: grid.aoi_mask(tile) for tile in grid.tiles}
    tasks = [(tile, day, items) for tile in grid.tiles for day, items in by_day.items()]
    todo = [t for t in tasks if not is_done(settings, t[0], t[1])]
    log(f"Extract: {len(tasks)} tile-days, {len(tasks) - len(todo)} cached, {len(todo)} to download "
        f"with {settings.io_workers} threads")
    if not todo:
        return
    failures = []
    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=settings.io_workers) as pool:
        futures = {pool.submit(_process, settings, grid, tile, day, items, masks[tile.id]): (tile.id, day)
                   for tile, day, items in todo}
        for future in as_completed(futures):
            tile_id, day = futures[future]
            done += 1
            try:
                stats = future.result()
                mean = stats["indices"].get("NDVI", {})
                ndvi = f" NDVI={mean['sum'] / mean['count']:.3f}" if mean.get("count") else ""
                log(f"  [{done}/{len(todo)}] {tile_id} {day} scenes={stats['n_scenes']} "
                    f"clear={stats['clear_fraction']:.1%}{ndvi}")
            except Exception as exc:  # keep going; the pair is retried on the next run
                failures.append((tile_id, day, repr(exc)))
                log(f"  [{done}/{len(todo)}] {tile_id} {day} FAILED: {exc!r}")
    log(f"Extract finished in {(time.time() - started) / 60:.1f} min")
    if failures:
        log(f"Extract: {len(failures)} tile-days failed. Run again to retry them.")
