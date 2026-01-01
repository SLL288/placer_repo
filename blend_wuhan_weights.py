#!/usr/bin/env python3
"""
Rasterize Wuhan hotspot polygons into a normalized weight grid and blend it
with the existing placer hotspot probability map.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Tuple

import numpy as np
from osgeo import gdal, ogr


def _open_base_raster(path: Path):
    ds = gdal.Open(str(path))
    if ds is None:
        raise RuntimeError(f"Could not open raster: {path}")
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    nodata = band.GetNoDataValue()
    return ds, band, gt, proj, nodata


def rasterize_hotspots(
    hotspots_path: Path,
    template_path: Path,
    out_path: Path,
    value_field: str = "RVALUE",
    neutral_value: float = 0.5,
) -> None:
    """Burn normalized hotspot values to a raster aligned to template."""
    gdal.UseExceptions()
    os.environ.setdefault("SHAPE_RESTORE_SHX", "NO")

    base_ds, _, gt, proj, _ = _open_base_raster(template_path)
    width, height = base_ds.RasterXSize, base_ds.RasterYSize

    src_ds = ogr.Open(str(hotspots_path), 0)  # read-only
    if src_ds is None:
        raise RuntimeError(f"Could not open hotspots: {hotspots_path}")
    layer = src_ds.GetLayer(0)
    if layer.GetFeatureCount() == 0:
        raise RuntimeError("Hotspots layer is empty.")

    values = []
    for feat in layer:
        val = feat.GetField(value_field)
        if val is not None:
            values.append(float(val))
    if not values:
        raise RuntimeError(f"No values found in field {value_field}.")
    vmin, vmax = min(values), max(values)
    span = vmax - vmin if vmax != vmin else 1.0

    # Copy layer into memory and add normalized field.
    mem_drv = ogr.GetDriverByName("Memory")
    mem_ds = mem_drv.CreateDataSource("mem_hotspots")
    mem_layer = mem_ds.CopyLayer(layer, "hotspots")
    layer_defn = mem_layer.GetLayerDefn()
    if mem_layer.FindFieldIndex("WNORM", True) == -1:
        mem_layer.CreateField(ogr.FieldDefn("WNORM", ogr.OFTReal))
    for feat in mem_layer:
        raw = feat.GetField(value_field)
        norm = (float(raw) - vmin) / span
        feat.SetField("WNORM", norm)
        mem_layer.SetFeature(feat)
    mem_layer.SyncToDisk()

    # Prepare output raster.
    drv = gdal.GetDriverByName("GTiff")
    if out_path.exists():
        out_path.unlink()
    out_ds = drv.Create(
        str(out_path),
        width,
        height,
        1,
        gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=YES"],
    )
    out_ds.SetGeoTransform(gt)
    out_ds.SetProjection(proj)
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(0.0)
    out_band.Fill(neutral_value)

    gdal.RasterizeLayer(out_ds, [1], mem_layer, options=["ATTRIBUTE=WNORM"])
    out_band.FlushCache()
    out_ds.FlushCache()


def blend_probabilities(
    base_path: Path,
    weight_path: Path,
    out_path: Path,
    strength: float = 2.0,
    neutral_value: float = 0.5,
    eps: float = 1e-6,
) -> None:
    """Blend base probabilities with weight raster using a logit shift."""
    gdal.UseExceptions()
    base_ds, base_band, gt, proj, nodata = _open_base_raster(base_path)
    weight_ds, weight_band, w_gt, w_proj, _ = _open_base_raster(weight_path)

    if (base_ds.RasterXSize != weight_ds.RasterXSize) or (
        base_ds.RasterYSize != weight_ds.RasterYSize
    ):
        raise RuntimeError("Weight raster grid does not match base raster.")
    if gt != w_gt:
        raise RuntimeError("Weight raster geotransform does not match base raster.")

    drv = gdal.GetDriverByName("GTiff")
    if out_path.exists():
        out_path.unlink()
    out_ds = drv.Create(
        str(out_path),
        base_ds.RasterXSize,
        base_ds.RasterYSize,
        1,
        gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=YES"],
    )
    out_ds.SetGeoTransform(gt)
    out_ds.SetProjection(proj)
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(nodata)

    block_x, block_y = base_band.GetBlockSize()
    xsize, ysize = base_ds.RasterXSize, base_ds.RasterYSize

    for y in range(0, ysize, block_y):
        rows = min(block_y, ysize - y)
        for x in range(0, xsize, block_x):
            cols = min(block_x, xsize - x)
            base_arr = base_band.ReadAsArray(x, y, cols, rows).astype("float64")
            weight_arr = weight_band.ReadAsArray(x, y, cols, rows).astype("float64")

            mask = base_arr == nodata
            base_arr = np.clip(base_arr, eps, 1 - eps)
            weight_arr = np.where(weight_arr > 0, weight_arr, neutral_value)

            logit = np.log(base_arr / (1.0 - base_arr))
            shifted = logit + strength * (weight_arr - neutral_value)
            blended = 1.0 / (1.0 + np.exp(-shifted))
            blended = blended.astype("float32")
            blended[mask] = nodata

            out_band.WriteArray(blended, xoff=x, yoff=y)

    out_band.FlushCache()
    out_ds.FlushCache()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Blend Wuhan hotspot weights into placer hotspot map."
    )
    parser.add_argument(
        "--hotspots-path",
        type=Path,
        default=Path("/Volumes/One Touch/Cape Mount Field Data/Wuhan Hotspots.shp"),
        help="Path to Wuhan hotspot polygons shapefile.",
    )
    parser.add_argument(
        "--base-raster",
        type=Path,
        default=Path("data/dem_features/p_terrain_masked.tif"),
        help="Base hotspot probability raster (terrain-only masked).",
    )
    parser.add_argument(
        "--weight-out",
        type=Path,
        default=Path("data/dem_features/wuhan_rclass_weight.tif"),
        help="Output normalized weight raster.",
    )
    parser.add_argument(
        "--blended-out",
        type=Path,
        default=Path("data/dem_features/p_terrain_masked_wuhan.tif"),
        help="Output blended hotspot raster.",
    )
    parser.add_argument(
        "--value-field",
        type=str,
        default="RVALUE",
        help="Hotspot field to use for weighting (RVALUE or RCLASS).",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=2.0,
        help="Logit shift strength for weights (higher pushes scores more).",
    )
    parser.add_argument(
        "--neutral",
        type=float,
        default=0.5,
        help="Neutral weight value outside polygons.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Rasterizing weights from {args.hotspots_path} -> {args.weight_out}")
    rasterize_hotspots(
        hotspots_path=args.hotspots_path,
        template_path=args.base_raster,
        out_path=args.weight_out,
        value_field=args.value_field,
        neutral_value=args.neutral,
    )
    print(
        f"Blending base raster {args.base_raster} with weights -> {args.blended_out}"
    )
    blend_probabilities(
        base_path=args.base_raster,
        weight_path=args.weight_out,
        out_path=args.blended_out,
        strength=args.strength,
        neutral_value=args.neutral,
    )
    print("Done.")


if __name__ == "__main__":
    main()
