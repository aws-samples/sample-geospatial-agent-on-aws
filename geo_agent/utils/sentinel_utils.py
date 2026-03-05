"""
Sentinel-2 satellite imagery utilities
"""
import os
import logging
from datetime import datetime, timedelta
import boto3
import tempfile
import sys
sys.path.append('..')
import config

import pystac_client
import rasterio
import numpy as np
from shapely.geometry import shape as shapely_shape

from .geocode_utils import get_polygon_of_aoi, convert_geometry_of_geojson
from .raster_utils import get_raster_crs, clip_raster_v2

logger = logging.getLogger(__name__)

def search_sentinel2_collection(start_date, end_date, aoi_geometry, max_cloud=100):
    """Search Sentinel 2 data collection for a date range"""
    client = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")
    
    search = client.search(
        collections=["sentinel-2-l2a"],
        query={"eo:cloud_cover": {"lt": max_cloud}},
        intersects=aoi_geometry.to_crs("EPSG:4326").geometry[0].__geo_interface__, 
        datetime=f"{start_date}/{end_date}"
    )
    
    s2_items = []
    for item in search.items_as_dicts():
        s2_items.append(item)
        
    return s2_items

def get_all_images_from_gdf(aoi_gdf, bands=None, max_cloud=100, current_date_str=None, days_delta=60):
    """Get all Sentinel-2 images for a given area of interest"""
    if current_date_str == None:
        start_date = datetime.today()
    else:
        start_date = datetime(*map(int, current_date_str.split('-')))

    analysis_start_date = start_date - timedelta(days=days_delta)
    analysis_start_date = analysis_start_date.date()
    analysis_end_date = start_date.date()

    print(f"{analysis_start_date} - {analysis_end_date}")

    # Band name mapping: user-friendly name -> Sentinel-2 asset name
    BAND_MAPPING = {
        "red": "red",        # B4 - 10m
        "green": "green",    # B3 - 10m
        "blue": "blue",      # B2 - 10m
        "nir": "nir",        # B8 - 10m (for NDVI, NDWI)
        "nir08": "nir08",    # B8A - 20m (for NBR - matches SWIR resolution)
        "swir1": "swir16",   # B11 - 20m
        "swir2": "swir22",   # B12 - 20m
    }

    res = search_sentinel2_collection(analysis_start_date, analysis_end_date, aoi_gdf, max_cloud=max_cloud)

    # Get AOI geometry in WGS84 for coverage calculation (pure geometry, no raster I/O)
    aoi_wgs84_geom = aoi_gdf.to_crs("EPSG:4326").geometry.unary_union

    new_res = []
    for itm in res:
        # Extract cloud coverage percentage from metadata
        cloud_pct = itm.get("properties", {}).get("eo:cloud_cover", None)

        # Extract Sentinel-2 tile ID from thumbnail URL
        # Format: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/32/U/PU/2025/7/S2A_32UPU_20250713_0_L2A/preview.jpg
        # Extract tile ID (e.g., 32UPU) from path segments at indices [4], [5], [6]
        url_parts = itm["assets"]["thumbnail"]["href"].split('/')
        tile_id = f"{url_parts[4]}{url_parts[5]}{url_parts[6]}" if "thumbnail" in itm["assets"] and len(url_parts) > 6 else None

        # Calculate AOI coverage from STAC item footprint (pure geometry, no raster I/O)
        try:
            item_geom = shapely_shape(itm["geometry"])
            intersection_area = aoi_wgs84_geom.intersection(item_geom).area
            coverage_pct = round((intersection_area / aoi_wgs84_geom.area) * 100, 1) if aoi_wgs84_geom.area > 0 else 0
        except Exception:
            coverage_pct = 0

        r = {
            "thumbnail": itm["assets"]["thumbnail"]["href"],
            "tci": itm["assets"]["visual"]["href"],
            "scl": itm["assets"]["scl"]["href"] if "scl" in itm["assets"] else None,
            "cloud_pct": cloud_pct,
            "date": itm.get("properties", {}).get("datetime", "Unknown date"),
            "tile_id": tile_id,
            "coverage_pct": coverage_pct
        }

        if bands!=None:
            for b in bands:
                # Map user-friendly band name to Sentinel-2 asset name
                asset_name = BAND_MAPPING.get(b, b)
                if asset_name in itm["assets"]:
                    r[b] = itm["assets"][asset_name]["href"]
        new_res.append(r)
    return new_res

def check_raster_quality(scl_path, max_nodata_pct=30):
    """
    Check if clipped raster has acceptable quality using SCL (cloud mask)
    
    When checking tile coverage, nodata pixels represent areas not covered by the satellite tile.
    
    SCL bad values: 0 (No Data), 1 (Saturated), 3 (Cloud Shadows), 8-10 (Clouds), 11 (Snow)
    SCL good values: 4 (Vegetation), 5 (Not Vegetated), 6 (Water), 7 (Unclassified)

    Args:
        scl_path: Path to clipped SCL file
        max_nodata_pct: Maximum acceptable bad pixel percentage (default 30%)

    Returns:
        tuple: (is_valid: bool, bad_pct: float, good_pct: float)
    """
    try:
        with rasterio.open(scl_path) as scl_src:
            scl_data = scl_src.read(1)
            scl_nodata = scl_src.nodata
            
            # Total pixels in AOI extent (including nodata from incomplete coverage)
            total_pixels = scl_data.size
            
            if total_pixels == 0:
                return False, 100, 0
            
            # Count pixels with data (covered by satellite)
            if scl_nodata is not None:
                pixels_with_data = np.sum(scl_data != scl_nodata)
                nodata_pixels = total_pixels - pixels_with_data
            else:
                pixels_with_data = total_pixels
                nodata_pixels = 0
            
            # Count bad pixels among those with data (clouds, shadows, etc.)
            bad_scl_values = [0, 1, 3, 8, 9, 10, 11]
            if pixels_with_data > 0:
                bad_pixels_in_data = np.sum(np.isin(scl_data[scl_data != scl_nodata], bad_scl_values))
            else:
                bad_pixels_in_data = 0
            
            # Total bad pixels = nodata (not covered) + bad SCL values (clouds, etc.)
            total_bad_pixels = nodata_pixels + bad_pixels_in_data
            good_pixels = total_pixels - total_bad_pixels
            
            good_pct = (good_pixels / total_pixels) * 100
            bad_pct = (total_bad_pixels / total_pixels) * 100
            is_valid = bad_pct <= max_nodata_pct
            
            print(f"  📊 Total: {total_pixels:,} pixels | Good: {good_pct:.1f}% | Bad: {bad_pct:.1f}% (uncovered: {nodata_pixels:,}, clouds/shadows: {bad_pixels_in_data:,})")
            return is_valid, bad_pct, good_pct

    except Exception as e:
        logger.error(f"Error checking raster quality: {e}")
        return False, 100, 0


def get_filtered_images(aoi_gdf, bands=["red", "nir"], max_cloud=30, current_date_str=None, days_delta=60, location=None):
    """
    Get filtered Sentinel-2 images for an AOI
    
    Strategy:
    - Filter tiles that fully cover the AOI (if multiple tiles exist)
    - Sort by cloud coverage
    - Return the best image
    
    Args:
        location: Location name for filename (e.g., "Hyde Park London")
    
    Returns:
        list: List with one image dict, or empty list if none found
    """
    # Validate inputs
    if aoi_gdf is None or aoi_gdf.empty:
        logger.error("Empty or None geometry provided")
        return []

    try:
        all_images = get_all_images_from_gdf(aoi_gdf, bands=bands, max_cloud=max_cloud, 
                                            current_date_str=current_date_str, days_delta=days_delta)
    except Exception as e:
        logger.error(f"Failed to get images from collection: {e}")
        return []

    print(f"🔍 Found {len(all_images)} candidate images")

    if len(all_images) == 0:
        logger.warning(f"No images found with max_cloud={max_cloud} in last {days_delta} days")
        return []

    # Get unique sentinel tile ids
    sentinel_tile_ids = set([img["tile_id"] for img in all_images])
    print(f"🌍 Found {len(sentinel_tile_ids)} unique Sentinel tiles: {sentinel_tile_ids}")

    # Select best tile based on AOI coverage (deterministic, no raster I/O)
    if len(sentinel_tile_ids) > 1:
        tile_coverages = {}
        for tile_id in sentinel_tile_ids:
            tile_images = [img for img in all_images if img["tile_id"] == tile_id]
            # All images from same MGRS tile have same footprint, so coverage_pct is consistent
            avg_coverage = sum(img.get("coverage_pct", 0) for img in tile_images) / len(tile_images)
            tile_coverages[tile_id] = avg_coverage
            print(f"  🌍 Tile {tile_id}: {avg_coverage:.1f}% AOI coverage ({len(tile_images)} images)")

        # Select tile with highest coverage (deterministic - same AOI always picks same tile)
        selected_tile_id = max(tile_coverages, key=tile_coverages.get)
        all_images = [img for img in all_images if img["tile_id"] == selected_tile_id]
        print(f"✅ Selected tile {selected_tile_id} ({tile_coverages[selected_tile_id]:.1f}% coverage, {len(all_images)} images)")
    
    # Convert geometry to the selected tile's CRS
    try:
        raster_crs = get_raster_crs(all_images[0]['tci'])
        aoi_gdf = aoi_gdf.to_crs(raster_crs)
        
        # Keep original geometry - MultiPolygons are supported
        # if aoi_gdf.geometry.iloc[0].geom_type == 'MultiPolygon':
        #     aoi_gdf = convert_geometry_of_geojson(aoi_gdf)
    except Exception as e:
        logger.error(f"Failed to convert geometry: {e}")
        return []

    # Sort by cloud coverage (primary), then AOI coverage as tiebreaker (secondary)
    all_images_sorted = sorted(all_images, key=lambda x: (x.get('cloud_pct', 100), -x.get('coverage_pct', 0)))
    best_image = all_images_sorted[0]

    print(f"📊 Best image: {best_image.get('date', 'unknown')[:10]} with {best_image.get('cloud_pct', 'N/A')}% cloud, {best_image.get('coverage_pct', 'N/A')}% AOI coverage")

    # Process the best image
    s3_client = boto3.client('s3')
    bucket_name = config.S3_BUCKET_NAME
    session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
    
    temp_dir = tempfile.mkdtemp(prefix='sentinel_data_')

    try:
        # Save AOI
        aoi_path = f"{temp_dir}/aoi.geojson"
        aoi_gdf.to_file(aoi_path, driver="GeoJSON")
        
        image_date = best_image.get('date', current_date_str or 'unknown')[:10]
        
        # Clean location name for filename
        clean_location = location.replace(" ", "_").replace(",", "").lower() if location else "unknown"
        
        # Clip and upload TCI (crop to AOI geometry)
        tci_temp = f"{temp_dir}/tci_clipped_{clean_location}_{image_date}.tif"
        clip_raster_v2(aoi_path, f"/vsicurl/{best_image['tci']}", tci_temp, crop_to_aoi=True)
        
        tci_s3_key = f"session_data/{session_id}/rasters/tci_clipped_{clean_location}_{image_date}.tif"
        s3_client.upload_file(tci_temp, bucket_name, tci_s3_key)
        
        result = {
            "tci": best_image['tci'],
            "tci_s3_url": f"s3://{bucket_name}/{tci_s3_key}",
            "thumbnail": best_image['thumbnail'],
            "cloud_pct": best_image.get('cloud_pct', 'N/A'),
            "date": best_image.get('date', 'Unknown date'),
            "tile_id": best_image.get('tile_id', 'Unknown tile ID'),
            "coverage_pct": best_image.get('coverage_pct', 'N/A')
        }
        
        # Process bands (crop to AOI geometry)
        if bands:
            for band in bands:
                if band in best_image:
                    band_temp = f"{temp_dir}/{band}_clipped_{clean_location}_{image_date}.tif"
                    clip_raster_v2(aoi_path, f"/vsicurl/{best_image[band]}", band_temp, crop_to_aoi=True)
                    
                    band_s3_key = f"session_data/{session_id}/rasters/{band}_clipped_{clean_location}_{image_date}.tif"
                    s3_client.upload_file(band_temp, bucket_name, band_s3_key)
                    
                    result[f'{band}_s3_url'] = f"s3://{bucket_name}/{band_s3_key}"
                    result[band] = best_image[band]
        
        print(f"✅ Successfully processed image from {image_date}")
        return [result]
        
    except Exception as e:
        logger.error(f"Failed to process image: {e}")
        return []
        
    finally:
        # Clean up
        try:
            import shutil
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up temp directory: {e}")