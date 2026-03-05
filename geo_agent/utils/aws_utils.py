"""
AWS utility functions for S3 operations and geometry handling.

This module provides helper functions for:
- Downloading JSON data from S3
- Downloading GeoJSON geometries from S3 and loading as GeoDataFrames
- S3 URL parsing and validation
- Managing geospatial data stored in S3 buckets

All functions expect S3 URLs in the format: s3://bucket-name/key/path
"""
import json
import boto3
import logging
from typing import Dict, Any
import geopandas as gpd
from io import BytesIO, StringIO

logger = logging.getLogger(__name__)

def list_files_in_s3(bucket_name:str, prefix:str):
    """
    List all files in an S3 bucket with a given prefix.

    Args:
        bucket_name: Name of the S3 bucket
        prefix: Prefix to filter files in the bucket

    Returns:
        List of file names with the given prefix
    """
    s3 = boto3.client('s3')
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    files = [obj['Key'] for obj in response.get('Contents', [])]
    return files

def download_from_s3(s3_url: str) -> Dict[Any, Any]:
    """
    Download JSON data from S3 URL
    
    Args:
        s3_url: S3 URL in format s3://bucket/key
        
    Returns:
        Parsed JSON data as dictionary
    """
    try:
        # Parse S3 URL
        if not s3_url.startswith('s3://'):
            raise ValueError(f"Invalid S3 URL format: {s3_url}")
        
        # Extract bucket and key
        s3_path = s3_url[5:]  # Remove 's3://'
        bucket, key = s3_path.split('/', 1)
        
        # Download from S3
        s3_client = boto3.client('s3')
        response = s3_client.get_object(Bucket=bucket, Key=key)
        
        # Parse JSON content
        content = response['Body'].read().decode('utf-8')
        data = json.loads(content)
        
        logger.info(f"✅ Downloaded data from {s3_url}")
        return data
        
    except Exception as e:
        logger.error(f"❌ Failed to download from {s3_url}: {str(e)}")
        raise

def download_geojson_from_s3(s3_url: str) -> gpd.GeoDataFrame:
    """
    Download GeoJSON data from S3 URL and return as GeoDataFrame
    
    Args:
        s3_url: S3 URL in format s3://bucket/key
        
    Returns:
        GeoDataFrame with spatial data
    """
    try:
        # Parse S3 URL
        if not s3_url.startswith('s3://'):
            raise ValueError(f"Invalid S3 URL format: {s3_url}")
        
        # Extract bucket and key
        s3_path = s3_url[5:]  # Remove 's3://'
        bucket, key = s3_path.split('/', 1)
        
        # Download from S3
        s3_client = boto3.client('s3')
        response = s3_client.get_object(Bucket=bucket, Key=key)
        
        # Read GeoJSON content into GeoDataFrame
        content = response['Body'].read().decode('utf-8')
        gdf = gpd.read_file(content)
        
        logger.info(f"✅ Downloaded GeoDataFrame from {s3_url}")
        return gdf
        
    except Exception as e:
        logger.error(f"❌ Failed to download GeoJSON from {s3_url}: {str(e)}")
        raise

def download_geometry_from_s3(s3_url: str) -> gpd.GeoDataFrame:
    """
    Download geometry data from S3 URL (GeoJSON format)

    Args:
        s3_url: S3 URL in format s3://bucket/key (must be .geojson file)

    Returns:
        GeoDataFrame with spatial data
    """
    if not s3_url.endswith('.geojson'):
        raise ValueError(f"Only GeoJSON format is supported. Got: {s3_url}")

    return download_geojson_from_s3(s3_url)