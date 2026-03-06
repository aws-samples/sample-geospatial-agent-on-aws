# Use Cases

This directory contains pre-configured scenarios for the geospatial agent. Each scenario includes geometry, satellite imagery (COGs), and narrative descriptions that provide instant-load demo experiences.

## Directory Structure

```
use-cases/
├── la-fires-2025/          # 2025 LA Palisades Fire burn analysis
├── lake-mead-water/        # Lake Mead water level change (2020-2025)
├── amazon-deforestation/   # Amazon rainforest deforestation monitoring
└── [scenario-id]/
    ├── config.json          # Scenario metadata and dates
    ├── geometry.geojson     # Area of interest boundary (EPSG:4326)
    ├── narrative.md         # Description shown in the UI
    ├── before/
    │   ├── tci-YYYY-MM-DD.tif   # True Color Image (before event)
    │   └── ndvi|nbr|ndwi-YYYY-MM-DD.tif  # Index raster (before)
    └── after/
        ├── tci-YYYY-MM-DD.tif   # True Color Image (after event)
        └── ndvi|nbr|ndwi-YYYY-MM-DD.tif  # Index raster (after)
```

## Included Scenarios

| Scenario | Location | Analysis Type | Time Range |
|---|---|---|---|
| `la-fires-2025` | Los Angeles, CA | Normalized Burn Ratio (NBR) | Dec 2024 - Mar 2025 |
| `lake-mead-water` | Nevada/Arizona | Normalized Difference Water Index (NDWI) | Sep 2020 - Oct 2025 |
| `amazon-deforestation` | Amazon Basin | Normalized Difference Vegetation Index (NDVI) | Dec 2020 - Sep 2025 |

## Notes

- All raster files are Cloud Optimized GeoTIFFs (COGs) for streaming via TiTiler.
- Geometry files use EPSG:4326 (WGS84) coordinate system.
- Scenarios are loaded in the frontend via the Use Case Gallery page.
