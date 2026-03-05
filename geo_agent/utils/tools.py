"""
Strands tool definitions for geospatial analysis
"""
import json
import logging
import os
from datetime import datetime
from io import BytesIO
import boto3
import math

from strands import tool
from strands_tools import calculator as calculator_tool
from .geocode_utils import get_polygon_of_aoi, geojson_str_to_gdf, haversine_distance
from .sentinel_utils import get_filtered_images
from .ndvi_utils import calculate_ndvi_stats, calculate_ndwi_stats, calculate_nbr_stats
from .aws_utils import download_from_s3, download_geometry_from_s3, list_files_in_s3

import config

logger = logging.getLogger(__name__)

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
    #try:
    if not current_date_str:
        current_date_str = datetime.today().strftime("%Y-%m-%d")
    
    status_msg = f"🔍 STEP 1: Searching for satellite images 📅 Date: {current_date_str}\n☁️ Max cloud: {max_cloud}%"
    logger.info(status_msg)

    if geometry_s3_url:
        # Download geometry from S3 (GeoJSON format)
        aoi_gdf = download_geometry_from_s3(geometry_s3_url)
        print(aoi_gdf)
    else:
        print("fallback geocode")
        geocode_result = await find_location_boundary(location)
        geocode_result = json.loads(geocode_result) #cast as a json
        aoi_gdf = download_geometry_from_s3(geocode_result["geometry_s3_url"])
        
    images = get_filtered_images(aoi_gdf, bands=["red", "green", "nir", "nir08", "swir2"], max_cloud=max_cloud, current_date_str=current_date_str, location=location)
    print(images)

    logger.info(f"✅ STEP 1 RESULT: Found {len(images)} images")

    if not images:
        logger.info("⚠️ STEP 1 RETRY: No images found, trying with higher cloud coverage (80%)")
        images = get_filtered_images(aoi_gdf, bands=["red", "green", "nir", "nir08", "swir2"], max_cloud=80, current_date_str=current_date_str, location=location)
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
        "nir_s3_url": result.get('nir_s3_url', ''),
        "nir08_s3_url": result.get('nir08_s3_url', ''),
        "swir2_s3_url": result.get('swir2_s3_url', ''),
        "cloud_pct": result.get('cloud_pct'),
        "tile_id": result.get('tile_id', ''),
        "coverage_pct": result.get('coverage_pct', '')
    })
    
    return out
    
    # except Exception as e:
    #     error_msg = f"❌ STEP 1 ERROR: {str(e)}"
    #     logger.error(error_msg)
    #     print(error_msg)
    #     return error_msg


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
        if index_type == "NDVI":
            vegetation_co2_kg = affected_area_m2 * metrics["vegetation_co2_per_m2"]
            vegetation_co2_tons = vegetation_co2_kg / 1000
            
            result["vegetation_co2_kg"] = round(vegetation_co2_kg, 2)
            result["vegetation_co2_tons"] = round(vegetation_co2_tons, 2)
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
