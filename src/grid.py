"""AOI loading, output CRS and the tile grid every stage shares."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from shapely.geometry import box
from shapely.ops import transform as shp_transform, unary_union

from .common import Settings


def load_aoi(settings: Settings) -> gpd.GeoDataFrame:
    """Dissolved AOI in EPSG:4326."""
    if settings.aoi_geojson is not None:
        path = Path(settings.aoi_geojson)
        if not path.is_absolute():
            path = settings.project_dir / path
        if not path.exists():
            raise SystemExit(f"AOI file not found: {path}")
        frame = gpd.read_file(path)
        label = path.name
    elif settings.extent is not None:
        west, south, east, north = settings.extent
        frame = gpd.GeoDataFrame(geometry=[box(west, south, east, north)], crs="EPSG:4326")
        label = f"extent {west},{south},{east},{north}"
    else:
        raise SystemExit("Set AOI_GEOJSON or EXTENT in config.py.")
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4326")
    frame = frame.to_crs("EPSG:4326")[["geometry"]]
    frame = frame.dissolve().reset_index(drop=True)
    frame["name"] = label
    return frame


def choose_crs(aoi_ll: gpd.GeoDataFrame, requested: str) -> str:
    if requested.lower() != "auto":
        crs = CRS.from_user_input(requested)
        if crs.is_geographic:
            raise SystemExit("OUTPUT_CRS must be in metres, for example EPSG:32643.")
        return crs.to_string()
    minx, miny, maxx, maxy = aoi_ll.total_bounds
    lon, lat = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    if maxx - minx > 12.0:
        return "EPSG:6933"
    zone = int((lon + 180.0) / 6.0) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


@dataclass(frozen=True)
class Tile:
    row: int
    col: int
    transform: Affine
    width: int
    height: int
    crs: str
    geom_ll: object  # tile footprint in EPSG:4326, for scene selection

    @property
    def id(self) -> str:
        return f"r{self.row:03d}_c{self.col:03d}"


@dataclass(frozen=True)
class Grid:
    crs: str
    transform: Affine
    width: int
    height: int
    tiles: tuple
    aoi_proj: object  # shapely geometry in grid CRS
    aoi_ll: object  # GeoDataFrame in EPSG:4326
    area_km2: float

    def tile(self, tile_id: str) -> Tile:
        for tile in self.tiles:
            if tile.id == tile_id:
                return tile
        raise KeyError(tile_id)

    def aoi_mask(self, tile: Tile) -> np.ndarray:
        """True inside the AOI."""
        return geometry_mask(
            [self.aoi_proj.__geo_interface__],
            out_shape=(tile.height, tile.width),
            transform=tile.transform,
            invert=True,
            all_touched=True,
        )


def build_grid(settings: Settings) -> Grid:
    aoi_ll = load_aoi(settings)
    crs = choose_crs(aoi_ll, settings.output_crs)
    aoi_proj = unary_union(aoi_ll.to_crs(crs).geometry)
    res = settings.resolution_m
    minx, miny, maxx, maxy = aoi_proj.bounds
    # Snap to the resolution so grids from different runs line up.
    minx, miny = math.floor(minx / res) * res, math.floor(miny / res) * res
    maxx, maxy = math.ceil(maxx / res) * res, math.ceil(maxy / res) * res
    width = int(round((maxx - minx) / res))
    height = int(round((maxy - miny) / res))
    transform = Affine(res, 0.0, minx, 0.0, -res, maxy)

    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    size = settings.tile_size_px
    tiles = []
    for row, y0 in enumerate(range(0, height, size)):
        for col, x0 in enumerate(range(0, width, size)):
            w, h = min(size, width - x0), min(size, height - y0)
            left, top = minx + x0 * res, maxy - y0 * res
            footprint = box(left, top - h * res, left + w * res, top)
            if not footprint.intersects(aoi_proj):
                continue
            tiles.append(
                Tile(
                    row=row,
                    col=col,
                    transform=Affine(res, 0.0, left, 0.0, -res, top),
                    width=w,
                    height=h,
                    crs=crs,
                    geom_ll=shp_transform(to_ll.transform, footprint),
                )
            )
    if not tiles:
        raise SystemExit("The AOI produced no tiles.")
    return Grid(
        crs=crs,
        transform=transform,
        width=width,
        height=height,
        tiles=tuple(tiles),
        aoi_proj=aoi_proj,
        aoi_ll=aoi_ll,
        area_km2=float(aoi_proj.area / 1e6),
    )
