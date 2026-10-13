"""STAC search and grouping of scenes into solar days."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pystac
import pystac_client
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.prepared import prep

from .common import Settings, log, read_json, write_json


def solar_day(item: pystac.Item) -> str:
    when = item.datetime
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    west, _, east, _ = item.bbox
    return (when + dt.timedelta(hours=(west + east) / 30.0)).date().isoformat()


def search(settings: Settings, aoi_ll) -> dict[str, list[pystac.Item]]:
    """Scenes intersecting the AOI, grouped by solar day.

    The result is cached in OUT_DIR/catalog/items.json so reruns do not
    depend on the STAC API returning the same thing twice.
    """
    cache = settings.out_dir / "catalog" / "items.json"
    key = {
        "stac": settings.stac_url,
        "collection": settings.collection,
        "start": settings.search_start.isoformat(),
        "end": settings.search_end.isoformat(),
        "max_cloud": settings.max_scene_cloud,
        "bounds": [round(float(v), 6) for v in aoi_ll.total_bounds],
    }
    if cache.exists() and not settings.overwrite:
        payload = read_json(cache)
        if payload.get("key") == key:
            items = [pystac.Item.from_dict(d) for d in payload["items"]]
            log(f"Catalog: {len(items)} scenes from cache {cache}")
            return _group(items)

    log(f"Searching {settings.stac_url} / {settings.collection} "
        f"{key['start']} .. {key['end']}")
    client = pystac_client.Client.open(settings.stac_url)
    west, south, east, north = aoi_ll.total_bounds
    result = client.search(
        collections=[settings.collection],
        bbox=(float(west), float(south), float(east), float(north)),
        datetime=f"{key['start']}T00:00:00Z/{key['end']}T23:59:59Z",
        query={"eo:cloud_cover": {"lte": settings.max_scene_cloud}},
    )
    aoi = prep(unary_union(aoi_ll.geometry))
    items = [i for i in result.items() if i.geometry is not None and aoi.intersects(shape(i.geometry))]
    write_json(cache, {"key": key, "items": [i.to_dict() for i in items]})
    log(f"Catalog: {len(items)} scenes after AOI and cloud filter")
    return _group(items)


def _group(items: list[pystac.Item]) -> dict[str, list[pystac.Item]]:
    grouped: dict[str, list[pystac.Item]] = {}
    for item in items:
        grouped.setdefault(solar_day(item), []).append(item)
    for day in grouped:
        # Least cloudy scene first: it wins where scenes overlap.
        grouped[day].sort(key=lambda i: i.properties.get("eo:cloud_cover", 100.0))
    return {day: grouped[day] for day in sorted(grouped)}


def write_scene_table(by_day: dict[str, list[pystac.Item]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["solar_day", "item_id", "platform", "mgrs_tile", "cloud_cover", "datetime"])
        for day, items in by_day.items():
            for item in items:
                writer.writerow([
                    day,
                    item.id,
                    item.properties.get("platform", ""),
                    item.properties.get("grid:code", item.properties.get("s2:mgrs_tile", "")),
                    item.properties.get("eo:cloud_cover", ""),
                    item.datetime.isoformat() if item.datetime else "",
                ])
