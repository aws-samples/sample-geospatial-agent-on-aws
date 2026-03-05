"""
Raster processing utilities for geospatial analysis.

This module provides helper functions for:
- Clipping rasters to area of interest (AOI) geometries
- Reading raster coordinate reference systems (CRS)
- Creating Cloud Optimized GeoTIFF (COG) files
- Validating raster-geometry intersections
- Quality checking satellite imagery (no-data analysis)

Supports working with Sentinel-2 satellite imagery and other geospatial raster formats.
"""
import json

import rasterio
import geopandas as gpd
from rasterio.mask import mask
from rasterio.enums import Resampling

def get_raster_crs(raster_path):
    """Get the coordinate reference system (CRS) of a raster"""
    with rasterio.open(raster_path) as dataset:
        return dataset.crs

def clip_raster_v2(mask_file, raster_file, target_file, crop_to_aoi=True):
    """Clip a raster using a mask file and save as Cloud Optimized GeoTIFF (COG)
    
    Args:
        mask_file: Path to mask geometry file
        raster_file: Path to input raster
        target_file: Path to output clipped raster
        crop_to_aoi: If True, crop to intersection. If False, create raster matching AOI bounds with satellite overlay.
    """
    import numpy as np
    from rasterio.windows import from_bounds
    from shapely.geometry import box
    
    # Read mask
    mask_df = gpd.read_file(mask_file)
    mask_crs = mask_df.crs

    # Open raster and check CRS compatibility
    with rasterio.open(raster_file) as src:
        raster_crs = src.crs
        print(f"Raster CRS {raster_crs}, Mask CRS: {mask_crs}")

        if mask_crs != raster_crs:
            raise Exception("Mask CRS does not match Raster CRS")

        if crop_to_aoi:
            # Standard behavior: crop to intersection only
            mask_json = json.loads(mask_df.to_json())
            geometries = [feature.get('geometry') for feature in mask_json.get('features')]
            out_image, out_transform = mask(src, geometries, crop=True, filled=True)
        else:
            # Create raster matching AOI bounds, overlay satellite data where available
            # Get AOI bounds
            aoi_bounds = mask_df.total_bounds  # (minx, miny, maxx, maxy)
            
            # Calculate output dimensions based on source resolution
            left, bottom, right, top = aoi_bounds
            res_x, res_y = src.res
            width = int(np.ceil((right - left) / res_x))
            height = int(np.ceil((top - bottom) / abs(res_y)))
            
            # Create transform for AOI bounds
            from rasterio.transform import from_bounds as transform_from_bounds
            out_transform = transform_from_bounds(left, bottom, right, top, width, height)
            
            # Initialize output array with nodata
            nodata_value = src.nodata if src.nodata is not None else 0
            out_image = np.full((src.count, height, width), nodata_value, dtype=src.dtypes[0])
            
            # Calculate window in source raster that overlaps with AOI
            try:
                window = from_bounds(left, bottom, right, top, src.transform)
                
                # Read the overlapping portion from source
                src_data = src.read(window=window, boundless=True, fill_value=nodata_value)
                
                # Calculate where to place this data in output array
                # Handle cases where AOI extends beyond raster
                src_window_bounds = src.window_bounds(window)
                
                # Calculate pixel offsets in output array
                col_off = max(0, int((src_window_bounds[0] - left) / res_x))
                row_off = max(0, int((top - src_window_bounds[3]) / abs(res_y)))
                
                # Calculate dimensions to copy
                copy_height = min(src_data.shape[1], height - row_off)
                copy_width = min(src_data.shape[2], width - col_off)
                
                # Overlay satellite data onto output array
                out_image[:, row_off:row_off+copy_height, col_off:col_off+copy_width] = \
                    src_data[:, :copy_height, :copy_width]
                    
            except Exception as e:
                print(f"  ⚠️ Warning during overlay: {e}")
                # If overlay fails, just use nodata array

        # Prepare COG profile using the COG driver (requires GDAL 3.1+)
        cog_profile = src.profile.copy()
        cog_profile.update({
            'driver': 'COG',
            'height': out_image.shape[1],
            'width': out_image.shape[2],
            'transform': out_transform,
            'compress': 'DEFLATE',
            'blockxsize': 512,
            'blockysize': 512,
            'BIGTIFF': 'IF_SAFER',
            'OVERVIEW_RESAMPLING': 'NEAREST',
        })

    # Write as COG directly
    with rasterio.open(target_file, 'w', **cog_profile) as dst:
        dst.write(out_image)

def raster_intersects_geojson_data(raster_url, geojson_data):
    """Check if raster intersects with geojson geometry"""
    from shapely.geometry import shape, box
    
    # Get raster bounds
    with rasterio.open(raster_url) as src:
        raster_bounds = src.bounds
        raster_bbox = box(raster_bounds.left, raster_bounds.bottom, raster_bounds.right, raster_bounds.top)
    
    # Get geojson geometry
    geom = shape(geojson_data['features'][0]['geometry'])
    
    # Check intersection
    return raster_bbox.intersects(geom)

def isPreviewSatImageValid(img, nodata_value=255):
    """Check if satellite image preview is valid (not too much no-data)"""
    import numpy as np
    
    if len(img.shape) < 3:
        return False
    
    # Check red band for no-data
    r_band = img[:, :, 0]
    nodata_pct = pct_no_data(r_band, nodata_value)
    
    # Reject if more than 50% no-data
    return nodata_pct < 50

def pct_no_data(band, nodata_value):
    """Calculate percentage of no-data pixels in a band"""
    import numpy as np
    
    total_pixels = band.size
    nodata_pixels = np.sum(band == nodata_value)
    
    return (nodata_pixels / total_pixels) * 100