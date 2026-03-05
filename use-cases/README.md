# Use Cases / Scenario Gallery

This directory contains pre-configured scenarios for the satellite imagery analysis application. Scenarios provide instant-load experiences where geometry, rasters, and analysis are pre-loaded from S3, eliminating the need for agent tool calls.

## 📁 Directory Structure

```
use-cases/
├── README.md (this file)
├── clip_cogs.py                   # Utility: Clip imagery to COGs (before/after)
├── simplify_geometry.py           # Utility: Simplify complex geometries
├── get_bbox.py                    # Utility: Calculate bounding box from geometry
├── la-fires-2025/                 # Example scenario
│   ├── config.json                # Scenario metadata, dates, and tool calls
│   ├── narrative.md               # Human-readable description with analysis
│   ├── geometry.geojson           # Area of interest boundary
│   ├── before/
│   │   ├── tci-YYYY-MM-DD.tif     # True Color Image (before)
│   │   └── nbr-YYYY-MM-DD.tif     # Normalized Burn Ratio (before)
│   └── after/
│       ├── tci-YYYY-MM-DD.tif     # True Color Image (after)
│       └── nbr-YYYY-MM-DD.tif     # Normalized Burn Ratio (after)
└── [your-scenario-id]/            # Add your scenario here
```

## 🚀 How to Add a New Scenario

### Step 1: Create Scenario Directory

Create a new directory with a unique ID (lowercase, hyphens, no spaces):

```bash
mkdir use-cases/your-scenario-id
cd use-cases/your-scenario-id
mkdir before after
```

### Step 2: Prepare Required Files

#### **config.json** (REQUIRED)

```json
{
  "name": "Your Scenario Name",
  "description": "Brief description of the scenario",
  "location": "Geographic location (city, state, country)",
  "dates": {
    "before": "YYYY-MM-DD",
    "after": "YYYY-MM-DD",
    "event_start": "YYYY-MM-DD"
  },
  "center_coordinates": {
    "lat": 34.0796,
    "lon": -118.5932
  },
  "scenario_version": "1.0",
  "created_date": "YYYY-MM-DD"
}
```

#### **geometry.geojson** (REQUIRED)

GeoJSON file defining the area of interest. Can be:
- Polygon (single boundary)
- MultiPolygon (multiple boundaries)
- FeatureCollection (multiple features)

**Requirements**:
- Must be in EPSG:4326 (WGS84) coordinate system
- Keep file size reasonable (<1MB recommended)
- Simplify complex geometries if needed

**Example**:
```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "name": "Area of Interest"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [[
          [-118.60, 34.05],
          [-118.55, 34.05],
          [-118.55, 34.10],
          [-118.60, 34.10],
          [-118.60, 34.05]
        ]]
      }
    }
  ]
}
```

#### **narrative.md** (OPTIONAL but recommended)

Human-readable markdown description of the scenario. This is displayed in the frontend.

```markdown
# Your Scenario Name

## Overview
Brief overview of the event/area.

## Timeline
- **Event Start**: Date
- **Before Image**: Date
- **After Image**: Date

## Impact
Key metrics and impacts.

## Data Sources
- Sentinel-2, Landsat, etc.
```

### Step 3: Prepare Raster Data (COGs)

All raster files must be **Cloud Optimized GeoTIFFs (COGs)** for streaming via TiTiler.

#### **Naming Convention**:
- `before/tci-YYYY-MM-DD.tif` - True Color Image (RGB bands)
- `before/nbr-YYYY-MM-DD.tif` - Normalized Burn Ratio
- `after/tci-YYYY-MM-DD.tif` - True Color Image (RGB bands)
- `after/nbr-YYYY-MM-DD.tif` - Normalized Burn Ratio

**Date format**: Use ISO 8601 format (YYYY-MM-DD) matching the dates in `config.json`

#### **Creating COGs**:

Use the provided utility script:

```bash
# 1. Get large bounding box imagery from Sentinel-2/Landsat
# Place them in your scenario directory:
# - tci_clipped_area_YYYY-MM-DD.tif
# - nbr_clipped_area_YYYY-MM-DD.tif

# 2. Clip before-fire imagery to geometry and convert to COG
python use-cases/clip_cogs.py --scenario your-scenario-id --date 2024-01-15 --period before

# 3. Clip after-fire imagery
python use-cases/clip_cogs.py --scenario your-scenario-id --date 2024-06-20 --period after

# Or edit the CONFIG section in clip_cogs.py and run without arguments:
python use-cases/clip_cogs.py
```

**Manual COG Creation** (using GDAL):

```bash
gdal_translate -of COG \
  -co COMPRESS=DEFLATE \
  -co PREDICTOR=2 \
  -co BIGTIFF=YES \
  input.tif output.tif
```

**Verify COG Format**:
```bash
gdalinfo output.tif | grep -i "layout.*cog"
```

### Step 4: Upload to S3

Upload your scenario directory to S3:

```bash
aws s3 sync use-cases/your-scenario-id/ \
  s3://your-bucket/use-cases/your-scenario-id/ \
  --exclude "*.md" --exclude "README*"
```

**Note**:
- Don't upload `*.md` documentation files (they're loaded separately)
- Upload narrative.md separately if needed
- Ensure S3 bucket matches `config.py` and backend `.env`

### Step 5: Register in Frontend

Add your scenario to `react-ui/frontend/src/pages/UseCaseGallery.tsx`:

```typescript
const scenarios: Scenario[] = [
  // ... existing scenarios
  {
    id: 'your-scenario-id',
    name: 'Your Scenario Name',
    description: 'Brief description (1-2 sentences)',
    icon: '🔥', // Choose an appropriate emoji
    metrics: [
      { label: 'Metric 1', value: '1,234 units' },
      { label: 'Metric 2', value: '567 units' },
    ],
    status: 'available', // or 'coming-soon'
  },
];
```

### Step 6: Test Locally

1. Start backend: `cd react-ui/backend && npm run dev`
2. Start frontend: `cd react-ui/frontend && npm run dev`
3. Navigate to: `http://localhost:5173/?scenario=your-scenario-id`
4. Verify:
   - Geometry loads and map zooms to area
   - All 4 layers appear (TCI Before, TCI After, NBR Before, NBR After)
   - Layer list matches visual order
   - Analysis displays in chat sidebar

## 📊 Layer Display Order

Layers are controlled by zIndex in `react-ui/frontend/src/pages/Chat.tsx`:

```typescript
// Higher zIndex = on top visually
rasters.push({
  url: config.assets.before.tci,
  name: 'TCI Before',
  zIndex: 4, // Top layer
});

rasters.push({
  url: config.assets.after.tci,
  name: 'TCI After',
  zIndex: 3,
});

rasters.push({
  url: config.assets.before.nbr,
  name: 'NBR Before',
  zIndex: 2,
});

rasters.push({
  url: config.assets.after.nbr,
  name: 'NBR After',
  zIndex: 1, // Bottom layer
});
```

**Customize for your scenario** if you want a different visual order.

## 🔧 Utility Scripts

### `clip_cogs.py`

Unified utility to clip both "before" and "after" imagery to your geometry and save as COGs.

**Features**:
- Handle CRS reprojection automatically
- Crop to AOI bounds
- Create proper COG format
- Verify output with rasterio
- Command-line arguments or CONFIG section

**Usage**:

**Option 1: Command-line arguments** (recommended)
```bash
# Clip before imagery
python use-cases/clip_cogs.py \
  --scenario la-fires-2025 \
  --date 2024-12-18 \
  --period before

# Clip after imagery
python use-cases/clip_cogs.py \
  --scenario la-fires-2025 \
  --date 2025-03-08 \
  --period after

# Custom input patterns
python use-cases/clip_cogs.py \
  --scenario my-scenario \
  --date 2024-01-01 \
  --period before \
  --tci-input "sentinel2_tci_{date}.tif" \
  --nbr-input "sentinel2_nbr_{date}.tif"
```

**Option 2: Edit CONFIG section** (for repeated runs)
1. Edit the CONFIG section at the top of `clip_cogs.py`:
   ```python
   DEFAULT_SCENARIO_ID = 'your-scenario-id'
   DEFAULT_PERIOD = 'before'  # or 'after'
   DEFAULT_DATE = '2024-01-15'
   DEFAULT_TCI_INPUT = 'tci_clipped_area_{date}.tif'
   DEFAULT_NBR_INPUT = 'nbr_clipped_area_{date}.tif'
   ```
2. Run: `python use-cases/clip_cogs.py`

**Arguments**:
- `--scenario, -s`: Scenario directory name
- `--date, -d`: Date in YYYY-MM-DD format
- `--period, -p`: 'before' or 'after'
- `--tci-input`: TCI input filename pattern (use `{date}` placeholder)
- `--nbr-input`: NBR input filename pattern (use `{date}` placeholder)

**Output**:
- Creates COGs in `use-cases/[scenario]/before/` or `use-cases/[scenario]/after/`
- Filenames: `tci-YYYY-MM-DD.tif`, `nbr-YYYY-MM-DD.tif`

---

### `simplify_geometry.py`

Simplifies complex geometries by keeping only the largest polygon from a MultiPolygon or reducing vertex count.

**Use when**:
- Your geometry has >10,000 vertices (causes slow map rendering)
- You have a MultiPolygon but only need the largest area
- File size of geometry.geojson is too large (>1MB)

**Usage**:
```bash
# Simplify a MultiPolygon to single largest polygon
python use-cases/simplify_geometry.py input.geojson output.geojson

# The script will:
# - Keep the largest polygon from MultiPolygon
# - Preserve exterior ring coordinates
# - Output simplified GeoJSON
```

**Example**:
```bash
python use-cases/simplify_geometry.py \
  use-cases/my-scenario/geometry_full.geojson \
  use-cases/my-scenario/geometry.geojson
```

---

### `get_bbox.py`

Calculates the bounding box from a GeoJSON geometry. Useful for downloading satellite imagery.

**Usage**:
```bash
# Get bounding box from geometry
python use-cases/get_bbox.py use-cases/la-fires-2025/geometry.geojson

# Output:
# {
#   "west": -118.60,
#   "south": 34.04,
#   "east": -118.52,
#   "north": 34.13
# }
```

**Use case**:
1. Create your geometry.geojson
2. Run get_bbox.py to get bounding box
3. Use bbox to download satellite imagery from Sentinel/Landsat
4. Use clip_cogs.py to clip imagery to exact geometry

---

## 📝 Best Practices

1. **Geometry Simplification**: Use `simplify_geometry.py` if your geometry is too complex (>10,000 vertices)

2. **File Sizes**: Keep individual COGs under 50MB for fast loading. Larger areas should be broken into tiles.

3. **Date Consistency**: Ensure dates in filenames match `config.json` exactly.

4. **CRS**: Always use EPSG:4326 (WGS84) for geometry. Rasters can be in any CRS (reprojected automatically).

5. **Testing**: Always test locally before deploying to production S3.

6. **Documentation**: Write clear narrative.md files with analysis results - they're shown to users!

7. **Tool Calls**: Define tool_calls in config.json to show the analysis workflow.

## 🐛 Troubleshooting

### "Scenario not found" error
- Check S3 bucket configuration in backend `.env`
- Verify S3 path: `s3://bucket/use-cases/your-id/config.json`
- Check AWS credentials

### Rasters show as grey/corrupted
- Verify COG format: `gdalinfo file.tif | grep COG`
- Check file isn't corrupted: `rio info file.tif`
- Ensure presigned URLs are working

### Layer order incorrect
- Check zIndex values in `Chat.tsx`
- Ensure all rasters have unique zIndex values
- Higher zIndex = on top

### Geometry doesn't display
- Validate GeoJSON: https://geojson.io
- Check CRS is EPSG:4326
- Verify geometry is within valid bounds (-180 to 180, -90 to 90)

## 📚 Additional Resources

- **COG Specification**: https://www.cogeo.org/
- **TiTiler Documentation**: https://developmentseed.org/titiler/
- **GeoJSON Spec**: https://geojson.org/
- **Sentinel-2 Data**: https://scihub.copernicus.eu/
- **Landsat Data**: https://earthexplorer.usgs.gov/

## 🤝 Contributing

When adding a new scenario:
1. Follow the naming conventions above
2. Test thoroughly locally
3. Document in `narrative.md`
4. Add to UseCaseGallery.tsx
5. Submit PR with clear description

## 🤝 Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.
