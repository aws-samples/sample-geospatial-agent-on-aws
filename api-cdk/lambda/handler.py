"""Proxy Lambda for Geospatial Agent REST API."""

import json
import os
import re
from datetime import date
from urllib.parse import urlparse, unquote

import boto3
from botocore.exceptions import ReadTimeoutError, ConnectTimeoutError, ClientError

VALID_ANALYSIS_TYPES = ["NDVI", "NDWI", "NBR"]

INVALID_LOCATION_INDICATORS = [
    "could not find",
    "invalid location",
    "unable to locate",
    "location not found",
    "cannot find",
    "unrecognized location",
    "no results found",
    "could not geocode",
    "failed to geocode",
    "unknown location",
]

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

CAPABILITIES_RESPONSE = {
    "analysisTypes": [
        {
            "id": "NDVI",
            "name": "Normalized Difference Vegetation Index",
            "description": "Measures vegetation health",
        },
        {
            "id": "NDWI",
            "name": "Normalized Difference Water Index",
            "description": "Detects water bodies and moisture",
        },
        {
            "id": "NBR",
            "name": "Normalized Burn Ratio",
            "description": "Assesses burn severity",
        },
    ],
    "satellite": "Sentinel-2",
    "coverage": "global",
    "temporalRange": "60 days rolling",
    "resolution": "10m",
}


def _response(status_code, body):
    """Build an API Gateway Lambda proxy integration response."""
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def _build_prompt(location, analysis_type, date_range=None):
    """Construct a structured prompt for the AgentCore agent."""
    prompt = f"Analyze {analysis_type} for {location}."
    if date_range:
        start = date_range.get("start", "")
        end = date_range.get("end", "")
        if start and end:
            prompt += f" Use imagery from {start} to {end}."
    prompt += " Return only the statistics and S3 URLs, no explanatory text."
    return prompt


def _invoke_agent(prompt):
    """Invoke AgentCore runtime and collect the streamed text response."""
    region = os.environ.get("AWS_REGION")
    agent_runtime_arn = os.environ.get("AGENT_RUNTIME_ARN", "")

    client = boto3.client("bedrock-agentcore", region_name=region)
    payload = json.dumps({"prompt": prompt}).encode("utf-8")
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        payload=payload,
    )

    # Process the streaming response
    content_type = response.get("contentType", "")
    chunks = []

    if "text/event-stream" in content_type:
        # SSE streaming response
        for line in response["response"].iter_lines(chunk_size=10):
            if line:
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                if decoded.startswith("data: "):
                    chunks.append(decoded[6:])
                else:
                    chunks.append(decoded)
    elif response.get("response"):
        # Standard response body
        for chunk in response["response"]:
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode("utf-8"))
            else:
                chunks.append(str(chunk))

    return "".join(chunks)


def _parse_agent_response(raw_text, location, analysis_type):
    """Parse raw agent text into a structured AnalyzeResponse dict."""

    # --- strip tool-use JSON objects from the text ---
    tool_use_pattern = re.compile(
        r'\{"toolUseId"\s*:.*?\}',
        re.DOTALL,
    )
    clean_text = tool_use_pattern.sub("", raw_text).strip()

    # --- extract statistics from embedded JSON blocks ---
    statistics = None
    # Look for a JSON block that contains a "classes" array
    json_block_pattern = re.compile(r'\{[^{}]*"classes"\s*:\s*\[.*?\][^{}]*\}', re.DOTALL)
    json_match = json_block_pattern.search(raw_text)
    if json_match:
        try:
            stats_obj = json.loads(json_match.group())
            classes = []
            for c in stats_obj.get("classes", []):
                classes.append({
                    "name": str(c.get("name", "")),
                    "area_m2": float(c.get("area_m2", 0)),
                    "percentage": float(c.get("percentage", 0)),
                })
            statistics = {
                "classes": classes,
                "meanIndex": float(stats_obj.get("meanIndex", 0)),
                "medianIndex": float(stats_obj.get("medianIndex", 0)),
            }
            # Remove the JSON block from the clean text
            clean_text = json_block_pattern.sub("", clean_text).strip()
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # --- extract S3 URLs ---
    s3_url_pattern = re.compile(r'((?:https?://[^\s]*?\.s3[^\s]*)|(?:s3://[^\s]+))')
    s3_urls = s3_url_pattern.findall(raw_text)

    image_urls = {"trueColor": None, "indexMap": None, "boundary": None}
    for url in s3_urls:
        lower = url.lower()
        if "truecolor" in lower or "true_color" in lower or "true-color" in lower:
            image_urls["trueColor"] = url
        elif "boundary" in lower:
            image_urls["boundary"] = url
        elif "index" in lower or analysis_type.lower() in lower:
            image_urls["indexMap"] = url
        else:
            # Assign to first empty slot
            if image_urls["trueColor"] is None:
                image_urls["trueColor"] = url
            elif image_urls["indexMap"] is None:
                image_urls["indexMap"] = url
            elif image_urls["boundary"] is None:
                image_urls["boundary"] = url

    # --- extract cloud coverage ---
    cloud_coverage = None
    cloud_match = re.search(r'cloud\s*(?:coverage|cover)\s*[:\s]*(\d+(?:\.\d+)?)\s*%?', raw_text, re.IGNORECASE)
    if cloud_match:
        cloud_coverage = float(cloud_match.group(1))

    # --- extract date ---
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', raw_text)
    analysis_date = date_match.group(1) if date_match else date.today().isoformat()

    # --- clean up residual whitespace in textAnalysis ---
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

    return {
        "location": location,
        "analysisType": analysis_type,
        "date": analysis_date,
        "textAnalysis": clean_text,
        "statistics": statistics,
        "imageUrls": image_urls,
        "metadata": {
            "satellite": "Sentinel-2",
            "resolution": "10m",
            "cloudCoverage": cloud_coverage,
            "source": "Copernicus / ESA",
        },
    }


def _generate_presigned_url(s3_url, expiration=3600):
    """Convert an S3 URL to a presigned GET URL.

    Supports both ``s3://bucket/key`` and
    ``https://bucket.s3[.region].amazonaws.com/key`` formats.
    Returns the original URL unchanged if parsing fails.
    """
    bucket = None
    key = None

    try:
        if s3_url.startswith("s3://"):
            # s3://bucket/key
            parsed = urlparse(s3_url)
            bucket = parsed.netloc
            key = parsed.path.lstrip("/")
        elif "s3" in s3_url and "amazonaws.com" in s3_url:
            # https://bucket.s3[.region].amazonaws.com/key[?query]
            parsed = urlparse(s3_url.split("?")[0])  # strip existing query params
            host = parsed.hostname or ""
            # bucket.s3.region.amazonaws.com  or  bucket.s3.amazonaws.com
            parts = host.split(".s3")
            if parts:
                bucket = parts[0]
            key = parsed.path.lstrip("/")
            if key:
                key = unquote(key)

        if not bucket or not key:
            # Fallback: use S3_BUCKET_NAME env var if bucket couldn't be parsed
            fallback_bucket = os.environ.get("S3_BUCKET_NAME", "")
            if fallback_bucket and key:
                bucket = fallback_bucket
            else:
                return s3_url

        region = os.environ.get("AWS_REGION", "us-east-1")
        s3_client = boto3.client("s3", region_name=region)
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiration,
        )
    except Exception:
        return s3_url


def _is_invalid_location_response(raw_text):
    """Check if the agent response indicates the location was invalid or not found."""
    if not raw_text or not raw_text.strip():
        return True
    lower = raw_text.lower()
    return any(indicator in lower for indicator in INVALID_LOCATION_INDICATORS)


def _handle_analyze(event):
    """Handle POST /analyze — validate, invoke AgentCore, return result."""
    # Parse request body
    raw_body = event.get("body")
    if not raw_body:
        return _response(400, {"message": "Missing request body"})

    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"message": "Invalid JSON in request body"})

    # Validate required fields
    location = body.get("location")
    if not location or not isinstance(location, str) or not location.strip():
        return _response(400, {"message": "Missing or invalid 'location' field"})

    analysis_type = body.get("analysisType")
    if not analysis_type:
        return _response(400, {"message": "Missing 'analysisType' field"})
    if analysis_type not in VALID_ANALYSIS_TYPES:
        return _response(400, {
            "message": f"Invalid analysisType '{analysis_type}'. Must be one of: {', '.join(VALID_ANALYSIS_TYPES)}"
        })

    date_range = body.get("dateRange")

    # Build prompt and invoke agent
    prompt = _build_prompt(location, analysis_type, date_range)

    try:
        raw_response = _invoke_agent(prompt)
    except (ReadTimeoutError, ConnectTimeoutError):
        return _response(504, {"message": "Agent request timed out. The geospatial analysis took too long to complete. Please try again."})
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("RequestTimeout", "RequestTimeoutException"):
            return _response(504, {"message": "Agent request timed out. The geospatial analysis took too long to complete. Please try again."})
        return _response(500, {"message": f"AgentCore invocation failed: {str(exc)}"})
    except Exception as exc:
        return _response(500, {"message": f"AgentCore invocation failed: {str(exc)}"})

    # Detect invalid location from agent response
    if _is_invalid_location_response(raw_response):
        return _response(422, {
            "message": f"Unable to process location '{location.strip()}'. The location could not be found or geocoded. Please provide a valid location name or coordinates."
        })

    result = _parse_agent_response(raw_response, location.strip(), analysis_type)

    # Convert raw S3 URLs to presigned URLs
    for url_key, url_value in result["imageUrls"].items():
        if url_value is not None:
            result["imageUrls"][url_key] = _generate_presigned_url(url_value)

    return _response(200, result)


def handler(event, context):
    """Lambda entry point — routes requests by method + path.
    Supports both API Gateway REST and Lambda Function URL event formats.
    """
    # API Gateway REST format
    method = event.get("httpMethod", "")
    resource = event.get("resource") or event.get("path") or ""

    # Lambda Function URL format
    if not method:
        req_ctx = event.get("requestContext", {}).get("http", {})
        method = req_ctx.get("method", "")
        resource = req_ctx.get("path", "")

    if method == "GET" and resource == "/capabilities":
        return _response(200, CAPABILITIES_RESPONSE)

    if method == "POST" and resource == "/analyze":
        return _handle_analyze(event)

    return _response(404, {"message": "Not Found"})
