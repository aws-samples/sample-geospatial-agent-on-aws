"""
Strands tool definitions for geospatial analysis
"""
import json
import logging
import os
import tempfile
from datetime import datetime
from io import BytesIO
import boto3
import math
import numpy as np
import rasterio

from strands import tool
from strands_tools import calculator as calculator_tool
from .geocode_utils import get_polygon_of_aoi, geojson_str_to_gdf, haversine_distance
from .sentinel_utils import get_filtered_images
from .ndvi_utils import calculate_ndvi_stats, calculate_ndwi_stats, calculate_nbr_stats
from .aws_utils import download_from_s3, download_geometry_from_s3, list_files_in_s3

import config

logger = logging.getLogger(__name__)


def _log_mem(tag: str) -> None:
    """Log current and peak process RSS so crashes leave a memory trail.

    Uses only the stdlib: peak RSS from resource.getrusage (KB on Linux) and
    current RSS from /proc/self/status. Best-effort — never raises.
    """
    try:
        import resource
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        cur_mb = -1.0
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        cur_mb = int(line.split()[1]) / 1024.0
                        break
        except Exception:
            pass
        logger.info("🧠 MEM[%s] current=%.0fMB peak=%.0fMB", tag, cur_mb, peak_mb)
    except Exception:
        pass

#####################################
### UTILITY TOOLS
#####################################

# Built-in calculator tool from strands_tools
calculator = calculator_tool

# re-use previous assets that were generated
@tool
async def list_session_assets() -> str:
    """List all session assets (geometries, images, analysis). Use to reuse existing data and avoid regeneration.
    
    Returns: JSON with asset metadata
    
    Call this in multi-turn conversations to check if data already exists before regenerating."""

    session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
    
    # Get all assets in the session
    assets = list_files_in_s3(config.S3_BUCKET_NAME, f"session_data/{session_id}/")
    
    # Emit result marker for frontend parsing
    result = {
        "session_id": session_id,
        "assets": assets
    }
    result_json = json.dumps(result, indent=2)
    print(f"\n<tool_result_output>\n{result_json}\n</tool_result_output>\n")
    return result_json

# visualization tool for sending map data to map
@tool
async def display_visual(s3_url: str, title: str, description: str = "") -> str:
    """Display geometry or imagery (TCI, NDVI, NDWI, NBR) on map.
    
    Args:
        s3_url: S3 URL to GeoJSON or raster
        title: Display title
        description: Optional details
    
    Returns: JSON with display metadata
    
    Call IMMEDIATELY after each result: geometry → display, TCI → display, NDVI → display. Don't batch."""
    result = {"status": "success"}
    result_json = json.dumps(result, indent=2)
    
    return result_json

#####################################
# GEOCODING TOOLS
#####################################

@tool
def bbox_around_point(lon: float, lat: float, distance_offset_meters: int = 2000) -> str:
    """Create bounding box around point.
    
    Args:
        lon: Longitude
        lat: Latitude
        distance_offset_meters: Buffer distance (default 2000m)
    
    Returns: GeoJSON polygon"""
    try:
        # Validate inputs
        lon = float(lon)
        lat = float(lat)
        distance_offset_meters = int(distance_offset_meters)
        
        # Equatorial radius (km) taken from https://nssdc.gsfc.nasa.gov/planetary/factsheet/earthfact.html
        earth_radius_meters = 6378137
        lat_offset = math.degrees(distance_offset_meters / earth_radius_meters)
        lon_offset = math.degrees(distance_offset_meters / (earth_radius_meters * math.cos(math.radians(lat))))
        
        # Round coordinates to avoid floating point precision issues
        coords = [
            [round(lon - lon_offset, 8), round(lat - lat_offset, 8)],
            [round(lon - lon_offset, 8), round(lat + lat_offset, 8)],
            [round(lon + lon_offset, 8), round(lat + lat_offset, 8)],
            [round(lon + lon_offset, 8), round(lat - lat_offset, 8)],
            [round(lon - lon_offset, 8), round(lat - lat_offset, 8)],
        ]
        
        result = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords]
            },
            "properties": {
                "center": [round(lon, 8), round(lat, 8)],
                "radius_meters": distance_offset_meters
            }
        }
        
        # Return as JSON string for better compatibility
        return json.dumps(result, separators=(',', ':'))
        
    except (ValueError, TypeError) as e:
        error_msg = f"Invalid input parameters: {e}"
        print(f"ERROR: {error_msg}")
        return json.dumps({"error": error_msg})
    except Exception as e:
        error_msg = f"Unexpected error in bbox_around_point: {e}"
        print(f"ERROR: {error_msg}")
        return json.dumps({"error": error_msg})

@tool
async def create_bbox_from_coordinates(geometry_json: str, location: str = "custom_area") -> str:
    """Create geometry file from user-drawn coordinates, Point, or Polygon GeoJSON.
    
    Args:
        geometry_json: GeoJSON string containing Point or Polygon features
        location: Name for the geometry (default: "custom_area")
    
    Returns: JSON with geometry_s3_url and coordinates
    
    Handles:
    - Single Point: Creates 2km bbox around point
    - Polygon: Saves as-is (if within size limit)
    - FeatureCollection: Extracts first feature
    
    Output format matches bbox_around_point for consistency."""

    max_size_km2=config.MAX_CUSTOM_AREA_SIZE_KM2

    try:
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME
        
        # Parse the GeoJSON
        geojson_data = json.loads(geometry_json)
        
        # Handle FeatureCollection vs single Feature
        if geojson_data.get('type') == 'FeatureCollection':
            if not geojson_data.get('features'):
                return json.dumps({"error": "FeatureCollection is empty"})
            feature = geojson_data['features'][0]
        elif geojson_data.get('type') == 'Feature':
            feature = geojson_data
        else:
            # Assume it's a geometry object directly
            feature = {
                "type": "Feature",
                "geometry": geojson_data,
                "properties": {}
            }
        
        geometry = feature['geometry']
        geometry_type = geometry['type']
        
        logger.info(f"📍 Creating geometry file for {location} (type: {geometry_type})")
        
        # Handle Point - create bbox around it
        if geometry_type == 'Point':
            coords = geometry['coordinates']
            lon, lat = coords[0], coords[1]
            
            logger.info(f"   Point coordinates: ({lat:.6f}, {lon:.6f})")
            
            # Create 2km bounding box using existing function
            bbox_geojson_str = bbox_around_point(lon, lat, 2000)
            
            # Convert to GeoDataFrame
            gdf = geojson_str_to_gdf(bbox_geojson_str)
            if gdf is None or gdf.empty:
                return json.dumps({"error": f"Failed to create bounding box for point"})
            
            # Save to S3
            s3_client = boto3.client('s3')
            clean_location = location.replace(" ", "_").replace(",", "").lower()
            s3_key = f"session_data/{session_id}/geometries/point_bbox_{clean_location}.geojson"
            
            geojson_str = gdf.to_json()
            
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=geojson_str.encode('utf-8'),
                ContentType='application/geo+json'
            )
            
            s3_url = f"s3://{bucket_name}/{s3_key}"
            logger.info(f"✅ Point bbox saved to {s3_url}")
            
            return json.dumps({
                "geometry_s3_url": s3_url,
                "location": clean_location,
                "type": "point_bbox",
                "center": {"lat": round(lat, 6), "lon": round(lon, 6)},
                "radius_meters": 2000
            })
        
        # Handle Polygon or MultiPolygon - save as-is
        elif geometry_type in ['Polygon', 'MultiPolygon']:
            # Convert to GeoDataFrame
            gdf = geojson_str_to_gdf(json.dumps(feature))
            if gdf is None or gdf.empty:
                return json.dumps({"error": f"Failed to parse {geometry_type}"})
            
            # Calculate centroid for reference
            gdf_wgs84 = gdf.to_crs('EPSG:4326')
            centroid = gdf_wgs84.geometry.centroid.iloc[0]
            centroid_lat = centroid.y
            centroid_lon = centroid.x
            
            # Calculate area
            gdf_projected = gdf.to_crs('EPSG:6933')
            area_m2 = gdf_projected.geometry.area.sum()
            area_km2 = area_m2 / 1_000_000
            
            logger.info(f"   Polygon area: {area_km2:.2f} km²")
            logger.info(f"   Centroid: ({centroid_lat:.6f}, {centroid_lon:.6f})")
            
            # Check if polygon exceeds maximum size
            if area_km2 > max_size_km2:
                error_msg = (
                    f"Polygon area ({area_km2:.2f} km²) exceeds maximum allowed size ({max_size_km2:.2f} km²). "
                    f"Please provide a smaller area or increase the max_size_km2 parameter."
                )
                logger.warning(f"⚠️ {error_msg}")
                return json.dumps({
                    "error": error_msg,
                    "area_km2": round(area_km2, 2),
                    "max_allowed_km2": max_size_km2,
                    "centroid": {"lat": round(centroid_lat, 6), "lon": round(centroid_lon, 6)}
                })
            
            # Save to S3
            s3_client = boto3.client('s3')
            clean_location = location.replace(" ", "_").replace(",", "").lower()
            s3_key = f"session_data/{session_id}/geometries/polygon_{clean_location}.geojson"
            
            geojson_str = gdf.to_json()
            
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=geojson_str.encode('utf-8'),
                ContentType='application/geo+json'
            )
            
            s3_url = f"s3://{bucket_name}/{s3_key}"
            logger.info(f"✅ Polygon saved to {s3_url}")
            
            return json.dumps({
                "geometry_s3_url": s3_url,
                "location": clean_location,
                "type": "polygon",
                "area_km2": round(area_km2, 2),
                "centroid": {"lat": round(centroid_lat, 6), "lon": round(centroid_lon, 6)}
            })
        
        else:
            return json.dumps({"error": f"Unsupported geometry type: {geometry_type}. Only Point, Polygon, and MultiPolygon are supported."})
            
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})
    except Exception as e:
        error_msg = f"Error creating geometry file: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})


@tool
async def find_location_boundary(location: str) -> str:
    """Get exact OSM boundary polygon for named location.
    
    Args:
        location: Place name (e.g., "Central Park", "Paris")
    
    Returns: JSON with geometry_s3_url and location name
    
    Use for: Getting precise boundaries of parks, cities, regions from OpenStreetMap"""
    try:
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME

        polygon = get_polygon_of_aoi(location)
        if polygon is None or polygon.empty:
            return f"❌ No polygon found for {location}"
        else:
            polygon = polygon.to_crs(epsg=4326)

        # Save to S3 as GeoJSON
        s3_client = boto3.client('s3')

        # Clean location name for filename
        clean_location = location.replace(" ", "_").replace(",", "").lower()
        s3_key = f"session_data/{session_id}/geometries/polygon_{clean_location}.geojson"

        # Convert to GeoJSON string
        geojson_str = polygon.to_json()

        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=geojson_str.encode('utf-8'),
            ContentType='application/geo+json'
        )

        s3_url = f"s3://{bucket_name}/{s3_key}"

        logger.info(f"✅ Polygon saved to {s3_url}")

        return json.dumps({
            "geometry_s3_url": s3_url,
            "location": clean_location})
        
    except Exception as e:
        error_msg = f"❌ Error getting polygon for {location}: {str(e)}"
        logger.error(error_msg)
        return error_msg


@tool
async def get_best_geometry(location: str, osm_s3_url: str, reference_lat: float, reference_lon: float, max_area_km2: float = 100) -> str:
    """Validate OSM geometry against reference coords. Returns best geometry (OSM or bbox fallback).
    
    Args:
        location: Place name
        osm_s3_url: OSM polygon from find_location_boundary
        reference_lat: Lat from search_places
        reference_lon: Lon from search_places
        max_area_km2: Max area threshold (default 100)
    
    Returns: JSON with validated geometry_s3_url, source type, validation details
    
    Workflow: Call search_places + find_location_boundary in parallel → get_best_geometry validates → use returned geometry_s3_url"""
    try:
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME

        # Convert to float
        reference_lat = float(reference_lat)
        reference_lon = float(reference_lon)
        max_area_km2 = float(max_area_km2)

        logger.info(f"🔍 Validating geometry for {location}")
        logger.info(f"   Reference coords: ({reference_lat:.4f}, {reference_lon:.4f})")
        logger.info(f"   Max area allowed: {max_area_km2} km²")

        # 1. Try to load OSM geometry
        try:
            osm_gdf = download_geometry_from_s3(osm_s3_url)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load OSM geometry: {e}")
            osm_gdf = None

        # 2. Check if OSM geometry is empty or failed to load
        if osm_gdf is None or osm_gdf.empty:
            logger.warning("⚠️ OSM geometry is empty or failed to load")
            reason = "OSM geometry empty or invalid"
            # Fallback: create bbox from coordinates
            return await _create_fallback_bbox(location, reference_lat, reference_lon, reason, session_id, bucket_name)

        # 3. Calculate area in km²
        # Project to equal-area projection (EPSG:6933) for accurate area calculation
        osm_gdf_projected = osm_gdf.to_crs('EPSG:6933')
        area_m2 = osm_gdf_projected.geometry.area.sum()
        area_km2 = area_m2 / 1_000_000

        logger.info(f"   OSM area: {area_km2:.2f} km²")

        if area_km2 > max_area_km2:
            logger.warning(f"⚠️ OSM area too large ({area_km2:.1f} km² > {max_area_km2} km²)")
            reason = f"OSM too large ({area_km2:.1f} km²)"
            return await _create_fallback_bbox(location, reference_lat, reference_lon, reason, session_id, bucket_name)

        # 4. Calculate centroid in WGS84
        osm_gdf_wgs84 = osm_gdf.to_crs('EPSG:4326')
        centroid = osm_gdf_wgs84.geometry.centroid.iloc[0]
        centroid_lat = centroid.y
        centroid_lon = centroid.x

        logger.info(f"   OSM centroid: ({centroid_lat:.4f}, {centroid_lon:.4f})")

        # 5. Calculate distance between OSM centroid and reference coordinates
        distance_km = haversine_distance(centroid_lat, centroid_lon, reference_lat, reference_lon)

        logger.info(f"   Distance from reference: {distance_km:.2f} km")

        # 6. Adaptive distance threshold based on area
        # Larger areas can have centroids further from search coordinates
        # Use square root of area as a reasonable scaling factor
        min_threshold_km = 10  # Minimum threshold for small areas
        adaptive_threshold_km = max(min_threshold_km, math.sqrt(area_km2) * 2)
        max_threshold_km = 50  # Cap at 50km to avoid accepting wrong locations
        distance_threshold_km = min(adaptive_threshold_km, max_threshold_km)

        logger.info(f"   Distance threshold: {distance_threshold_km:.2f} km")

        if distance_km > distance_threshold_km:
            logger.warning(f"⚠️ OSM centroid too far from reference ({distance_km:.1f} km > {distance_threshold_km:.1f} km)")
            reason = f"OSM centroid too far ({distance_km:.1f} km from reference)"
            return await _create_fallback_bbox(location, reference_lat, reference_lon, reason, session_id, bucket_name)

        # 7. OSM validated! Return it
        logger.info(f"✅ OSM geometry validated successfully")

        return json.dumps({
            "geometry_s3_url": osm_s3_url,
            "source": "osm",
            "location": location,
            "validation": {
                "area_km2": round(area_km2, 2),
                "distance_km": round(distance_km, 2),
                "threshold_km": round(distance_threshold_km, 2),
                "osm_centroid": {"lat": round(centroid_lat, 6), "lon": round(centroid_lon, 6)},
                "reference_coords": {"lat": round(reference_lat, 6), "lon": round(reference_lon, 6)}
            },
            "reason": f"OSM validated (area: {area_km2:.1f} km², distance: {distance_km:.1f} km)"
        })

    except Exception as e:
        error_msg = f"❌ Error validating geometry for {location}: {str(e)}"
        logger.error(error_msg)
        # On error, fallback to bbox
        try:
            return await _create_fallback_bbox(location, reference_lat, reference_lon, f"Validation error: {str(e)}", session_id, bucket_name)
        except:
            return json.dumps({"error": error_msg})


async def _create_fallback_bbox(location: str, lat: float, lon: float, reason: str, session_id: str, bucket_name: str) -> str:
    """Helper function to create fallback bounding box when OSM validation fails"""
    logger.info(f"📦 Creating fallback 2km bounding box")
    logger.info(f"   Reason: {reason}")

    # Create 2km bounding box
    bbox_geojson_str = bbox_around_point(lon, lat, 2000)

    # Convert to GeoDataFrame
    gdf = geojson_str_to_gdf(bbox_geojson_str)
    if gdf is None or gdf.empty:
        raise Exception(f"Failed to create bounding box for {location}")

    # Save to S3 as GeoJSON
    s3_client = boto3.client('s3')
    clean_location = location.replace(" ", "_").replace(",", "").lower()
    s3_key = f"session_data/{session_id}/geometries/bbox_{clean_location}.geojson"

    # Convert to GeoJSON
    geojson_str = gdf.to_json()

    s3_client.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=geojson_str.encode('utf-8'),
        ContentType='application/geo+json'
    )

    s3_url = f"s3://{bucket_name}/{s3_key}"
    logger.info(f"✅ Fallback bbox saved to {s3_url}")

    return json.dumps({
        "geometry_s3_url": s3_url,
        "source": "bbox",
        "location": location,
        "reason": reason,
        "fallback": True
    })


#####################################
### SATELLITE IMAGERY RETRIEVAL TOOLS
#####################################

@tool
async def get_rasters(location: str, geometry_s3_url: str = None, current_date_str: str = None, max_cloud: float = 30) -> str:
    """Get Sentinel-2 satellite imagery bands. Searches BACKWARDS 60 days from current_date_str.

    Args:
        location: Place name (for filenames)
        geometry_s3_url: Boundary from find_location_boundary or create_bbox_from_coordinates
        current_date_str: END date of 60-day search window (YYYY-MM-DD). Searches from (date - 60 days) to date.
            For PRE-event imagery: use a date BEFORE the event (e.g., "2024-12-31" for Jan 2025 fire).
            For POST-event imagery: use a date 1-2 months AFTER the event (e.g., "2025-03-01" for Jan 2025 fire).
        max_cloud: Max cloud % (default 30, retries at 80 if no results)

    Returns: JSON with tci_s3_url, red_s3_url, green_s3_url, nir_s3_url, nir08_s3_url, swir2_s3_url, date_used, cloud_pct

    Use date_used in subsequent analysis calls. For comparisons, call this twice with different dates that bracket the event."""
    if not current_date_str:
        current_date_str = datetime.today().strftime("%Y-%m-%d")
    
    status_msg = f"🔍 STEP 1: Searching for satellite images 📅 Date: {current_date_str}\n☁️ Max cloud: {max_cloud}%"
    logger.info(status_msg)
    _log_mem("get_rasters:start")

    if geometry_s3_url:
        # Download geometry from S3 (GeoJSON format)
        aoi_gdf = download_geometry_from_s3(geometry_s3_url)
        print(aoi_gdf)
    else:
        print("fallback geocode")
        geocode_result = await find_location_boundary(location)
        geocode_result = json.loads(geocode_result) #cast as a json
        aoi_gdf = download_geometry_from_s3(geocode_result["geometry_s3_url"])
        
    images = get_filtered_images(aoi_gdf, bands=["red", "green", "blue", "nir", "nir08", "swir2"], max_cloud=max_cloud, current_date_str=current_date_str, location=location)
    print(images)

    logger.info(f"✅ STEP 1 RESULT: Found {len(images)} images")

    if not images:
        logger.info("⚠️ STEP 1 RETRY: No images found, trying with higher cloud coverage (80%)")
        images = get_filtered_images(aoi_gdf, bands=["red", "green", "blue", "nir", "nir08", "swir2"], max_cloud=80, current_date_str=current_date_str, location=location)
        logger.info(f"✅ STEP 1 RETRY RESULT: Found {len(images)} images")
        
    if not images:
        error_msg = f"❌ STEP 1 FAILED: No satellite images found for {location} on {current_date_str}"
        logger.error(error_msg)
        return error_msg
    
    result = images[0]
    success_msg = f"✅ STEP 1 SUCCESS: Image found with {result.get('cloud_pct', 'N/A')}% cloud coverage\n🛰️ Date: {result.get('date', 'Unknown')}\n📊 Bands available: red, nir, tci"
    logger.info(success_msg)
    
    # Return S3 URLs for all saved rasters
    out = json.dumps({
        "location" : str(location),
        "date_used": result['date'][:10],
        "tci_s3_url": result.get('tci_s3_url', ''),
        "red_s3_url": result.get('red_s3_url', ''),
        "green_s3_url": result.get('green_s3_url', ''),
        "blue_s3_url": result.get('blue_s3_url', ''),
        "nir_s3_url": result.get('nir_s3_url', ''),
        "nir08_s3_url": result.get('nir08_s3_url', ''),
        "swir2_s3_url": result.get('swir2_s3_url', ''),
        "cloud_pct": result.get('cloud_pct'),
        "tile_id": result.get('tile_id', ''),
        "coverage_pct": result.get('coverage_pct', '')
    })
    
    _log_mem("get_rasters:end")
    return out


#####################################
### BANDMATH TOOLS
#####################################

#TODO: more general run_bandmath approach (similar to calculator, calculate any spectral index), 
#also enable differencing of images
@tool
async def run_bandmath(
    location: str,
    index_type: str,
    band1_url: str,
    band2_url: str,
    date_str: str = None,
    geometry_s3_url: str = None
) -> str:
    """Calculate spectral indices (NDVI, NDWI, NBR). ALWAYS pass date_str and geometry_s3_url!
    
    Args:
        location: Place name
        index_type: Type of index - "NDVI", "NDWI", or "NBR"
        band1_url: First band URL (red for NDVI, green for NDWI, nir08 for NBR)
        band2_url: Second band URL (nir for NDVI/NDWI, swir2 for NBR)
        date_str: Date from get_rasters (CRITICAL for unique filenames)
        geometry_s3_url: Geometry for clipping
    
    Returns: JSON with statistics, area per class, and S3 URL for the calculated index
    
    Index Types:
    - NDVI (Vegetation): band1=red, band2=nir
      Classes: (-1,0]=no vegetation (water, rock, structures), (0,0.5]=light vegetation (shrubs, grass, fields), 
               (0.5,0.7]=dense vegetation (plantations), (0.7,1]=very dense vegetation (rainforest)
      Use for: Vegetation health, deforestation, crop monitoring, land cover analysis
      
    - NDWI (Water): band1=green, band2=nir
      Classes: >0.3=water, 0.1-0.3=vegetation/moisture, 0-0.1=built-up, <0=other
      Use for: Flood monitoring, drought analysis, reservoir levels, water body mapping
      
    - NBR (Burn): band1=nir08 (20m), band2=swir2 (20m)
      Classes: >0.1=unburned, -0.1 to 0.1=moderate burn, <-0.1=high severity burn
      Use for: Wildfire damage assessment, burn severity mapping, post-fire recovery"""

    
    index_type = index_type.upper()
    
    if index_type not in ["NDVI", "NDWI", "NBR"]:
        return json.dumps({"error": f"Invalid index_type: {index_type}. Must be NDVI, NDWI, or NBR"})
    
    try:
        # NDVI calculation
        if index_type == "NDVI":
            status_msg = f"🌿 VEGETATION ANALYSIS: Calculating NDVI\n📍 Location: {location}\n📅 Date: {date_str or 'today'}\n🔴 Red band: {band1_url[:50]}...\n🟢 NIR band: {band2_url[:50]}..."
            logger.info(status_msg)
            
            stats = await calculate_ndvi_stats(band1_url, band2_url, date_str, geometry_s3_url, location)
            
            success_msg = f"✅ NDVI SUCCESS: Very Dense={stats['very_dense_vegetation_percentage']:.1f}%, Dense={stats['dense_vegetation_percentage']:.1f}%, Light={stats['light_vegetation_percentage']:.1f}%, None={stats['no_vegetation_percentage']:.1f}%"
            logger.info(success_msg)
            
            return json.dumps({
                "index_type": "NDVI",
                "min": stats['min'],
                "max": stats['max'],
                "mean": stats['mean'],
                "median": stats['median'],
                "count": stats['count'],
                "no_vegetation_percentage": stats['no_vegetation_percentage'],
                "no_vegetation_area_m2": stats['no_vegetation_area_m2'],
                "light_vegetation_percentage": stats['light_vegetation_percentage'],
                "light_vegetation_area_m2": stats['light_vegetation_area_m2'],
                "dense_vegetation_percentage": stats['dense_vegetation_percentage'],
                "dense_vegetation_area_m2": stats['dense_vegetation_area_m2'],
                "very_dense_vegetation_percentage": stats['very_dense_vegetation_percentage'],
                "very_dense_vegetation_area_m2": stats['very_dense_vegetation_area_m2'],
                "location": location,
                "result_s3_url": stats.get('ndvi_s3_url', '')
            })
        
        # NDWI calculation
        elif index_type == "NDWI":
            status_msg = f"🌊 WATER ANALYSIS: Calculating NDWI\n📍 Location: {location}\n📅 Date: {date_str or 'today'}\n🟢 Green band: {band1_url[:50]}...\n🟤 NIR band: {band2_url[:50]}..."
            logger.info(status_msg)
            
            stats = await calculate_ndwi_stats(band1_url, band2_url, date_str, geometry_s3_url, location)
            
            success_msg = f"✅ NDWI SUCCESS: Water={stats['water_percentage']:.1f}%, Non-water={stats['non_water_percentage']:.1f}%"
            logger.info(success_msg)
            
            return json.dumps({
                "index_type": "NDWI",
                "mean": stats['mean'],
                "water_percentage": stats['water_percentage'],
                "water_area_m2": stats['water_area_m2'],
                "non_water_percentage": stats['non_water_percentage'],
                "non_water_area_m2": stats['non_water_area_m2'],
                "location": location,
                "result_s3_url": stats.get('ndwi_s3_url', '')
            })
        
        # NBR calculation
        elif index_type == "NBR":
            status_msg = f"🔥 FIRE ANALYSIS: Calculating NBR\n📍 Location: {location}\n📅 Date: {date_str or 'today'}\n🟤 NIR08 band (20m): {band1_url[:50]}...\n🟠 SWIR2 band (20m): {band2_url[:50]}..."
            logger.info(status_msg)
            
            stats = await calculate_nbr_stats(band1_url, band2_url, date_str, geometry_s3_url, location)
            
            success_msg = f"✅ NBR SUCCESS: High severity={stats['high_severity_percentage']:.1f}%, Moderate={stats['moderate_severity_percentage']:.1f}%, Unburned={stats['unburned_percentage']:.1f}%"
            logger.info(success_msg)
            
            return json.dumps({
                "index_type": "NBR",
                "mean": stats['mean'],
                "high_severity_percentage": stats['high_severity_percentage'],
                "high_severity_area_m2": stats['high_severity_area_m2'],
                "moderate_severity_percentage": stats['moderate_severity_percentage'],
                "moderate_severity_area_m2": stats['moderate_severity_area_m2'],
                "unburned_percentage": stats['unburned_percentage'],
                "unburned_area_m2": stats['unburned_area_m2'],
                "location": location,
                "result_s3_url": stats.get('nbr_s3_url', '')
            })
            
    except Exception as e:
        error_msg = f"❌ {index_type} ANALYSIS ERROR: {str(e)}"
        logger.error(error_msg)
        return json.dumps({"error": error_msg})


#####################################
### IMPACT CALCULATION TOOLS
#####################################

@tool
async def calculate_environmental_impact(affected_area_m2: float, index_type: str) -> str:
    """Calculate environmental impact metrics based on affected area and index type.
    
    Args:
        affected_area_m2: Affected area in square meters
        index_type: Type of index used - "NDVI", "NBR", or "NDWI"
    
    Returns: JSON with impact metrics:
        - vegetation_co2: CO2 sequestration impact (kg) for NDVI
        - burn_co2: CO2 emissions from burning (kg) for NBR
        - water_quantity: Water volume (m³) for NDWI
        - affected_area_km2: Area in km² for reference
    
    Impact Calculations:
    - NDVI (Vegetation): vegetation_co2 = area × 0.0025 kg/m² (annual CO2 sequestration)
      Use for: Estimating carbon sequestration loss from deforestation
    - NBR (Burn): burn_co2 = area × 0.015 kg/m² (CO2 released from burning)
      Use for: Estimating carbon emissions from wildfires
    - NDWI (Water): water_quantity = area × 0.001 m³/m² (1mm water depth)
      Use for: Estimating water volume in floods or reservoirs
    
    Example:
        For 1 km² (1,000,000 m²) of burned forest:
        burn_co2 = 1,000,000 × 0.015 = 15,000 kg = 15 metric tons CO2"""
    
    try:
        # Validate inputs
        affected_area_m2 = float(affected_area_m2)
        index_type = index_type.upper()
        
        if index_type not in config.IMPACT_METRICS:
            return json.dumps({
                "error": f"Invalid index_type: {index_type}. Must be NDVI, NBR, or NDWI"
            })
        
        if affected_area_m2 <= 0:
            return json.dumps({
                "error": f"Invalid affected_area_m2: {affected_area_m2}. Must be positive"
            })
        
        # Get impact metrics for the index type
        metrics = config.IMPACT_METRICS[index_type]
        
        # Convert area to km² for readability
        affected_area_km2 = affected_area_m2 / 1_000_000
        
        logger.info(f"🌍 IMPACT CALCULATION: {index_type}")
        logger.info(f"   Area: {affected_area_km2:.2f} km² ({affected_area_m2:,.0f} m²)")
        
        # Base result with common fields
        result = {
            "index_type": index_type,
            "affected_area_m2": round(affected_area_m2, 2),
            "affected_area_km2": round(affected_area_km2, 4)
        }
        
        # Add only relevant metrics based on index type
        if index_type == "NDVI" or index_type == "CHANGE_DETECTION":
            vegetation_co2_kg = affected_area_m2 * metrics["vegetation_co2_per_m2"]
            vegetation_co2_tons = vegetation_co2_kg / 1000
            
            result["vegetation_co2_kg"] = round(vegetation_co2_kg, 2)
            result["vegetation_co2_tons"] = round(vegetation_co2_tons, 2)
            if index_type == "CHANGE_DETECTION":
                result["interpretation"] = f"The detected land change area of {affected_area_km2:.2f} km² represents approximately {vegetation_co2_tons:.2f} metric tons of potential CO2 sequestration capacity affected per year"
            else:
                result["interpretation"] = f"This vegetation area sequesters approximately {vegetation_co2_tons:.2f} metric tons of CO2 per year"
            
            logger.info(f"   🌱 Vegetation CO2 sequestration: {vegetation_co2_tons:.2f} metric tons/year")
            
        elif index_type == "NBR":
            burn_co2_kg = affected_area_m2 * metrics["burn_co2_per_m2"]
            burn_co2_tons = burn_co2_kg / 1000
            
            result["burn_co2_kg"] = round(burn_co2_kg, 2)
            result["burn_co2_tons"] = round(burn_co2_tons, 2)
            result["interpretation"] = f"The burned area released approximately {burn_co2_tons:.2f} metric tons of CO2 into the atmosphere"
            
            logger.info(f"   🔥 Burn CO2 emissions: {burn_co2_tons:.2f} metric tons")
            
        elif index_type == "NDWI":
            water_quantity_m3 = affected_area_m2 * metrics["water_quantity_per_m2"]
            
            result["water_quantity_m3"] = round(water_quantity_m3, 2)
            result["water_quantity_liters"] = round(water_quantity_m3 * 1000, 2)
            result["interpretation"] = f"The water body contains approximately {water_quantity_m3:,.0f} cubic meters ({water_quantity_m3 * 1000:,.0f} liters) of water"
            
            logger.info(f"   💧 Water volume: {water_quantity_m3:,.0f} m³ ({water_quantity_m3 * 1000:,.0f} liters)")
        
        logger.info(f"✅ IMPACT CALCULATION SUCCESS")
        
        return json.dumps(result, indent=2)
        
    except ValueError as e:
        error_msg = f"Invalid input values: {str(e)}"
        logger.error(f"❌ IMPACT CALCULATION ERROR: {error_msg}")
        return json.dumps({"error": error_msg})
        
    except Exception as e:
        error_msg = f"Unexpected error in impact calculation: {str(e)}"
        logger.error(f"❌ IMPACT CALCULATION ERROR: {error_msg}")
        return json.dumps({"error": error_msg})


#####################################
### CHANGE DETECTION TOOLS
#####################################

@tool
async def scan_region_change(
    region: str,
    year1: int,
    month1: int,
    year2: int,
    month2: int,
    top_n: int = 20,
    geometry_s3_url: str = None,
) -> str:
    """Scan an entire country or large region for land surface change hotspots using Clay AI embeddings.

    This is a FAST broad-area scan (seconds to minutes) that identifies WHERE change happened
    at 1.28km resolution across an entire country. Use the returned hotspot bboxes to drill
    into specific areas with run_change_detection for pixel-level detail.

    IMPORTANT: Very large countries (USA, Brazil, China, Canada, India) may take too long.
    For these, suggest scanning a specific state/region instead. Medium countries (Colombia,
    Peru, Costa Rica, etc.) work well.

    Args:
        region: Whole country or US state name (e.g. "Colombia", "Colorado"). For a
            named SUB-REGION, also pass geometry_s3_url (see below) - do NOT rely on the name.
        year1: Earlier year (e.g. 2020)
        month1: Earlier month (1-12)
        year2: Later year (e.g. 2025)
        month2: Later month (1-12)
        top_n: Number of top hotspots to return (default 20)
        geometry_s3_url: OPTIONAL. For a NAMED SUB-REGION (valley, county, metro, basin,
            mountain range, national forest - anything smaller than a whole supported
            country/state), geocode the area first (find_location_boundary +
            get_best_geometry) and pass the resulting geometry here; the scan then covers
            only that area's bounding box. Leave unset only for a whole supported
            country/US-state named in `region`.

    Returns: JSON with:
        - summary: total cells scanned, area covered, % changed, timing
        - hotspots: Top N areas ranked by change severity, each with center_lat/lon and bbox for drill-in
        - hotspot_geometry_s3_url: GeoJSON of hotspot locations for map display

    Workflow:
    1. User asks: "Where has deforestation occurred in Colombia between 2020 and 2025?"
    2. Call scan_region_change("Colombia", 2020, 6, 2025, 6)
    3. display_visual(hotspot_geometry_s3_url) to show hotspots on map
    4. Present hotspot results with locations and severity
    5. User selects a hotspot → use its bbox with get_rasters + run_change_detection for detailed analysis

    For very large countries (USA, Brazil, China, India, Canada, Australia):
    - Suggest scanning a sub-region: "Colorado", "Rondônia State", "New South Wales"
    - Or use a custom bbox for a specific area of interest

    Coverage: Global Sentinel-2 archive, Jan 2017 - April 2026, monthly resolution.
    Resolution: 1.28km grid cells (each cell is one Clay v1.5 embedding).
    Method: Cosine similarity between embedding vectors — semantically aware, ignores seasonal noise.
    """
    from .lgnd_embeddings import scan_region_change as _scan_region, COUNTRY_BBOXES

    try:
        region_lower = region.lower().strip()
        # Resolve the scan bbox. Priority:
        #   1. An explicit geocoded geometry (sub-regions: valleys, counties, metros,
        #      basins, parks - anything that is not a whole supported country/state).
        #   2. An EXACT match in the known whole-country/state table.
        # We intentionally do NOT substring-match the name against the table, so that
        # "San Luis Valley, Colorado" never collapses to the whole Colorado bbox.
        if geometry_s3_url:
            try:
                geom_gdf = download_geometry_from_s3(geometry_s3_url).to_crs("EPSG:4326")
                minx, miny, maxx, maxy = (float(v) for v in geom_gdf.total_bounds)
                bbox = (minx, miny, maxx, maxy)
                logger.info("REGION SCAN (geocoded sub-region): %s bbox=%s, %d-%02d -> %d-%02d",
                            region, bbox, year1, month1, year2, month2)
            except Exception as e:
                return json.dumps({"error": f"Could not read geometry for '{region}': {e}"})
        elif region_lower in COUNTRY_BBOXES:
            bbox = COUNTRY_BBOXES[region_lower]
            logger.info("REGION SCAN: %s (%s), %d-%02d -> %d-%02d",
                        region, bbox, year1, month1, year2, month2)
        else:
            return json.dumps({
                "error": (
                    f"'{region}' is not a recognized whole country or US state. If it is a "
                    f"sub-region (valley, county, metro, basin, park, mountain range, etc.), "
                    f"geocode it first with find_location_boundary + get_best_geometry, then "
                    f"call scan_region_change again with geometry_s3_url set to that geometry. "
                    f"Do NOT substitute the parent state/country."
                ),
                "supported_whole_regions_sample": sorted(COUNTRY_BBOXES.keys())[:20],
            })

        # Check if region is very large (>20 geohashes would be excessive)
        from .lgnd_embeddings import _get_geohashes_for_bbox
        scan_geohashes = _get_geohashes_for_bbox(bbox[0], bbox[1], bbox[2], bbox[3])
        if len(scan_geohashes) > 20:
            return json.dumps({
                "error": f"Region '{region}' spans {len(scan_geohashes)} geohash partitions (max 20). "
                         f"Please scan a smaller sub-region."
            })

        result = _scan_region(
            bbox=bbox,
            year1=year1,
            month1=month1,
            year2=year2,
            month2=month2,
            top_n=top_n,
            min_change_score=0.15,
        )

        if "error" in result:
            return json.dumps(result)

        # Build GeoJSON for map display: ALL changed cells as a density layer,
        # colored by change_score, with the top-N hotspots tagged (tier="top",
        # plus rank) so the frontend can highlight them distinctly.
        all_cells = result.get("_all_cells", [])
        change_geojson = {
            "type": "FeatureCollection",
            "features": []
        }
        for c in all_cells:
            b = c.get("bbox")
            if not b:
                continue
            rank = c.get("rank")
            feature = {
                "type": "Feature",
                "properties": {
                    "change_score": c.get("change_score", 0),
                    "rank": rank,
                    "tier": "top" if rank else "change",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [b["west"], b["south"]],
                        [b["east"], b["south"]],
                        [b["east"], b["north"]],
                        [b["west"], b["north"]],
                        [b["west"], b["south"]],
                    ]]
                }
            }
            change_geojson["features"].append(feature)

        # Fallback: if for some reason _all_cells is empty, fall back to the
        # top-N hotspots so the map still shows something.
        if not change_geojson["features"]:
            for h in result.get("hotspots", []):
                if h.get("bbox"):
                    b = h["bbox"]
                    change_geojson["features"].append({
                        "type": "Feature",
                        "properties": {
                            "change_score": h["change_score"],
                            "rank": h["rank"],
                            "tier": "top",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [b["west"], b["south"]],
                                [b["east"], b["south"]],
                                [b["east"], b["north"]],
                                [b["west"], b["north"]],
                                [b["west"], b["south"]],
                            ]]
                        }
                    })

        # Save change GeoJSON to S3
        s3_client = boto3.client('s3')
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME
        clean_region = region.replace(" ", "_").replace(",", "").lower()
        s3_key = f"session_data/{session_id}/geometries/scan_hotspots_{clean_region}_{year1}{month1:02d}_to_{year2}{month2:02d}.geojson"

        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=json.dumps(change_geojson).encode('utf-8'),
            ContentType='application/geo+json'
        )
        hotspot_s3_url = f"s3://{bucket_name}/{s3_key}"

        # Strip the internal full-cell list so it does not bloat the LLM context;
        # the model still gets the ranked top-N hotspots for narration.
        cells_shown = len(change_geojson["features"])
        result.pop("_all_cells", None)

        # Enhance result with region metadata
        result["region"] = region
        result["hotspot_geometry_s3_url"] = hotspot_s3_url
        result["cells_displayed"] = cells_shown
        result["method"] = "Clay v1.5 foundation model embeddings (LGND/Source Cooperative)"
        result["resolution"] = "1.28km grid cells"
        result["interpretation"] = (
            f"Scanned {result['summary']['total_cells_scanned']:,} cells "
            f"({result['summary']['area_scanned_km2']:,.0f} km²) using peak-season "
            f"{result['summary'].get('month_name', '')} imagery. "
            f"{result['summary']['cells_with_change']:,} cells ({result['summary']['change_percentage']:.1f}%) "
            f"show significant change. The map layer (hotspot_geometry_s3_url) shows ALL "
            f"{cells_shown:,} changed cells colored by change score, with the top "
            f"{len(result.get('hotspots', []))} hotspots highlighted. "
            f"Display hotspot_geometry_s3_url on the map, then drill into a specific hotspot "
            f"with run_change_detection."
        )
        # Include drill-in recommendation prominently
        if "drill_in_recommendation" in result:
            result["IMPORTANT_for_drill_in"] = result["drill_in_recommendation"]

        logger.info(f"✅ REGION SCAN COMPLETE: {result['summary']['total_cells_scanned']:,} cells, "
                    f"{result['summary']['cells_with_change']} changed, "
                    f"{result['summary']['total_time_s']:.1f}s")

        return json.dumps(result)

    except Exception as e:
        error_msg = f"❌ REGION SCAN ERROR: {type(e).__name__}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return json.dumps({"error": error_msg})


@tool
async def run_change_detection(
    location: str,
    red_s3_url_date1: str,
    nir_s3_url_date1: str,
    green_s3_url_date1: str,
    red_s3_url_date2: str,
    nir_s3_url_date2: str,
    green_s3_url_date2: str,
    date1_str: str,
    date2_str: str,
    geometry_s3_url: str = None,
    nir08_s3_url_date1: str = None,
    swir2_s3_url_date1: str = None,
    nir08_s3_url_date2: str = None,
    swir2_s3_url_date2: str = None,
    blue_s3_url_date1: str = None,
    blue_s3_url_date2: str = None,
) -> str:
    """Detect land surface changes between two dates using multi-index spectral analysis.
    Produces two change maps: a spectral index composite and iMAD. (Embedding-based
    change is handled separately by the scan_region_change tool.)

    REQUIRES calling get_rasters TWICE first (once per date) to obtain band URLs.

    Args:
        location: Place name (for filenames)
        red_s3_url_date1: Red band S3 URL for earlier date
        nir_s3_url_date1: NIR band S3 URL for earlier date
        green_s3_url_date1: Green band S3 URL for earlier date
        red_s3_url_date2: Red band S3 URL for later date
        nir_s3_url_date2: NIR band S3 URL for later date
        green_s3_url_date2: Green band S3 URL for later date
        date1_str: Earlier date (YYYY-MM-DD) from get_rasters date_used
        date2_str: Later date (YYYY-MM-DD) from get_rasters date_used
        geometry_s3_url: Geometry for clipping (from find_location_boundary or create_bbox_from_coordinates)
        nir08_s3_url_date1: Optional NIR08 (20m) for date 1 — enables NBR in composite
        swir2_s3_url_date1: Optional SWIR2 (20m) for date 1 — enables NBR in composite
        nir08_s3_url_date2: Optional NIR08 (20m) for date 2 — enables NBR in composite
        swir2_s3_url_date2: Optional SWIR2 (20m) for date 2 — enables NBR in composite
        blue_s3_url_date1: Optional Blue band (10m) for date 1 — enables BSI (Bare Soil Index) for construction detection
        blue_s3_url_date2: Optional Blue band (10m) for date 2 — enables BSI

    Returns: JSON with change statistics (per-class areas and percentages), change_map_s3_url for visualization

    The output raster uses values 0-1 (composite change score). Display with display_visual — the frontend
    renders it with a reversed RdYlGn colormap (green=no change, yellow=moderate, red=high change).

    Workflow:
    1. get_rasters(date1) + get_rasters(date2) in PARALLEL
    2. run_change_detection(all band URLs from both dates)
    3. display_visual(geometry) then display_visual(change_map_s3_url)
    """
    import shutil
    from .raster_utils import clip_raster_v2
    from .aws_utils import download_geometry_from_s3
    from .change_detection_utils import (
        compute_index_delta,
        compute_bsi_delta,
        compute_composite_change_score,
        compute_change_statistics,
        imad_change_score,
    )

    temp_dir = tempfile.mkdtemp(prefix='change_detect_')

    try:
        s3_client = boto3.client('s3')
        session_id = os.environ.get('AGENT_SESSION_ID', config.DEFAULT_SESSION_ID)
        bucket_name = config.S3_BUCKET_NAME

        logger.info(f"🔄 CHANGE DETECTION: {location}")
        logger.info(f"   Date 1: {date1_str}, Date 2: {date2_str}")
        _log_mem("change_detection:start")

        # Guard rail: run_change_detection is pixel-level and only processes a
        # single Sentinel-2 tile (~110 km across). If the AOI is larger than one
        # tile can cover, the result would silently represent just a sliver of the
        # requested area while appearing to cover all of it. Reject oversized AOIs
        # up front (before any downloads) and route the caller to scan_region_change.
        if geometry_s3_url:
            try:
                guard_gdf = download_geometry_from_s3(geometry_s3_url)
                area_km2 = float(guard_gdf.to_crs("EPSG:6933").geometry.area.sum()) / 1_000_000
                if area_km2 > config.MAX_CHANGE_DETECTION_AREA_KM2:
                    logger.warning("⚠️ CHANGE DETECTION: AOI too large (%.0f km² > %d km²) — routing to scan_region_change",
                                   area_km2, config.MAX_CHANGE_DETECTION_AREA_KM2)
                    return json.dumps({
                        "error": (
                            f"The area for '{location}' is ~{area_km2:,.0f} km², which is too large for "
                            f"pixel-level change detection (limit {config.MAX_CHANGE_DETECTION_AREA_KM2:,} km²). "
                            f"run_change_detection processes a single Sentinel-2 tile (~110 km across), so it "
                            f"would only cover a small part of this area and misrepresent the result."
                        ),
                        "area_km2": round(area_km2, 1),
                        "max_area_km2": config.MAX_CHANGE_DETECTION_AREA_KM2,
                        "recommended_tool": "scan_region_change",
                        "recommendation": (
                            "Use scan_region_change for a broad hotspot scan of the whole region, then drill "
                            "into a specific hotspot (under ~100 km²) with run_change_detection for detail."
                        ),
                    })
            except Exception as guard_err:
                # If the area check itself fails, don't block the analysis — log and continue.
                logger.warning("Change detection area guard skipped (%s): %s",
                               type(guard_err).__name__, guard_err)

        # --- Helper to download a band from S3 ---
        def download_band(s3_url, label):
            b, k = s3_url.replace('s3://', '').split('/', 1)
            local = f"{temp_dir}/{label}.tif"
            s3_client.download_file(b, k, local)
            return local

        # --- Download 10m bands for both dates ---
        red1_path = download_band(red_s3_url_date1, "red_d1")
        nir1_path = download_band(nir_s3_url_date1, "nir_d1")
        green1_path = download_band(green_s3_url_date1, "green_d1")
        red2_path = download_band(red_s3_url_date2, "red_d2")
        nir2_path = download_band(nir_s3_url_date2, "nir_d2")
        green2_path = download_band(green_s3_url_date2, "green_d2")

        # --- Read arrays ---
        with rasterio.open(red1_path) as src:
            red1 = src.read(1)
            profile = src.profile.copy()
            transform = src.transform

        with rasterio.open(nir1_path) as src:
            nir1 = src.read(1)
        with rasterio.open(green1_path) as src:
            green1 = src.read(1)
        with rasterio.open(red2_path) as src:
            red2 = src.read(1)
        with rasterio.open(nir2_path) as src:
            nir2 = src.read(1)
        with rasterio.open(green2_path) as src:
            green2 = src.read(1)

        # --- Download optional 20m bands ---
        nir08_1 = nir08_2 = swir2_1 = swir2_2 = None
        if all([nir08_s3_url_date1, swir2_s3_url_date1, nir08_s3_url_date2, swir2_s3_url_date2]):
            nir08_1_path = download_band(nir08_s3_url_date1, "nir08_d1")
            swir2_1_path = download_band(swir2_s3_url_date1, "swir2_d1")
            nir08_2_path = download_band(nir08_s3_url_date2, "nir08_d2")
            swir2_2_path = download_band(swir2_s3_url_date2, "swir2_d2")

            with rasterio.open(nir08_1_path) as src:
                nir08_1 = src.read(1)
            with rasterio.open(swir2_1_path) as src:
                swir2_1 = src.read(1)
            with rasterio.open(nir08_2_path) as src:
                nir08_2 = src.read(1)
            with rasterio.open(swir2_2_path) as src:
                swir2_2 = src.read(1)

        # --- Download optional blue band (10m) ---
        blue1 = blue2 = None
        if blue_s3_url_date1 and blue_s3_url_date2:
            blue1_path = download_band(blue_s3_url_date1, "blue_d1")
            blue2_path = download_band(blue_s3_url_date2, "blue_d2")
            with rasterio.open(blue1_path) as src:
                blue1 = src.read(1)
            with rasterio.open(blue2_path) as src:
                blue2 = src.read(1)

        # ===================================================================
        # Run Tier 1 (spectral index) and Tier 2 (iMAD) in parallel
        # ===================================================================
        from concurrent.futures import ThreadPoolExecutor, as_completed

        clean_location = location.replace(" ", "_").replace(",", "").lower()

        # Prepare geometry clipping resources once (shared by both tiers)
        aoi_gdf = None
        aoi_path = None
        if geometry_s3_url:
            aoi_gdf = download_geometry_from_s3(geometry_s3_url)
            with rasterio.open(red1_path) as src:
                aoi_gdf = aoi_gdf.to_crs(src.crs)
            aoi_path = f"{temp_dir}/aoi.geojson"
            aoi_gdf.to_file(aoi_path, driver='GeoJSON')

        def run_tier1():
            """Tier 1: Spectral index differencing (NDVI + NDWI + NBR)."""
            deltas = {}
            deltas["NDVI"] = compute_index_delta(red1, nir1, red2, nir2, "NDVI")
            logger.info(f"   ✅ NDVI delta computed (mean={np.mean(deltas['NDVI']):.4f})")

            deltas["NDWI"] = compute_index_delta(green1, nir1, green2, nir2, "NDWI")
            logger.info(f"   ✅ NDWI delta computed (mean={np.mean(deltas['NDWI']):.4f})")

            if nir08_1 is not None:
                nbr_delta_20m = compute_index_delta(nir08_1, swir2_1, nir08_2, swir2_2, "NBR")
                from scipy.ndimage import zoom
                scale_y = red1.shape[0] / nbr_delta_20m.shape[0]
                scale_x = red1.shape[1] / nbr_delta_20m.shape[1]
                nbr_delta_10m = zoom(nbr_delta_20m, (scale_y, scale_x), order=0)
                nbr_delta_10m = nbr_delta_10m[:red1.shape[0], :red1.shape[1]]
                deltas["NBR"] = nbr_delta_10m.astype(np.float32)
                logger.info(f"   ✅ NBR delta computed (mean={np.mean(deltas['NBR']):.4f})")
            else:
                logger.info("   ℹ️ NBR skipped (20m bands not provided)")

            # BSI delta (optional — needs blue + swir2 + red + nir)
            if blue1 is not None and swir2_1 is not None:
                from scipy.ndimage import zoom as _zoom_bsi
                # Resample SWIR2 from 20m to 10m
                sy = red1.shape[0] / swir2_1.shape[0]
                sx = red1.shape[1] / swir2_1.shape[1]
                swir2_1_10m = _zoom_bsi(swir2_1, (sy, sx), order=0)[:red1.shape[0], :red1.shape[1]]
                swir2_2_10m = _zoom_bsi(swir2_2, (sy, sx), order=0)[:red1.shape[0], :red1.shape[1]]
                deltas["BSI"] = compute_bsi_delta(red1, swir2_1_10m, nir1, blue1,
                                                   red2, swir2_2_10m, nir2, blue2)
                logger.info(f"   ✅ BSI delta computed (mean={np.mean(deltas['BSI']):.4f})")
            else:
                logger.info("   ℹ️ BSI skipped (blue or swir2 bands not provided)")

            composite = compute_composite_change_score(deltas)
            logger.info(f"   ✅ Tier 1 composite: mean={np.mean(composite):.4f}, max={np.max(composite):.4f}")

            # Write temp raster
            t1_temp = f"{temp_dir}/tier1_temp.tif"
            t1_profile = profile.copy()
            t1_profile.update({'driver': 'GTiff', 'dtype': rasterio.float32, 'count': 1})
            with rasterio.open(t1_temp, 'w', **t1_profile) as dst:
                dst.write_band(1, composite)

            # Clip
            t1_cog = f"{temp_dir}/tier1_cog.tif"
            if aoi_path:
                clip_raster_v2(aoi_path, t1_temp, t1_cog)
            else:
                shutil.copy(t1_temp, t1_cog)

            # Upload
            s3_key = f"session_data/{session_id}/rasters/change_detection_{clean_location}_{date1_str}_to_{date2_str}.tif"
            s3_client.upload_file(t1_cog, bucket_name, s3_key)
            url = f"s3://{bucket_name}/{s3_key}"
            logger.info(f"   ✅ Tier 1 change map uploaded: {url}")

            # Stats
            with rasterio.open(t1_cog) as src:
                data = src.read(1)
                t = src.transform
                pix_area = abs(t.a) * abs(t.e)
            stats = compute_change_statistics(data, pix_area)

            return {"url": url, "stats": stats, "indices_used": list(deltas.keys())}

        def run_tier2_imad():
            """Tier 2: iMAD on normalized spectral indices (NDVI, NDWI, optionally NBR).

            Using indices instead of raw bands suppresses atmospheric/illumination
            noise that causes false positives, while iMAD adds multivariate
            statistical rigor on top.
            """
            try:
                eps = 1e-10

                # Compute NDVI for both dates: (NIR - Red) / (NIR + Red)
                ndvi_d1 = (nir1.astype(np.float64) - red1.astype(np.float64)) / (nir1 + red1 + eps)
                ndvi_d2 = (nir2.astype(np.float64) - red2.astype(np.float64)) / (nir2 + red2 + eps)

                # Compute NDWI for both dates: (Green - NIR) / (Green + NIR)
                ndwi_d1 = (green1.astype(np.float64) - nir1.astype(np.float64)) / (green1 + nir1 + eps)
                ndwi_d2 = (green2.astype(np.float64) - nir2.astype(np.float64)) / (green2 + nir2 + eps)

                bands_d1 = [ndvi_d1, ndwi_d1]
                bands_d2 = [ndvi_d2, ndwi_d2]

                # Add NBR if 20m bands are available
                if nir08_1 is not None:
                    from scipy.ndimage import zoom as _zoom
                    nbr_d1_20m = (nir08_1.astype(np.float64) - swir2_1.astype(np.float64)) / (nir08_1 + swir2_1 + eps)
                    nbr_d2_20m = (nir08_2.astype(np.float64) - swir2_2.astype(np.float64)) / (nir08_2 + swir2_2 + eps)
                    # Resample 20m NBR to 10m
                    sy = red1.shape[0] / nbr_d1_20m.shape[0]
                    sx = red1.shape[1] / nbr_d1_20m.shape[1]
                    nbr_d1 = _zoom(nbr_d1_20m, (sy, sx), order=0)[:red1.shape[0], :red1.shape[1]]
                    nbr_d2 = _zoom(nbr_d2_20m, (sy, sx), order=0)[:red1.shape[0], :red1.shape[1]]
                    bands_d1.append(nbr_d1)
                    bands_d2.append(nbr_d2)

                image1 = np.stack(bands_d1, axis=0)
                image2 = np.stack(bands_d2, axis=0)

                n_idx = image1.shape[0]
                logger.info(f"   🔬 iMAD: Running on {n_idx} indices, {image1.shape[1]}x{image1.shape[2]} pixels")

                imad_score = imad_change_score(image1, image2, max_iter=30, tol=1e-3)
                logger.info(f"   ✅ iMAD score: mean={np.mean(imad_score):.4f}, max={np.max(imad_score):.4f}")

                # Write temp raster
                t2_temp = f"{temp_dir}/imad_temp.tif"
                t2_profile = profile.copy()
                t2_profile.update({'driver': 'GTiff', 'dtype': rasterio.float32, 'count': 1})
                with rasterio.open(t2_temp, 'w', **t2_profile) as dst:
                    dst.write_band(1, imad_score)

                # Clip
                t2_cog = f"{temp_dir}/imad_cog.tif"
                if aoi_path:
                    clip_raster_v2(aoi_path, t2_temp, t2_cog)
                else:
                    shutil.copy(t2_temp, t2_cog)

                # Upload
                s3_key = f"session_data/{session_id}/rasters/change_detection_imad_{clean_location}_{date1_str}_to_{date2_str}.tif"
                s3_client.upload_file(t2_cog, bucket_name, s3_key)
                url = f"s3://{bucket_name}/{s3_key}"
                logger.info(f"   ✅ iMAD change map uploaded: {url}")

                # Stats
                with rasterio.open(t2_cog) as src:
                    data = src.read(1)
                    t = src.transform
                    pix_area = abs(t.a) * abs(t.e)
                stats = compute_change_statistics(data, pix_area)

                return {"url": url, "stats": stats}

            except Exception as e:
                logger.error(f"   ⚠️ iMAD failed (Tier 1 still available): {e}", exc_info=True)
                return None

        # Run Tier 1 (spectral composite) and Tier 2 (iMAD) in parallel.
        # NOTE: embedding-based change detection is intentionally handled by the
        # dedicated country/state-wide scan_region_change tool, not here. Keeping
        # this tool to two tiers also bounds its peak memory footprint.
        with ThreadPoolExecutor(max_workers=2) as executor:
            tier1_future = executor.submit(run_tier1)
            tier2_future = executor.submit(run_tier2_imad)

            tier1_result = tier1_future.result()
            tier2_result = tier2_future.result()
        _log_mem("change_detection:after_tiers")

        # --- Build combined result ---
        stats = tier1_result["stats"]
        result = {
            "index_type": "CHANGE_DETECTION",
            "location": location,
            "date1": date1_str,
            "date2": date2_str,
            "indices_used": tier1_result["indices_used"],
            "change_map_s3_url": tier1_result["url"],
            "no_change_percentage": stats["no_change_pct"],
            "no_change_area_m2": stats["no_change_area_m2"],
            "low_change_percentage": stats["low_change_pct"],
            "low_change_area_m2": stats["low_change_area_m2"],
            "moderate_change_percentage": stats["moderate_change_pct"],
            "moderate_change_area_m2": stats["moderate_change_area_m2"],
            "high_change_percentage": stats["high_change_pct"],
            "high_change_area_m2": stats["high_change_area_m2"],
            "mean_change_score": stats["mean_change_score"],
            "max_change_score": stats["max_change_score"],
            "total_changed_area_m2": stats["moderate_change_area_m2"] + stats["high_change_area_m2"],
        }

        # Add iMAD results if available
        if tier2_result:
            imad_stats = tier2_result["stats"]
            result["imad_change_map_s3_url"] = tier2_result["url"]
            result["imad_no_change_percentage"] = imad_stats["no_change_pct"]
            result["imad_high_change_percentage"] = imad_stats["high_change_pct"]
            result["imad_mean_change_score"] = imad_stats["mean_change_score"]
            result["imad_total_changed_area_m2"] = imad_stats["moderate_change_area_m2"] + imad_stats["high_change_area_m2"]

        logger.info(f"✅ CHANGE DETECTION SUCCESS: Tier1 No change={stats['no_change_pct']:.1f}%, "
                     f"Low={stats['low_change_pct']:.1f}%, Moderate={stats['moderate_change_pct']:.1f}%, "
                     f"High={stats['high_change_pct']:.1f}%")
        if tier2_result:
            logger.info(f"   iMAD: No change={imad_stats['no_change_pct']:.1f}%, "
                         f"High={imad_stats['high_change_pct']:.1f}%")

        return json.dumps(result)

    except Exception as e:
        error_msg = f"❌ CHANGE DETECTION ERROR: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return json.dumps({"error": error_msg})

    finally:
        try:
            import shutil as _shutil
            if os.path.exists(temp_dir):
                _shutil.rmtree(temp_dir)
        except Exception:
            pass
