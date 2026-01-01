#!/usr/bin/env python3
"""
Generate a placer-friendly masked terrain probability raster from a DEM and an
existing terrain hotspot raster (p_terrain.tif).

Derived layers from the DEM (using WhiteboxTools):
  - filled DEM
  - D8 flow direction + flow accumulation
  - streams (by flow accumulation threshold)
  - distance to streams (Euclidean)
  - HAND (height above stream)
  - slope (degrees)

Mask thresholds (defaults match prior workflow):
  - slope <= 10 degrees
  - HAND <= 15 m
  - distance to stream <= 300 m
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from whitebox import WhiteboxTools


def run_wbt(wbt: WhiteboxTools, tool: str, **kwargs) -> None:
    ok = getattr(wbt, tool)(**kwargs)
    if not ok:
        raise RuntimeError(f"WhiteboxTools {tool} failed with args {kwargs}")


def apply_mask(prob_path: Path, slope_path: Path, hand_path: Path, dist_path: Path,
               out_path: Path, slope_max: float, hand_max: float, dist_max: float,
               window: int = 1024) -> None:
    with rasterio.open(prob_path) as p_src, \
         rasterio.open(slope_path) as s_src, \
         rasterio.open(hand_path) as h_src, \
         rasterio.open(dist_path) as d_src:

        profile = p_src.profile
        profile.update(dtype="float32", nodata=0.0, compress="DEFLATE",
                       tiled=True, blockxsize=512, blockysize=512, BIGTIFF="YES")
        height, width = p_src.height, p_src.width

        with rasterio.open(out_path, "w", **profile) as dst:
            for row_off in range(0, height, window):
                win_h = min(window, height - row_off)
                for col_off in range(0, width, window):
                    win_w = min(window, width - col_off)
                    win = Window(col_off=col_off, row_off=row_off, width=win_w, height=win_h)

                    p = p_src.read(1, window=win)
                    slp = s_src.read(1, window=win)
                    hd = h_src.read(1, window=win)
                    dist = d_src.read(1, window=win)

                    mask = np.ones_like(p, dtype=bool)
                    mask &= np.isfinite(p)
                    mask &= np.isfinite(slp) & (slp <= slope_max)
                    mask &= np.isfinite(hd) & (hd <= hand_max)
                    mask &= np.isfinite(dist) & (dist <= dist_max)

                    out = np.zeros_like(p, dtype="float32")
                    out[mask] = p[mask].astype("float32")
                    dst.write(out, 1, window=win)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build p_terrain_masked.tif from DEM and p_terrain.tif.")
    parser.add_argument("--dem", type=Path, default=Path("data/dem_utm.tif"),
                        help="Input DEM (UTM, GeoTIFF).")
    parser.add_argument("--prob", type=Path, default=Path("data/dem_features/p_terrain.tif"),
                        help="Terrain probability raster to mask.")
    parser.add_argument("--out", type=Path, default=Path("data/dem_features/p_terrain_masked.tif"),
                        help="Output masked raster.")
    parser.add_argument("--stream-thresh", type=float, default=1000.0,
                        help="Flow accumulation threshold (cells) to define streams.")
    parser.add_argument("--slope-max", type=float, default=10.0, help="Slope threshold in degrees.")
    parser.add_argument("--hand-max", type=float, default=15.0, help="HAND threshold in meters.")
    parser.add_argument("--dist-max", type=float, default=300.0, help="Distance-to-stream threshold in meters.")
    args = parser.parse_args()

    if not args.dem.exists():
        raise FileNotFoundError(f"DEM not found: {args.dem}")
    if not args.prob.exists():
        raise FileNotFoundError(f"Probability raster not found: {args.prob}")

    wbt = WhiteboxTools()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        filled = tmp / "dem_filled.tif"
        fdir = tmp / "d8_dir.tif"
        facc = tmp / "flow_acc.tif"
        streams = tmp / "streams.tif"
        dist = tmp / "dist2stream.tif"
        hand = tmp / "hand.tif"
        slope = tmp / "slope.tif"

        wbt.set_working_dir(tmpdir)
        wbt.verbose = False

        run_wbt(wbt, "fill_depressions", dem=str(args.dem), output=str(filled))
        run_wbt(wbt, "d8_pointer", dem=str(filled), output=str(fdir))
        run_wbt(wbt, "d8_flow_accumulation", dem=str(filled), output=str(facc), out_type="cells")
        run_wbt(wbt, "extract_streams", flow_accum=str(facc), output=str(streams), threshold=args.stream_thresh)
        run_wbt(wbt, "euclidean_distance", i=str(streams), output=str(dist))
        run_wbt(wbt, "elevation_above_stream", dem=str(filled), streams=str(streams), output=str(hand), wd=tmpdir)
        run_wbt(wbt, "slope", dem=str(filled), output=str(slope), units="degrees")

        apply_mask(prob_path=args.prob,
                   slope_path=slope,
                   hand_path=hand,
                   dist_path=dist,
                   out_path=args.out,
                   slope_max=args.slope_max,
                   hand_max=args.hand_max,
                   dist_max=args.dist_max)

    print(f"Masked raster written to {args.out}")


if __name__ == "__main__":
    main()
