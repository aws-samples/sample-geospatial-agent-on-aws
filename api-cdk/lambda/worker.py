"""Worker Lambda — invoked async, calls AgentCore, writes results to DynamoDB."""

import json
import os
import time

import boto3
from botocore.exceptions import ReadTimeoutError, ConnectTimeoutError, ClientError

# Import shared helpers from handler.py
from handler import (
    _build_prompt,
    _invoke_agent,
    _parse_agent_response,
    _generate_presigned_url,
    _is_invalid_location_response,
)

JOBS_TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "")
dynamodb = boto3.resource("dynamodb")


def _handle_mcp_tool_call(tool_name, arguments):
    """Handle an individual MCP tool call by invoking the agent with a
    tool-specific prompt. Returns the parsed result.

    Each tool maps to a specific agent prompt that triggers the right
    tool chain in the geospatial agent.
    """
    if tool_name == "search_places":
        query = arguments.get("query", "")
        prompt = f"Search for the location '{query}'. Return the coordinates, address, and place metadata. Return only the results, no explanatory text."
        raw = _invoke_agent(prompt)
        return {"tool": "search_places", "query": query, "result": raw}

    elif tool_name == "find_location_boundary":
        location = arguments.get("location", "")
        prompt = f"Find the boundary polygon for '{location}'. Return the S3 URL of the GeoJSON boundary file. Return only the S3 URL, no explanatory text."
        raw = _invoke_agent(prompt)
        return {"tool": "find_location_boundary", "location": location, "result": raw}

    elif tool_name == "get_rasters":
        location = arguments.get("location", "")
        geometry_url = arguments.get("geometry_s3_url", "")
        date_str = arguments.get("current_date_str", "")
        max_cloud = arguments.get("max_cloud", 30)
        prompt = f"Get Sentinel-2 satellite imagery bands for '{location}'."
        if geometry_url:
            prompt += f" Use the boundary geometry at {geometry_url}."
        if date_str:
            prompt += f" Search from 60 days before {date_str} to {date_str}."
        prompt += f" Maximum cloud coverage: {max_cloud}%. Return only the S3 URLs for each band, no explanatory text."
        raw = _invoke_agent(prompt)
        return {"tool": "get_rasters", "location": location, "result": raw}

    elif tool_name == "run_bandmath":
        location = arguments.get("location", "")
        index_type = arguments.get("index_type", "NDVI")
        band1_url = arguments.get("band1_url", "")
        band2_url = arguments.get("band2_url", "")
        date_str = arguments.get("date_str", "")
        geometry_url = arguments.get("geometry_s3_url", "")
        prompt = f"Calculate {index_type} for '{location}' using band1={band1_url} and band2={band2_url}."
        if date_str:
            prompt += f" Date: {date_str}."
        if geometry_url:
            prompt += f" Clip to geometry: {geometry_url}."
        prompt += " Return the statistics (mean, median, class percentages) and S3 URL of the result raster. Return only the data, no explanatory text."
        raw = _invoke_agent(prompt)
        return {"tool": "run_bandmath", "index_type": index_type, "location": location, "result": raw}

    elif tool_name == "display_visual":
        s3_url = arguments.get("s3_url", "")
        title = arguments.get("title", "")
        description = arguments.get("description", "")
        # display_visual is a visualization tool — just acknowledge it
        return {"tool": "display_visual", "s3_url": s3_url, "title": title, "description": description, "result": "Visualization acknowledged"}

    else:
        raise ValueError(f"Unknown MCP tool: {tool_name}")


def _update_job(job_id, status, result=None, error=None):
    """Update job record in DynamoDB."""
    table = dynamodb.Table(JOBS_TABLE_NAME)
    update_expr = "SET #s = :status, updatedAt = :now"
    expr_values = {":status": status, ":now": int(time.time())}
    expr_names = {"#s": "status"}

    if result is not None:
        update_expr += ", #r = :result"
        expr_values[":result"] = result
        expr_names["#r"] = "result"

    if error is not None:
        update_expr += ", #e = :error"
        expr_values[":error"] = error
        expr_names["#e"] = "error"

    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames=expr_names,
    )


def handler(event, context):
    """Process an analysis job or MCP tool call."""
    job_id = event.get("jobId")
    request = event.get("request", {})

    # --- MCP tool call: invoke the agent with a tool-specific prompt ---
    if request.get("mcp_tool_call"):
        tool_name = request.get("tool_name", "")
        arguments = request.get("arguments", {})
        _update_job(job_id, "RUNNING")

        try:
            result = _handle_mcp_tool_call(tool_name, arguments)
            _update_job(job_id, "COMPLETED", result=result)
        except Exception as exc:
            _update_job(job_id, "FAILED", error=f"Tool error: {str(exc)}")
        return

    # --- Standard analysis job ---
    location = request.get("location", "").strip()
    analysis_type = request.get("analysisType", "")
    date_range = request.get("dateRange")
    user_prompt = request.get("prompt", "").strip()

    # Mark as running
    _update_job(job_id, "RUNNING")

    try:
        if location:
            prompt = _build_prompt(location, analysis_type, date_range)
        elif user_prompt:
            # No explicit location — build prompt from the user's natural language request.
            # The agent's geocoding tools will extract place names.
            prompt = f"{user_prompt} Perform {analysis_type} analysis. Return only the statistics and S3 URLs, no explanatory text."
        else:
            _update_job(job_id, "FAILED", error="No location or prompt provided")
            return
        raw_response = _invoke_agent(prompt)
    except (ReadTimeoutError, ConnectTimeoutError):
        _update_job(job_id, "FAILED", error="Agent request timed out")
        return
    except ClientError as exc:
        _update_job(job_id, "FAILED", error=f"AgentCore error: {str(exc)}")
        return
    except Exception as exc:
        _update_job(job_id, "FAILED", error=f"Unexpected error: {str(exc)}")
        return

    # Check for invalid location
    if _is_invalid_location_response(raw_response):
        loc_desc = location if location else "(extracted from prompt)"
        _update_job(job_id, "FAILED", error=f"Unable to process location '{loc_desc}'")
        return

    # Parse and enrich result
    result = _parse_agent_response(raw_response, location or "requested area", analysis_type)

    # Convert TIF image URLs to browser-renderable PNGs.
    # For TIF files: fetch a PNG preview via TiTiler, upload to S3, return presigned PNG URL.
    # For other files (GeoJSON, etc.): skip — they're not renderable as images.
    titiler_url = os.environ.get("TITILER_API_URL", "").rstrip("/")
    titiler_api_key = os.environ.get("TITILER_API_KEY", "")
    s3_client = boto3.client("s3")
    s3_bucket = os.environ.get("S3_BUCKET_NAME", "")

    for url_key in list(result["imageUrls"].keys()):
        url_value = result["imageUrls"][url_key]
        if url_value is None:
            continue

        lower_url = url_value.lower()

        # Skip non-image files and raw band rasters that aren't useful standalone
        if lower_url.endswith(".geojson") or lower_url.endswith(".json"):
            result["imageUrls"][url_key] = None
            continue
        # Skip raw spectral bands (NIR, RED) — they're inputs to index calculations,
        # not meaningful standalone images
        basename = lower_url.split("/")[-1].split("?")[0]
        if basename.startswith("nir_") or basename.startswith("red_"):
            result["imageUrls"][url_key] = None
            continue

        if (lower_url.endswith(".tif") or lower_url.endswith(".tiff")) and titiler_url and s3_bucket:
            try:
                from urllib.parse import quote
                presigned_tif = _generate_presigned_url(url_value)

                # Build TiTiler preview URL with appropriate rendering params
                # based on the image type (TCI vs index rasters)
                titiler_params = f"url={quote(presigned_tif, safe='')}&max_size=512"

                if "tci" in lower_url or "true_color" in lower_url or "truecolor" in lower_url:
                    # True Color Image — 3-band RGB, just needs size constraint
                    pass
                elif "ndvi" in lower_url or "ndwi" in lower_url or "nbr" in lower_url:
                    # Index raster — single band float, needs rescaling and colormap
                    titiler_params += "&rescale=-1,1&colormap_name=rdylgn"
                elif "nir" in lower_url or "red" in lower_url:
                    # Raw band — single band, needs rescaling
                    titiler_params += "&rescale=0,10000"
                else:
                    # Unknown raster — auto rescale
                    titiler_params += "&rescale=0,10000"

                titiler_preview_url = f"{titiler_url}/cog/preview.png?{titiler_params}"

                import urllib.request
                req = urllib.request.Request(titiler_preview_url)
                if titiler_api_key:
                    req.add_header("x-api-key", titiler_api_key)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw_data = resp.read()

                # TiTiler via API Gateway may return base64-encoded PNG
                # Detect and decode if needed
                import base64
                if raw_data[:4] == b'\x89PNG':
                    png_data = raw_data  # Already raw PNG
                else:
                    try:
                        png_data = base64.b64decode(raw_data)
                        if png_data[:4] != b'\x89PNG':
                            raise ValueError("Decoded data is not PNG")
                    except Exception:
                        png_data = raw_data  # Use as-is, hope for the best

                # Upload PNG to S3
                png_key = url_value.replace("s3://", "").split("/", 1)[1] if "s3://" in url_value else url_value
                png_key = png_key.rsplit(".", 1)[0] + ".png"
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=png_key,
                    Body=png_data,
                    ContentType="image/png",
                )
                result["imageUrls"][url_key] = _generate_presigned_url(f"s3://{s3_bucket}/{png_key}")
            except Exception as e:
                print(f"[Worker] TiTiler conversion failed for {url_key}: {e}")
                # Fall back to presigned TIF URL
                result["imageUrls"][url_key] = _generate_presigned_url(url_value)
        else:
            result["imageUrls"][url_key] = _generate_presigned_url(url_value)

    _update_job(job_id, "COMPLETED", result=result)
