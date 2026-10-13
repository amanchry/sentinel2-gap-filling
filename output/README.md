# Output folder

Everything in this folder is written by `python run_pipeline.py`, and all of it can be regenerated. Only this README is tracked in git.

Placeholders used below:

| Placeholder | Meaning | Example |
|---|---|---|
| `<method>` | gap-filling method from `METHODS` in `config.py` | `without_gapfilling`, `linear` |
| `<INDEX>` | spectral index from `INDICES` | `NDVI`, `NDWI`, `NDMI`, `EVI`, `SWIR1`, `SWIR2` |
| `<period>` | output period, `start_end` (inclusive) | `2025-05-01_2025-05-10` |
| `<date>` | Sentinel-2 acquisition (solar) day | `2025-07-14` |
| `<tile>` | processing tile, row and column in the grid | `r000_c000` |

```
output/
├── README.md                        this file
├── run.log                          log of every run (appended)
├── catalog/                         which Sentinel-2 scenes were used
├── observations/                    cloud-masked index values per acquisition date (cache)
├── methods/                         FINAL PRODUCTS, one folder per method
│   └── <method>/<INDEX>/
└── report/                          charts and tables for checking the results
```

---

## 1. Final products: `methods/<method>/<INDEX>/`

These are the rasters to use.

| File | Content |
|---|---|
| `<INDEX>_<period>.tif` | **The index value for that period.** One file per output period. |
| `qa/<INDEX>_qa_<period>.tif` | Quality layer for the same period. See below. |
| `<INDEX>_stack.vrt` | All periods of this index as one multi-band raster: band 1 = first period, band 2 = second, and so on. Open it in QGIS, or with `rioxarray` / `rasterio`, to get the full time series at once. |
| `tiles/`, `qa_tiles/`, `stats/` | Intermediate per-tile pieces. The pipeline uses them to resume and to build the mosaics, so you don't need to open them. |

**Raster format**
- Cloud-Optimised GeoTIFF at the `RESOLUTION_M` pixel size, deflate-compressed, with overviews.
- Projection: the UTM zone of the AOI centroid, or `OUTPUT_CRS` if set.
- Data type: float32, with `NaN` = no value. With `OUTPUT_DTYPE = "int16"` in the config, values are instead stored as value × 10000, with nodata −32768.
- Pixels outside the AOI boundary are `NaN`.

**Value ranges**

| Index | Formula (Sentinel-2 bands) | Range |
|---|---|---|
| NDVI | (B08 − B04) / (B08 + B04) | −1 … 1 |
| NDWI | (B03 − B08) / (B03 + B08), McFeeters | −1 … 1 |
| NDMI | (B08 − B11) / (B08 + B11) | −1 … 1 |
| EVI | 2.5 (B08 − B04) / (B08 + 6 B04 − 7.5 B02 + 1) | −1 … 1 |
| SWIR1 | B11 surface reflectance, 20 m resampled to 10 m | 0 … 1 |
| SWIR2 | B12 surface reflectance, 20 m resampled to 10 m | 0 … 1 |

**QA raster** (`qa/<INDEX>_qa_<period>.tif`, uint16, 2 bands)

Band 1, `flag`, says where each pixel's value came from:

| Value | Meaning |
|---|---|
| 0 | no value: no clear observation and the method could not fill it |
| 1 | observed: median of the clear observations inside the period (real data) |
| 2 | interpolated: estimated by the method from observations before and after |
| 3 | edge fill: estimated from one side only (linear with `edge = "nearest"`) |

Band 2, `gap_days`, is the number of days between the two observations used for the estimate. It is 0 for observed pixels. Larger values mean less trustworthy estimates.

**How the methods differ**
- `without_gapfilling`: flags are only 1 or 0. Cloudy periods stay empty. Use it as the baseline.
- `linear`: flags are 1, 2, and 0 where there was no clear observation within `max_gap_days` on both sides.

---

## 2. Report: `report/`

Diagnostics for validating each run and comparing methods.

| File | Content |
|---|---|
| `aoi.png` | the area of interest and the processing tiles |
| `observations.csv` | one row per acquisition date: share of the area that was clear, and the area-mean of each index |
| `<method>/summary.csv` | one row per index and period: area-mean value and the share of pixels that were observed, interpolated, edge-filled or empty |
| `<method>/timeseries_<INDEX>.png` | area-mean of raw clear observations (dots; size = clear share) against the method's period values (line), with a bar per period showing observed, interpolated and empty shares |
| `<method>/maps_<INDEX>.png` | a small map of every period |
| `<method>/pixels_<INDEX>.png` | four individual pixels: raw observations against period values, coloured by QA flag. This is the quickest way to see what a method does. |

---

## 3. Observation cache: `observations/<tile>/`

The cloud-masked index values on every acquisition date, before any gap-filling. They are downloaded once and shared by all methods.

| File | Content |
|---|---|
| `<date>.tif` | int16, one band per index (band names = index names), value × 10000, nodata −32768. Cloudy, shadowed, snow and no-data pixels are already removed. |
| `<date>.json` | scenes used, clear share, and per-index pixel count and sum. It marks the date as done. A fully cloudy date has only the `.json`. |

Deleting this folder forces all imagery to be downloaded again.

---

## 4. Scene catalogue: `catalog/`

| File | Content |
|---|---|
| `items.json` | cached STAC search result, so reruns use exactly the same scenes |
| `scenes.csv` | one row per scene: solar day, scene id, satellite (S2A/S2B/S2C), MGRS tile, cloud cover %, acquisition time |

---

## Rerunning

- Running again skips everything that already exists.
- Changing a method's settings or the periods recomputes only `methods/` and `report/`.
- Changing the area, dates or indices downloads only what is missing.
- To start from scratch, delete this folder, keeping this README, or set `OVERWRITE = True` in `config.py`.
