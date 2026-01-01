## Terrain Mask and Wuhan Blend Commands

### Build placer-friendly terrain mask
From repo root (DEM and terrain prob must exist):
```bash
python data/scripts/build_p_terrain_masked_from_dem.py \
  --dem data/dem_utm.tif \
  --prob data/dem_features/p_terrain.tif \
  --out data/dem_features/p_terrain_masked.tif \
  --stream-thresh 1000 \
  --slope-max 10 --hand-max 15 --dist-max 300
```
Requires `whitebox`, `rasterio`, `numpy`.

### Blend Wuhan weights into terrain mask
```bash
python data/scripts/blend_wuhan_weights.py \
  --hotspots-path "/Volumes/One Touch/Cape Mount Field Data/Wuhan Hotspots.shp" \
  --base-raster data/dem_features/p_terrain_masked.tif \
  --weight-out data/dem_features/wuhan_rclass_weight.tif \
  --blended-out data/dem_features/p_terrain_masked_wuhan.tif \
  --value-field RVALUE \
  --strength 0.8 \
  --neutral 0.5
```
Adjust `--strength` to tune Wuhan influence (e.g., 0.6 softer, 1.5 stronger). Use `--value-field RCLASS` to weight by class instead of value. Outputs:
- `wuhan_rclass_weight.tif`: normalized weights (0.05–1.0; neutral 0.5 outside polygons)
- `p_terrain_masked_wuhan.tif`: terrain mask blended with Wuhan weights

## Visualization Hints (GIS)
- Renderer: `Singleband pseudocolor`.
- Min: `0.4`; Max: `0.9`–`1.0` to emphasize higher probabilities.
- Layer transparency/opacity: ~`0.4` so base layers show through.
- Resampling when zooming: `Bilinear` for smooth display.
