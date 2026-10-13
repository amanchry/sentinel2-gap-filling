"""Stage 3: assemble tiles into one raster per method / index / period.

    OUT_DIR/methods/<method>/<index>/<index>_<period>.tif      values (COG)
    OUT_DIR/methods/<method>/<index>/qa/<index>_qa_<period>.tif flag + gap days (COG)
    OUT_DIR/methods/<method>/<index>/<index>_stack.vrt         all periods as bands

With FINAL_FORMAT = "vrt" the .tif files are VRTs over the tiles instead.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import Settings, log
from .periods import Period

GDAL_BIN = Path(sys.executable).resolve().parent


def _gdal(tool: str, *args: str) -> None:
    exe = GDAL_BIN / tool
    subprocess.run([str(exe if exe.exists() else tool), *args], check=True, capture_output=True, text=True)


def _assemble(tile_dir: Path, target: Path, settings: Settings, resampling: str) -> Path:
    tiles = sorted(tile_dir.glob("*.tif"))
    if not tiles:
        raise FileNotFoundError(f"No tiles in {tile_dir}")
    target.parent.mkdir(parents=True, exist_ok=True)
    vrt = tile_dir.with_suffix(".vrt")
    _gdal("gdalbuildvrt", "-overwrite", str(vrt), *map(str, tiles))
    if settings.final_format == "vrt":
        final = target.with_suffix(".vrt")
        _gdal("gdalbuildvrt", "-overwrite", str(final), *map(str, tiles))
        return final
    if target.exists() and not settings.overwrite and target.stat().st_mtime >= max(t.stat().st_mtime for t in tiles):
        return target
    tmp = target.with_name(target.stem + ".part.tif")
    _gdal("gdal_translate", "-of", "COG", "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=YES",
          "-co", "BIGTIFF=IF_SAFER", "-co", "NUM_THREADS=ALL_CPUS", "-co", f"OVERVIEW_RESAMPLING={resampling}",
          str(vrt), str(tmp))
    tmp.replace(target)
    return target


def run(settings: Settings, periods: list[Period]) -> None:
    jobs = []
    for method in settings.methods:
        for index in settings.indices:
            base = settings.method_dir(method) / index
            for period in periods:
                jobs.append((base / "tiles" / period.label, base / f"{index}_{period.label}.tif", "AVERAGE"))
                jobs.append((base / "qa_tiles" / period.label, base / "qa" / f"{index}_qa_{period.label}.tif",
                             "NEAREST"))
    log(f"Mosaic: {len(jobs)} rasters ({settings.final_format})")
    with ThreadPoolExecutor(max_workers=max(1, settings.cpu_workers)) as pool:
        finals = list(pool.map(lambda job: _assemble(job[0], job[1], settings, job[2]), jobs))
    for method in settings.methods:
        for index in settings.indices:
            base = settings.method_dir(method) / index
            layers = [p for p in finals if p.parent == base]
            layers.sort()
            _gdal("gdalbuildvrt", "-overwrite", "-separate", str(base / f"{index}_stack.vrt"), *map(str, layers))
    log(f"Mosaic done. Outputs under {settings.out_dir / 'methods'}")
