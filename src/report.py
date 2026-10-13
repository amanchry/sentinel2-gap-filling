"""Stage 4: charts and tables to check each method.

    OUT_DIR/report/aoi.png
    OUT_DIR/report/observations.csv                    AOI mean / clear fraction per date
    OUT_DIR/report/<method>/summary.csv                AOI mean and QA shares per index and period
    OUT_DIR/report/<method>/timeseries_<index>.png     raw observations vs gap-filled periods
    OUT_DIR/report/<method>/maps_<index>.png           one small map per period
    OUT_DIR/report/<method>/pixels_<index>.png         a few individual pixels, raw vs filled
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path("/tmp") / "mplconfig-s2-gapfill"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import rasterio

from . import indices as idx
from .common import Settings, log, read_band, read_json
from .gapfill import load_cube, observation_list
from .periods import Period

FLAG_STYLE = {  # code: (label, colour)
    1: ("observed", "#2a7f62"),
    2: ("interpolated", "#e0a020"),
    3: ("edge fill", "#c05050"),
    0: ("no value", "#bbbbbb"),
}


def _observation_series(settings: Settings, grid) -> dict[str, dict]:
    """date -> {n_aoi, clear, <index>: (sum, count)} summed over tiles."""
    series: dict[str, dict] = {}
    for tile in grid.tiles:
        for path in sorted((settings.obs_dir / tile.id).glob("*.json")):
            stats = read_json(path)
            row = series.setdefault(stats["date"], {"n_aoi": 0, "clear": 0.0, "indices": {}})
            row["n_aoi"] += stats["n_aoi"]
            row["clear"] += stats["clear_fraction"] * stats["n_aoi"]
            for name, s in stats["indices"].items():
                acc = row["indices"].setdefault(name, [0.0, 0])
                acc[0] += s["sum"]
                acc[1] += s["count"]
    return dict(sorted(series.items()))


def _period_summary(settings: Settings, grid, method: str, index: str, periods: list[Period]) -> list[dict]:
    rows = []
    totals = {p.label: {"sum": 0.0, "count": 0, "flags": np.zeros(4)} for p in periods}
    for tile in grid.tiles:
        path = settings.method_dir(method) / index / "stats" / f"{tile.id}.json"
        if not path.exists():
            continue
        for label, s in read_json(path)["periods"].items():
            if label in totals:
                totals[label]["sum"] += s["sum"]
                totals[label]["count"] += s["count"]
                totals[label]["flags"] += np.array([s["flags"][str(c)] for c in range(4)])
    n_aoi = sum(int(grid.aoi_mask(t).sum()) for t in grid.tiles)
    for p in periods:
        t = totals[p.label]
        f = t["flags"]
        # Pixels outside the AOI also carry flag 0; count "no value" inside the AOI only.
        observed, filled, edge = f[1] / n_aoi, f[2] / n_aoi, f[3] / n_aoi
        rows.append({
            "period": p.label, "start": p.start, "end": p.end,
            "mean": round(t["sum"] / t["count"], 4) if t["count"] else float("nan"),
            "observed": round(observed, 4), "interpolated": round(filled, 4), "edge": round(edge, 4),
            "no_value": round(max(0.0, 1.0 - observed - filled - edge), 4),
        })
    return rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _timeseries_chart(index: str, obs: dict, rows: list[dict], path: Path, settings: Settings, method: str):
    spec = idx.INDICES[index]
    fig, (ax, axq) = plt.subplots(2, 1, figsize=(12, 6.4), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]})
    dates, means, clear = [], [], []
    for day, row in obs.items():
        s, c = row["indices"].get(index, (0.0, 0))
        if c:
            dates.append(dt.date.fromisoformat(day))
            means.append(s / c)
            clear.append(row["clear"] / row["n_aoi"] if row["n_aoi"] else 0)
    clear = np.array(clear)
    ax.scatter(dates, means, s=10 + 90 * clear, c="#4c78a8", alpha=0.75, edgecolor="none",
               label="clear-sky observation (size = clear share)")
    centres = [r["start"] + (r["end"] - r["start"]) / 2 for r in rows]
    ax.plot(centres, [r["mean"] for r in rows], color="#d1495b", marker="o", lw=1.8,
            label=f"{method}: period mean")
    for r in rows:
        ax.axvspan(r["start"], r["end"] + dt.timedelta(days=1), color="#000", alpha=0.03 if rows.index(r) % 2 else 0)
    ax.axvspan(settings.search_start, settings.start, color="#888", alpha=0.08)
    ax.axvspan(settings.end + dt.timedelta(days=1), settings.search_end, color="#888", alpha=0.08)
    ax.set_ylabel(f"{index} (AOI mean)")
    ax.set_title(f"{index}: clear observations vs {method} periods ({spec.description})")
    ax.legend(loc="best", frameon=False, fontsize=9)
    ax.grid(alpha=0.25)

    bottom = np.zeros(len(rows))
    width = [(r["end"] - r["start"]).days + 1 for r in rows]
    for key, code in (("observed", 1), ("interpolated", 2), ("edge", 3), ("no_value", 0)):
        vals = np.array([r[key] for r in rows])
        axq.bar([r["start"] for r in rows], vals, width=width, bottom=bottom, align="edge",
                color=FLAG_STYLE[code][1], label=FLAG_STYLE[code][0], edgecolor="white", linewidth=0.5)
        bottom += vals
    axq.set_ylim(0, 1)
    axq.set_ylabel("share of AOI")
    axq.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8)
    axq.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _read_small(path: Path, max_px: int = 360) -> np.ndarray:
    with rasterio.open(path) as src:
        scale = max(src.height, src.width) / max_px
        shape = (max(1, round(src.height / max(scale, 1))), max(1, round(src.width / max(scale, 1))))
        data = src.read(1, out_shape=shape, masked=True).astype(np.float32).filled(np.nan)
        if src.dtypes[0] == "int16":
            data /= 10000.0
        return data


def _maps_chart(index: str, method_dir: Path, periods: list[Period], path: Path, method: str):
    spec = idx.INDICES[index]
    cols = 6
    nrows = math.ceil(len(periods) / cols)
    fig, axes = plt.subplots(nrows, cols, figsize=(cols * 2.4, nrows * 2.5 + 0.6), squeeze=False)
    image = None
    for ax, period in zip(axes.flat, periods):
        candidates = [method_dir / f"{index}_{period.label}{ext}" for ext in (".tif", ".vrt")]
        target = next((c for c in candidates if c.exists()), None)
        ax.set_axis_off()
        if target is None:
            continue
        data = _read_small(target)
        image = ax.imshow(data, cmap=spec.cmap, vmin=spec.display_range[0], vmax=spec.display_range[1],
                          interpolation="nearest")
        ax.set_title(f"{period.start:%d %b}–{period.end:%d %b}", fontsize=8)
    for ax in list(axes.flat)[len(periods):]:
        ax.set_axis_off()
    if image is not None:
        fig.colorbar(image, ax=axes, fraction=0.02, pad=0.01, label=index)
    fig.suptitle(f"{index} — method: {method}", fontsize=11)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _pixels_chart(settings: Settings, grid, method: str, index: str, periods: list[Period], path: Path):
    """Raw observations and filled values for the pixels with the most interpolated periods."""
    tile = grid.tiles[0]
    obs = observation_list(settings, tile.id)
    if not obs:
        return
    cube = load_cube(settings, tile, index, obs)
    base = settings.method_dir(method) / index
    flags, values = [], []
    for p in periods:
        flags.append(read_band(base / "qa_tiles" / p.label / f"{tile.id}.tif", 1))
        values.append(read_band(base / "tiles" / p.label / f"{tile.id}.tif", 1))
    flags, values = np.stack(flags), np.stack(values)
    score = (flags == 2).sum(axis=0) + (flags == 1).sum(axis=0) * 0.01
    score[~np.isfinite(values).any(axis=0)] = -1
    rng = np.random.default_rng(0)
    top = np.argsort(score.ravel())[::-1][:200]
    picks = rng.choice(top, size=min(4, top.size), replace=False)
    obs_dates = [dt.date.fromisoformat(d) for d, _ in obs]
    centres = [p.start + (p.end - p.start) / 2 for p in periods]
    fig, axes = plt.subplots(len(picks), 1, figsize=(12, 2.3 * len(picks)), sharex=True, squeeze=False)
    for ax, flat in zip(axes[:, 0], picks):
        r, c = np.unravel_index(flat, score.shape)
        raw = cube[:, r, c]
        ok = np.isfinite(raw)
        ax.plot(np.array(obs_dates)[ok], raw[ok], "o", color="#4c78a8", ms=4, label="clear observation")
        ax.plot(centres, values[:, r, c], "-", color="#999", lw=1)
        for code in (1, 2, 3):
            sel = flags[:, r, c] == code
            if sel.any():
                ax.plot(np.array(centres)[sel], values[sel, r, c], "s", ms=6, color=FLAG_STYLE[code][1],
                        label=f"period value: {FLAG_STYLE[code][0]}")
        x, y = tile.transform * (c + 0.5, r + 0.5)
        ax.set_title(f"{tile.id} pixel row {r}, col {c}  (x={x:.0f}, y={y:.0f} {tile.crs})", fontsize=9)
        ax.set_ylabel(index)
        ax.grid(alpha=0.25)
    axes[0, 0].legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8)
    axes[-1, 0].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _aoi_map(grid, path: Path):
    fig, ax = plt.subplots(figsize=(6, 6))
    grid.aoi_ll.boundary.plot(ax=ax, color="#0b6e4f")
    grid.aoi_ll.plot(ax=ax, color="#8fce6a", alpha=0.35)
    for tile in grid.tiles:
        xs, ys = tile.geom_ll.exterior.xy
        ax.plot(xs, ys, color="#555", lw=0.6)
    ax.set_title(f"AOI {grid.aoi_ll['name'].iloc[0]} — {len(grid.tiles)} tiles, {grid.area_km2:,.0f} km²")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run(settings: Settings, grid, periods: list[Period]) -> None:
    out = settings.out_dir / "report"
    out.mkdir(parents=True, exist_ok=True)
    _aoi_map(grid, out / "aoi.png")
    obs = _observation_series(settings, grid)
    obs_rows = []
    for day, row in obs.items():
        rec = {"date": day, "clear_fraction": round(row["clear"] / row["n_aoi"], 4) if row["n_aoi"] else 0.0}
        for name in settings.indices:
            s, c = row["indices"].get(name, (0.0, 0))
            rec[f"{name}_mean"] = round(s / c, 4) if c else ""
        obs_rows.append(rec)
    if obs_rows:
        _write_csv(out / "observations.csv", obs_rows)

    for method in settings.methods:
        mdir = out / method
        mdir.mkdir(parents=True, exist_ok=True)
        summary = []
        for index in settings.indices:
            rows = _period_summary(settings, grid, method, index, periods)
            summary += [{"index": index, **r} for r in rows]
            _timeseries_chart(index, obs, rows, mdir / f"timeseries_{index}.png", settings, method)
            _maps_chart(index, settings.method_dir(method) / index, periods, mdir / f"maps_{index}.png", method)
            _pixels_chart(settings, grid, method, index, periods, mdir / f"pixels_{index}.png")
        _write_csv(mdir / "summary.csv", summary)
        log(f"Report for {method}: {mdir}")
