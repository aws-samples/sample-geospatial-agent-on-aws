"""
Geocoding utility functions
"""
import logging
import json

import osmnx as ox
import geopandas as gpd
import geopy.geocoders as geocoders
from shapely.geometry import box, shape

logger = logging.getLogger(__name__)

def get_polygon_of_aoi(location):
    """Get polygon for area of interest using OSMnx with improved error handling"""
    try:
        # Configure OSMnx settings for better reliability
        import osmnx as ox
        import os
        
        logger.info(f"🔍 Attempting OSMnx geocoding for: {location}")
        logger.info(f"   OSMnx version: {ox.__version__}")
        
        # Configure OSMnx cache using environment variables or fallback
        import tempfile
        cache_folder = os.environ.get('OSMNX_CACHE_FOLDER', os.path.join(tempfile.gettempdir(), 'osmnx_cache'))
        use_cache = os.environ.get('OSMNX_USE_CACHE', 'true').lower() == 'true'
        
        try:
            # Ensure cache directory exists
            os.makedirs(cache_folder, exist_ok=True)
            
            # Configure OSMnx settings for version 2.x
            ox.settings.cache_folder = cache_folder
            ox.settings.use_cache = use_cache
            logger.info(f"   ✅ OSMnx cache configured: {cache_folder} (enabled: {use_cache})")
        except Exception as cache_error:
            logger.warning(f"   ⚠️ Could not configure OSMnx cache: {cache_error}")
        
        # Use OSMnx to get the place boundary
        result = ox.geocode_to_gdf(location)
        
        if result is not None and not result.empty:
            # Check if we got a proper boundary (not just a point)
            geom = result.geometry.iloc[0]
            if geom.geom_type == 'Polygon':
                coord_count = len(geom.exterior.coords)
                logger.info(f"✅ OSMnx SUCCESS: Got {geom.geom_type} with {coord_count} coordinates")
                
                if coord_count > 10:  # Proper boundary should have many points
                    return result
                else:
                    logger.warning(f"⚠️ OSMnx returned simple shape with only {coord_count} points, trying fallback")
            elif geom.geom_type == 'MultiPolygon':
                logger.info(f"✅ OSMnx SUCCESS: Got MultiPolygon with {len(geom.geoms)} parts")
                return result
            else:
                logger.warning(f"⚠️ OSMnx returned {geom.geom_type}, not a polygon boundary")
        else:
            logger.warning("⚠️ OSMnx returned empty result")
            
    except Exception as e:
        logger.error(f"❌ OSMnx geocoding failed: {e}")
        
    # Fallback: create a small bounding box around a geocoded point
    logger.info("🔄 Falling back to Nominatim + bounding box")
    try:
        geolocator = geocoders.Nominatim(user_agent="geospatial-agent")
        location_data = geolocator.geocode(location)
        
        if location_data:
            lat, lon = location_data.latitude, location_data.longitude
            logger.info(f"📍 Nominatim coords: ({lat:.6f}, {lon:.6f})")
            
            # Create a small bounding box (roughly 1km x 1km)
            buffer = 0.005  # approximately 500m
            geometry = box(lon - buffer, lat - buffer, lon + buffer, lat + buffer)
            result = gpd.GeoDataFrame([{'geometry': geometry}], crs='EPSG:4326')
            
            logger.warning(f"📦 Created fallback bounding box ({buffer*2*111000:.0f}m x {buffer*2*111000:.0f}m)")
            return result
        else:
            raise ValueError(f"Could not geocode location: {location}")
    except Exception as e2:
        logger.error(f"❌ Fallback geocoding failed: {e2}")
        raise ValueError(f"Could not find location: {location}")

def convert_geometry_of_geojson(gdf):
    """Convert a MultiPolygon geometry to a bounding box if needed"""
    if len(gdf) != 1:
        raise ValueError("The GeoDataFrame must have exactly one row.")

    geometry = gdf.geometry.iloc[0]

    if geometry.geom_type == 'MultiPolygon':
        geometry = box(*geometry.bounds)

    return gpd.GeoDataFrame(geometry=gpd.GeoSeries(geometry), crs=gdf.crs)

def geojson_str_to_gdf(geojson_str: str):
    """Convert GeoJSON string to GeoDataFrame"""
    
    try:
        # Parse JSON string directly
        geojson_data = json.loads(geojson_str)
        
        # Handle single Feature or FeatureCollection
        if geojson_data.get('type') == 'Feature':
            features = [geojson_data]
        else:
            features = geojson_data.get('features', [])
        
        # Convert to GeoDataFrame
        gdf_features = []
        for feature in features:
            geom = shape(feature['geometry'])
            props = feature.get('properties', {})
            gdf_features.append({'geometry': geom, **props})
        
        return gpd.GeoDataFrame(gdf_features, crs='EPSG:4326')
        
    except Exception as e:
        print(f"Error converting GeoJSON to GeoDataFrame: {e}")
        return None

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth in kilometers.

    Args:
        lat1, lon1: Latitude and longitude of first point
        lat2, lon2: Latitude and longitude of second point

    Returns:
        Distance in kilometers
    """
    import math
    
    # Convert to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.asin(math.sqrt(a))

    # Radius of Earth in kilometers
    r = 6371

    return c * r
