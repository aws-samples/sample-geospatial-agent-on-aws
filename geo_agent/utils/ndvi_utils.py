"""
NDVI, NDWI, and NBR calculation utilities
"""
import os
import logging
import tempfile

import numpy as np
import rasterio
import geopandas as gpd
from rasterstats import zonal_stats

#from .raster_utils import get_raster_crs, clip_raster_v2

logger = logging.getLogger(__name__)

async def calculate_ndvi_stats(red_s3_url, nir_s3_url, date_str=None, geometry_s3_url=None, location=None):
    """
    Calculate NDVI statistics from S3 raster URLs and save NDVI as COG to S3

    NDVI (Normalized Difference Vegetation Index) highlights vegetation density:
    - Values (-1, 0]: No vegetation (water, rock, artificial structures)
    - Values (0, 0.5]: Light vegetation (shrubs, grass, fields)
    - Values (0.5, 0.7]: Dense vegetation (plantations)
    - Values (0.7, 1]: Very dense vegetation (rainforest)

    Formula: NDVI = (NIR - Red) / (NIR + Red)
    Range: -1 to 1
    
    Args:
        red_s3_url: S3 URL to red band raster
        nir_s3_url: S3 URL to NIR band raster
        date_str: Date string for filename (YYYY-MM-DD)
        geometry_s3_url: S3 URL to geometry for clipping
        location: Location name for filename (e.g., "Hyde Park London")
    
    Returns:
        Dictionary with statistics and area per vegetation class
    """
    import boto3
    import shutil
    import sys
    from datetime import datetime
    sys.path.append('..')
    import config
    from .raster_utils import clip_raster_v2
    from .aws_utils import download_geometry_from_s3
    
    temp_dir = tempfile.mkdtemp(prefix='ndvi_data_')

    try:
        # Download rasters from S3
        s3_client = boto3.client('s3')

        # Parse S3 URLs
        red_bucket, red_key = red_s3_url.replace('s3://', '').split('/', 1)
        nir_bucket, nir_key = nir_s3_url.replace('s3://', '').split('/', 1)
        
        # Download files
        red_filename = f"{temp_dir}/red_clipped_.tif"
        nir_filename = f"{temp_dir}/nir_clipped.tif"
        
        s3_client.download_file(red_bucket, red_key, red_filename)
        s3_client.download_file(nir_bucket, nir_key, nir_filename)
        
        print("calculating NDVI")
        # Calculate NDVI
        with rasterio.open(red_filename) as red_file:
            red = red_file.read(1)
            kwargs = red_file.meta
        
        with rasterio.open(nir_filename) as nir_file:
            nir = nir_file.read(1)
        
        # NDVI calculation: (NIR - Red) / (NIR + Red)
        ndvi = (nir.astype(float) - red.astype(float)) / (nir + red)
        
        # Save NDVI as COG to S3
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME
        
        # Create temporary NDVI file
        ndvi_temp_path = f'{temp_dir}/ndvi_temp.tif'
        kwargs.update({
            'driver': 'GTiff',
            'dtype': rasterio.float32,
            'count': 1,
        })
        
        with rasterio.open(ndvi_temp_path, 'w', **kwargs) as dst:
            dst.write_band(1, ndvi.astype(rasterio.float32))
        
        # Clip NDVI to original geometry if provided
        ndvi_cog_path = f'{temp_dir}/ndvi_cog.tif'
        if geometry_s3_url:
            print(f"Clipping NDVI to geometry: {geometry_s3_url}")
            # Download geometry and clip NDVI to it
            aoi_gdf = download_geometry_from_s3(geometry_s3_url)
            print(f"Downloaded geometry with CRS: {aoi_gdf.crs}")
            
            # Reproject geometry to match raster CRS
            with rasterio.open(ndvi_temp_path) as src:
                raster_crs = src.crs
                print(f"Raster CRS: {raster_crs}")
                aoi_gdf = aoi_gdf.to_crs(raster_crs)
                print(f"Reprojected geometry to: {aoi_gdf.crs}")
        
            aoi_path = f"{temp_dir}/aoi.geojson"
            aoi_gdf.to_file(aoi_path, driver='GeoJSON')
            print(f"Saved reprojected geometry to: {aoi_path}")
            clip_raster_v2(aoi_path, ndvi_temp_path, ndvi_cog_path)
            print(f"Clipped NDVI saved to: {ndvi_cog_path}")
        else:
            print("No geometry provided, skipping clip")
            # No geometry provided, just convert to COG
            import shutil
            shutil.copy(ndvi_temp_path, ndvi_cog_path)
        
        # Upload NDVI to S3
        if not date_str:
            date_str = datetime.today().strftime("%Y-%m-%d")
        
        # Clean location name for filename
        clean_location = location.replace(" ", "_").replace(",", "").lower() if location else "unknown"
        
        ndvi_s3_key = f"session_data/{session_id}/rasters/ndvi_clipped_{clean_location}_{date_str}.tif"
        s3_client.upload_file(ndvi_cog_path, bucket_name, ndvi_s3_key)
        ndvi_s3_url = f"s3://{bucket_name}/{ndvi_s3_key}"
        
        print("analysing vegetation index raster")
        # Calculate statistics directly from NDVI array
        valid_ndvi = ndvi[~np.isnan(ndvi) & ~np.isinf(ndvi)]
        
        # Get pixel resolution to calculate area
        with rasterio.open(ndvi_cog_path) as src:
            transform = src.transform
            pixel_width = abs(transform.a)  # pixel width in CRS units
            pixel_height = abs(transform.e)  # pixel height in CRS units
            pixel_area_m2 = pixel_width * pixel_height  # area per pixel in m²
        
        # NDVI Classification:
        # (-1, 0]: No vegetation (water, rock, artificial structures)
        # (0, 0.5]: Light vegetation (shrubs, grass, fields)
        # (0.5, 0.7]: Dense vegetation (plantations)
        # (0.7, 1]: Very dense vegetation (rainforest)
        
        no_vegetation_pixels = np.sum(valid_ndvi <= 0)  # No vegetation
        light_vegetation_pixels = np.sum((valid_ndvi > 0) & (valid_ndvi <= 0.5))  # Light vegetation
        dense_vegetation_pixels = np.sum((valid_ndvi > 0.5) & (valid_ndvi <= 0.7))  # Dense vegetation
        very_dense_vegetation_pixels = np.sum(valid_ndvi > 0.7)  # Very dense vegetation
        total_pixels = len(valid_ndvi)
        
        # Calculate areas in m²
        no_vegetation_area_m2 = no_vegetation_pixels * pixel_area_m2
        light_vegetation_area_m2 = light_vegetation_pixels * pixel_area_m2
        dense_vegetation_area_m2 = dense_vegetation_pixels * pixel_area_m2
        very_dense_vegetation_area_m2 = very_dense_vegetation_pixels * pixel_area_m2
        
        # Calculate percentages
        no_vegetation_pct = (no_vegetation_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        light_vegetation_pct = (light_vegetation_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        dense_vegetation_pct = (dense_vegetation_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        very_dense_vegetation_pct = (very_dense_vegetation_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        
        return {
            'min': float(np.min(valid_ndvi)),
            'max': float(np.max(valid_ndvi)),
            'mean': float(np.mean(valid_ndvi)),
            'median': float(np.median(valid_ndvi)),
            'count': int(len(valid_ndvi)),
            'no_vegetation_percentage': float(no_vegetation_pct),
            'no_vegetation_area_m2': float(no_vegetation_area_m2),
            'light_vegetation_percentage': float(light_vegetation_pct),
            'light_vegetation_area_m2': float(light_vegetation_area_m2),
            'dense_vegetation_percentage': float(dense_vegetation_pct),
            'dense_vegetation_area_m2': float(dense_vegetation_area_m2),
            'very_dense_vegetation_percentage': float(very_dense_vegetation_pct),
            'very_dense_vegetation_area_m2': float(very_dense_vegetation_area_m2),
            'ndvi_s3_url': ndvi_s3_url
        }
    
    finally:
        # Clean up temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


async def calculate_ndwi_stats(green_s3_url, nir_s3_url, date_str=None, geometry_s3_url=None, location=None):
    """
    Calculate NDWI statistics from S3 raster URLs and save NDWI as COG to S3

    NDWI (Normalized Difference Water Index) highlights water bodies:
    - Values > 0.3: Water bodies (lakes, rivers, wetlands, flooded areas)
    - Values 0.1-0.3: Vegetation/moisture
    - Values 0-0.1: Built-up areas
    - Values < 0: Other (dry land, bare soil)

    Formula: NDWI = (Green - NIR) / (Green + NIR)
    Range: -1 to 1
    
    Args:
        green_s3_url: S3 URL to green band raster
        nir_s3_url: S3 URL to NIR band raster
        date_str: Date string for filename (YYYY-MM-DD)
        geometry_s3_url: S3 URL to geometry for clipping
        location: Location name for filename (e.g., "Lake Mead Nevada")
    
    Returns:
        Dictionary with statistics and area per land cover class
    """
    import boto3
    import shutil
    import sys
    from datetime import datetime
    sys.path.append('..')
    import config
    from .raster_utils import clip_raster_v2
    from .aws_utils import download_geometry_from_s3

    temp_dir = tempfile.mkdtemp(prefix='ndwi_data_')

    try:
        # Download rasters from S3
        s3_client = boto3.client('s3')

        # Parse S3 URLs
        green_bucket, green_key = green_s3_url.replace('s3://', '').split('/', 1)
        nir_bucket, nir_key = nir_s3_url.replace('s3://', '').split('/', 1)

        # Download files
        green_filename = f"{temp_dir}/green_clipped.tif"
        nir_filename = f"{temp_dir}/nir_clipped.tif"

        s3_client.download_file(green_bucket, green_key, green_filename)
        s3_client.download_file(nir_bucket, nir_key, nir_filename)

        print("calculating NDWI")
        # Calculate NDWI
        with rasterio.open(green_filename) as green_file:
            green = green_file.read(1)
            kwargs = green_file.meta

        with rasterio.open(nir_filename) as nir_file:
            nir = nir_file.read(1)

        # NDWI calculation: (Green - NIR) / (Green + NIR)
        ndwi = (green.astype(float) - nir.astype(float)) / (green + nir)

        # Save NDWI as COG to S3
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME

        # Create temporary NDWI file
        ndwi_temp_path = f'{temp_dir}/ndwi_temp.tif'
        kwargs.update({
            'driver': 'GTiff',
            'dtype': rasterio.float32,
            'count': 1,
        })

        with rasterio.open(ndwi_temp_path, 'w', **kwargs) as dst:
            dst.write_band(1, ndwi.astype(rasterio.float32))

        # Clip NDWI to original geometry if provided
        ndwi_cog_path = f'{temp_dir}/ndwi_cog.tif'
        if geometry_s3_url:
            # Download geometry and clip NDWI to it
            aoi_gdf = download_geometry_from_s3(geometry_s3_url)

            # Reproject geometry to match raster CRS
            with rasterio.open(ndwi_temp_path) as src:
                raster_crs = src.crs
                aoi_gdf = aoi_gdf.to_crs(raster_crs)

            aoi_path = f"{temp_dir}/aoi.geojson"
            aoi_gdf.to_file(aoi_path, driver='GeoJSON')
            clip_raster_v2(aoi_path, ndwi_temp_path, ndwi_cog_path)
        else:
            # No geometry provided, just convert to COG
            import shutil
            shutil.copy(ndwi_temp_path, ndwi_cog_path)

        # Upload NDWI to S3
        if not date_str:
            date_str = datetime.today().strftime("%Y-%m-%d")
        
        # Clean location name for filename
        clean_location = location.replace(" ", "_").replace(",", "").lower() if location else "unknown"
        
        ndwi_s3_key = f"session_data/{session_id}/rasters/ndwi_clipped_{clean_location}_{date_str}.tif"
        s3_client.upload_file(ndwi_cog_path, bucket_name, ndwi_s3_key)
        ndwi_s3_url = f"s3://{bucket_name}/{ndwi_s3_key}"

        print("analysing water index raster")
        # Calculate statistics directly from NDWI array
        valid_ndwi = ndwi[~np.isnan(ndwi) & ~np.isinf(ndwi)]

        # Get pixel resolution to calculate area
        with rasterio.open(ndwi_cog_path) as src:
            transform = src.transform
            pixel_width = abs(transform.a)  # pixel width in CRS units
            pixel_height = abs(transform.e)  # pixel height in CRS units
            pixel_area_m2 = pixel_width * pixel_height  # area per pixel in m²

        # Binary NDWI Classification:
        # Values > 0.1: Water bodies
        # Values <= 0.1: Non-water
        
        water_pixels = np.sum(valid_ndwi > 0.1)
        non_water_pixels = np.sum(valid_ndwi <= 0.1)
        total_pixels = len(valid_ndwi)
        
        # Calculate areas in m²
        water_area_m2 = water_pixels * pixel_area_m2
        non_water_area_m2 = non_water_pixels * pixel_area_m2
        
        # Calculate percentages
        water_pct = (water_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        non_water_pct = (non_water_pixels / total_pixels) * 100 if total_pixels > 0 else 0

        return {
            'min': float(np.min(valid_ndwi)),
            'max': float(np.max(valid_ndwi)),
            'mean': float(np.mean(valid_ndwi)),
            'median': float(np.median(valid_ndwi)),
            'count': int(len(valid_ndwi)),
            'water_percentage': float(water_pct),
            'water_area_m2': float(water_area_m2),
            'non_water_percentage': float(non_water_pct),
            'non_water_area_m2': float(non_water_area_m2),
            'ndwi_s3_url': ndwi_s3_url
        }

    finally:
        # Clean up temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


async def calculate_nbr_stats(nir_s3_url, swir2_s3_url, date_str=None, geometry_s3_url=None, location=None):
    """
    Calculate NBR statistics from S3 raster URLs and save NBR as COG to S3

    NBR (Normalized Burn Ratio) highlights burned areas and fire severity:
    - Values > 0.1: Healthy vegetation (unburned)
    - Values -0.1 to 0.1: Moderate burn severity
    - Values < -0.1: High burn severity (severe fire damage)

    Formula: NBR = (NIR08 - SWIR2) / (NIR08 + SWIR2)
    Range: -1 to 1
    Note: Uses NIR08 (20m) and SWIR2 (20m) bands for matching resolution
    
    Args:
        nir_s3_url: S3 URL to NIR08 band raster (20m resolution)
        swir2_s3_url: S3 URL to SWIR2 band raster (20m resolution)
        date_str: Date string for filename (YYYY-MM-DD)
        geometry_s3_url: S3 URL to geometry for clipping
        location: Location name for filename (e.g., "Paradise CA")
    
    Returns:
        Dictionary with statistics and area per burn severity class
    """
    import boto3
    import shutil
    import sys
    from datetime import datetime
    sys.path.append('..')
    import config
    from .raster_utils import clip_raster_v2
    from .aws_utils import download_geometry_from_s3

    temp_dir = tempfile.mkdtemp(prefix='nbr_data_')

    try:
        # Download rasters from S3
        s3_client = boto3.client('s3')

        # Parse S3 URLs
        nir_bucket, nir_key = nir_s3_url.replace('s3://', '').split('/', 1)
        swir2_bucket, swir2_key = swir2_s3_url.replace('s3://', '').split('/', 1)

        # Download files
        nir_filename = f"{temp_dir}/nir_clipped.tif"
        swir2_filename = f"{temp_dir}/swir2_clipped.tif"

        s3_client.download_file(nir_bucket, nir_key, nir_filename)
        s3_client.download_file(swir2_bucket, swir2_key, swir2_filename)

        print("calculating NBR")
        # Calculate NBR
        with rasterio.open(nir_filename) as nir_file:
            nir = nir_file.read(1)
            kwargs = nir_file.meta

        with rasterio.open(swir2_filename) as swir2_file:
            swir2 = swir2_file.read(1)

        # NBR calculation: (NIR - SWIR2) / (NIR + SWIR2)
        nbr = (nir.astype(float) - swir2.astype(float)) / (nir + swir2)

        # Save NBR as COG to S3
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME

        # Create temporary NBR file
        nbr_temp_path = f'{temp_dir}/nbr_temp.tif'
        kwargs.update({
            'driver': 'GTiff',
            'dtype': rasterio.float32,
            'count': 1,
        })

        with rasterio.open(nbr_temp_path, 'w', **kwargs) as dst:
            dst.write_band(1, nbr.astype(rasterio.float32))

        # Clip NBR to original geometry if provided
        nbr_cog_path = f'{temp_dir}/nbr_cog.tif'
        if geometry_s3_url:
            # Download geometry and clip NBR to it
            aoi_gdf = download_geometry_from_s3(geometry_s3_url)

            # Reproject geometry to match raster CRS
            with rasterio.open(nbr_temp_path) as src:
                raster_crs = src.crs
                aoi_gdf = aoi_gdf.to_crs(raster_crs)

            aoi_path = f"{temp_dir}/aoi.geojson"
            aoi_gdf.to_file(aoi_path, driver='GeoJSON')
            clip_raster_v2(aoi_path, nbr_temp_path, nbr_cog_path)
        else:
            # No geometry provided, just convert to COG
            import shutil
            shutil.copy(nbr_temp_path, nbr_cog_path)

        # Upload NBR to S3
        if not date_str:
            date_str = datetime.today().strftime("%Y-%m-%d")
        
        # Clean location name for filename
        clean_location = location.replace(" ", "_").replace(",", "").lower() if location else "unknown"
        
        nbr_s3_key = f"session_data/{session_id}/rasters/nbr_clipped_{clean_location}_{date_str}.tif"
        s3_client.upload_file(nbr_cog_path, bucket_name, nbr_s3_key)
        nbr_s3_url = f"s3://{bucket_name}/{nbr_s3_key}"

        print("analysing burn index raster")
        # Calculate statistics directly from NBR array
        valid_nbr = nbr[~np.isnan(nbr) & ~np.isinf(nbr)]

        # Get pixel resolution to calculate area
        with rasterio.open(nbr_cog_path) as src:
            transform = src.transform
            pixel_width = abs(transform.a)  # pixel width in CRS units
            pixel_height = abs(transform.e)  # pixel height in CRS units
            pixel_area_m2 = pixel_width * pixel_height  # area per pixel in m²

        # NBR Classification based on proper thresholds:
        # Values > 0.1: Healthy vegetation (unburned)
        # Values -0.1 to 0.1: Moderate burn severity
        # Values < -0.1: High burn severity (severe fire damage)
        
        high_severity_pixels = np.sum(valid_nbr < -0.1)  # Severe burn
        moderate_severity_pixels = np.sum((valid_nbr >= -0.1) & (valid_nbr <= 0.1))  # Moderate burn
        unburned_pixels = np.sum(valid_nbr > 0.1)  # Healthy vegetation
        total_pixels = len(valid_nbr)

        # Calculate areas in m²
        high_severity_area_m2 = high_severity_pixels * pixel_area_m2
        moderate_severity_area_m2 = moderate_severity_pixels * pixel_area_m2
        unburned_area_m2 = unburned_pixels * pixel_area_m2
        
        # Calculate percentages
        high_severity_pct = (high_severity_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        moderate_severity_pct = (moderate_severity_pixels / total_pixels) * 100 if total_pixels > 0 else 0
        unburned_pct = (unburned_pixels / total_pixels) * 100 if total_pixels > 0 else 0

        return {
            'min': float(np.min(valid_nbr)),
            'max': float(np.max(valid_nbr)),
            'mean': float(np.mean(valid_nbr)),
            'median': float(np.median(valid_nbr)),
            'count': int(len(valid_nbr)),
            'high_severity_percentage': float(high_severity_pct),
            'high_severity_area_m2': float(high_severity_area_m2),
            'moderate_severity_percentage': float(moderate_severity_pct),
            'moderate_severity_area_m2': float(moderate_severity_area_m2),
            'unburned_percentage': float(unburned_pct),
            'unburned_area_m2': float(unburned_area_m2),
            'nbr_s3_url': nbr_s3_url
        }

    finally:
        # Clean up temp directory
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)