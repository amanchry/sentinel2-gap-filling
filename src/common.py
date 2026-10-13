"""Settings, logging and raster helpers shared by every stage."""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine

INT16_NODATA = -32768
INT16_SCALE = 10000.0

# GDAL settings for anonymous reads of public cloud COGs.
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
}


@dataclass
class Settings:
    project_dir: Path
    aoi_geojson: str | None
    extent: tuple | None
    start: dt.date
    end: dt.date
    buffer_days: int
    period: str | int
    resolution_m: float
    output_crs: str
    tile_size_px: int
    stac_url: str
    collection: str
    max_scene_cloud: float
    bad_scl: tuple
    indices: list
    methods: dict
    out_dir: Path
    output_dtype: str
    final_format: str
    io_workers: int
    cpu_workers: int
    stages: list
    overwrite: bool
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_module(cls, module, project_dir: Path) -> "Settings":
        out_dir = Path(module.OUT_DIR)
        if not out_dir.is_absolute():
            out_dir = project_dir / out_dir
        return cls(
            project_dir=project_dir,
            aoi_geojson=module.AOI_GEOJSON,
            extent=module.EXTENT,
            start=dt.date.fromisoformat(module.START),
            end=dt.date.fromisoformat(module.END),
            buffer_days=int(module.BUFFER_DAYS),
            period=module.PERIOD,
            resolution_m=float(module.RESOLUTION_M),
            output_crs=module.OUTPUT_CRS,
            tile_size_px=int(module.TILE_SIZE_PX),
            stac_url=module.STAC_URL,
            collection=module.COLLECTION,
            max_scene_cloud=float(module.MAX_SCENE_CLOUD),
            bad_scl=tuple(module.BAD_SCL),
            indices=list(module.INDICES),
            methods=dict(module.METHODS),
            out_dir=out_dir,
            output_dtype=module.OUTPUT_DTYPE,
            final_format=module.FINAL_FORMAT,
            io_workers=int(module.IO_WORKERS),
            cpu_workers=int(module.CPU_WORKERS),
            stages=list(module.STAGES),
            overwrite=bool(module.OVERWRITE),
        )

    @property
    def search_start(self) -> dt.date:
        return self.start - dt.timedelta(days=self.buffer_days)

    @property
    def search_end(self) -> dt.date:
        return self.end + dt.timedelta(days=self.buffer_days)

    @property
    def obs_dir(self) -> Path:
        return self.out_dir / "observations"

    def method_dir(self, method: str) -> Path:
        return self.out_dir / "methods" / method


_log_lock = threading.Lock()
_log_path: Path | None = None


def set_log_file(path: Path) -> None:
    global _log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_path = path


def log(message: str) -> None:
    line = f"[{dt.datetime.now():%H:%M:%S}] {message}"
    with _log_lock:
        print(line, flush=True)
        if _log_path is not None:
            with _log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def as_affine(transform) -> Affine:
    return transform if isinstance(transform, Affine) else Affine(*transform)


def _block(n: int) -> int:
    return 16 if n < 16 else min(512, n // 16 * 16)


def write_raster(
    path: Path,
    bands: list[np.ndarray],
    crs: str,
    transform,
    descriptions: list[str] | None = None,
    dtype: str = "float32",
    tags: dict | None = None,
) -> None:
    """Write bands to a tiled GeoTIFF atomically (temp file, then rename).

    float32: NaN is nodata. int16: value * 10000, -32768 is nodata.
    uint8/uint16: written as-is, no nodata value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = bands[0].shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(bands),
        "dtype": dtype,
        "crs": CRS.from_user_input(crs),
        "transform": as_affine(transform),
        "compress": "deflate",
        "tiled": True,
        "blockxsize": _block(width),
        "blockysize": _block(height),
        "BIGTIFF": "IF_SAFER",
    }
    if dtype == "float32":
        profile.update(nodata=np.nan, predictor=3)
    elif dtype == "int16":
        profile.update(nodata=INT16_NODATA, predictor=2)
    else:
        profile.update(predictor=2)  # QA layers: every value, including 0, is meaningful
    tmp = path.with_name(path.name + ".part")
    with rasterio.open(tmp, "w", **profile) as dst:
        for i, band in enumerate(bands, start=1):
            if dtype == "int16":
                out = np.full(band.shape, INT16_NODATA, dtype=np.int16)
                ok = np.isfinite(band)
                out[ok] = np.clip(np.rint(band[ok] * INT16_SCALE), -32767, 32767).astype(np.int16)
                dst.write(out, i)
                dst.update_tags(i, scale_factor=str(1 / INT16_SCALE))
            else:
                dst.write(band.astype(dtype, copy=False), i)
            if descriptions:
                dst.set_band_description(i, descriptions[i - 1])
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
    os.replace(tmp, path)


def read_band(path: Path, name_or_index) -> np.ndarray:
    """Read one band as float32 with NaN for nodata. int16 files are unscaled."""
    with rasterio.open(path) as src:
        if isinstance(name_or_index, str):
            index = list(src.descriptions).index(name_or_index) + 1
        else:
            index = int(name_or_index)
        data = src.read(index)
        if data.dtype == np.int16:
            out = data.astype(np.float32) / INT16_SCALE
            out[data == INT16_NODATA] = np.nan
            return out
        return data.astype(np.float32, copy=False)


def band_names(path: Path) -> list[str]:
    with rasterio.open(path) as src:
        return [d or "" for d in src.descriptions]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
