"""
Pipeline settings. 
"""

# ---------------------------------------------------------------------------
# Area of interest. Set AOI_GEOJSON to a boundary file, or set it to None and
# give EXTENT as (west, south, east, north) in EPSG:4326.
# ---------------------------------------------------------------------------
AOI_GEOJSON = "data/features.geojson"
EXTENT = None

# ---------------------------------------------------------------------------
# Time. Output periods cover START..END (inclusive). BUFFER_DAYS of extra
# imagery is read on both sides so the first and last periods have
# neighbours to interpolate from.
# ---------------------------------------------------------------------------
START = "2025-05-01"
END = "2025-10-31"
BUFFER_DAYS = 45

# "dekad" = 1-10, 11-20, 21-end of each month (18 periods for May-Oct).
# An integer N gives fixed N-day periods starting at START.
PERIOD = "dekad"

# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------
RESOLUTION_M = 10.0
OUTPUT_CRS = "auto"  # "auto" = UTM zone of the AOI centre, or e.g. "EPSG:32643"
TILE_SIZE_PX = 1024  # memory per worker scales with TILE_SIZE_PX**2 * n_dates

# ---------------------------------------------------------------------------
# Imagery
# ---------------------------------------------------------------------------
STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-c1-l2a"
MAX_SCENE_CLOUD = 90.0  # drop scenes above this eo:cloud_cover (%)
# SCL classes treated as missing: no data, saturated, cloud shadow,
# cloud medium/high probability, thin cirrus, snow.
BAD_SCL = (0, 1, 3, 8, 9, 10, 11)

# Indices to produce. Available: NDVI, NDWI, NDMI, EVI, SWIR1, SWIR2
# (SWIR1/SWIR2 are B11/B12 surface reflectance resampled to 10 m.)
INDICES = ["NDVI", "NDWI", "NDMI", "EVI", "SWIR1", "SWIR2"]

# ---------------------------------------------------------------------------
# Gap-filling methods. Each entry runs independently and writes to
# OUT_DIR/methods/<name>/. Add new methods here once they exist in
# src/methods/.
# ---------------------------------------------------------------------------
METHODS = {
    # No gap-filling: per-period median of clear observations, gaps stay empty.
    # Remove "linear" below to run only this.
    "without_gapfilling": {},
    "linear": {
        # A gap is filled only if a clear observation exists within this many
        # days on BOTH sides of the period centre (Spatial Thoughts recipe).
        "max_gap_days": 45,
        # "leave_empty": leave the pixel empty when one side is missing.
        # "nearest": use the one available side (flagged as 3 in QA).
        "edge": "leave_empty",
    },
}

# ---------------------------------------------------------------------------
# Output and execution
# ---------------------------------------------------------------------------
OUT_DIR = "output"
OUTPUT_DTYPE = "float32"  # or "int16" (value * 10000, nodata -32768)
FINAL_FORMAT = "cog"  # "cog" = one Cloud-Optimised GeoTIFF per period, "vrt" = tiles + VRT only
IO_WORKERS = 8  # parallel downloads (threads)
CPU_WORKERS = 4  # parallel gap-fill tiles (processes)
STAGES = ["extract", "gapfill", "mosaic", "report"]
OVERWRITE = False
