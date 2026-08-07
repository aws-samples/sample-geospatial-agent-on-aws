"""API Lambda — handles submit, poll, capabilities, and MCP JSON-RPC."""

import json
import os
import time
import uuid

import boto3

JOBS_TABLE_NAME = os.environ.get("JOBS_TABLE_NAME", "")
WORKER_FUNCTION_NAME = os.environ.get("WORKER_FUNCTION_NAME", "")

VALID_ANALYSIS_TYPES = ["NDVI", "NDWI", "NBR"]

CAPABILITIES_RESPONSE = {
    "analysisTypes": [
        {"id": "NDVI", "name": "Vegetation Health", "description": "Normalized Difference Vegetation Index — measures live green vegetation"},
        {"id": "NDWI", "name": "Water Detection", "description": "Normalized Difference Water Index — detects water bodies and moisture"},
        {"id": "NBR", "name": "Burn Severity", "description": "Normalized Burn Ratio — assesses wildfire damage and burn severity"},
    ],
    "satellite": "Sentinel-2",
    "coverage": "global",
    "temporalRange": "60 days rolling",
    "resolution": "10m",
}

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
}

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def _handle_submit(event):
    """POST /analyze — validate, create job, invoke worker async."""
    raw_body = event.get("body")
    if not raw_body:
        return _response(400, {"message": "Missing request body"})

    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"message": "Invalid JSON in request body"})

    location = body.get("location")
    prompt = body.get("prompt")

    # Either location or prompt is required. The agent's geocoding tools
    # can extract place names from a natural language prompt.
    has_location = location and isinstance(location, str) and location.strip()
    has_prompt = prompt and isinstance(prompt, str) and prompt.strip()

    if not has_location and not has_prompt:
        return _response(400, {"message": "Missing 'location' or 'prompt' field"})

    analysis_type = body.get("analysisType")
    if not analysis_type:
        return _response(400, {"message": "Missing 'analysisType' field"})
    if analysis_type not in VALID_ANALYSIS_TYPES:
        return _response(400, {
            "message": f"Invalid analysisType '{analysis_type}'. Must be one of: {', '.join(VALID_ANALYSIS_TYPES)}"
        })

    # Create job record
    job_id = str(uuid.uuid4())
    now = int(time.time())
    ttl = now + 86400  # 24h expiry

    table = dynamodb.Table(JOBS_TABLE_NAME)
    table.put_item(Item={
        "jobId": job_id,
        "status": "PENDING",
        "request": body,
        "createdAt": now,
        "ttl": ttl,
    })

    # Invoke worker async
    lambda_client.invoke(
        FunctionName=WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps({"jobId": job_id, "request": body}),
    )

    return _response(202, {
        "jobId": job_id,
        "status": "PENDING",
        "message": "Analysis submitted. Poll GET /jobs/{jobId} for results.",
    })


def _handle_poll(event):
    """GET /jobs/{jobId} — return job status and results."""
    job_id = event.get("pathParameters", {}).get("jobId")
    if not job_id:
        return _response(400, {"message": "Missing jobId"})

    table = dynamodb.Table(JOBS_TABLE_NAME)
    resp = table.get_item(Key={"jobId": job_id})
    item = resp.get("Item")

    if not item:
        return _response(404, {"message": f"Job '{job_id}' not found"})

    result = {"jobId": job_id, "status": item["status"]}

    if item["status"] == "COMPLETED":
        result["result"] = item.get("result", {})
    elif item["status"] == "FAILED":
        result["error"] = item.get("error", "Unknown error")

    return _response(200, result)


def handler(event, context):
    """Route requests by method + path."""
    method = event.get("httpMethod", "")
    resource = event.get("resource") or event.get("path") or ""

    if method == "GET" and resource == "/capabilities":
        return _response(200, CAPABILITIES_RESPONSE)

    if method == "POST" and resource == "/analyze":
        return _handle_submit(event)

    if method == "GET" and "/jobs/" in resource:
        return _handle_poll(event)

    if method == "POST" and resource == "/mcp":
        return _handle_mcp(event)

    return _response(404, {"message": "Not Found"})


# ---------------------------------------------------------------------------
# MCP JSON-RPC handler
# ---------------------------------------------------------------------------

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "geospatial-agent-mcp"
MCP_SERVER_VERSION = "1.0.0"

MCP_TOOLS = [
    {
        "name": "search_places",
        "description": (
            "Search for places using geocoding. Returns location coordinates, "
            "addresses, and place metadata. Use for finding specific locations "
            "by name or address."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (address, place name, etc.)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_location_boundary",
        "description": (
            "Get the exact boundary polygon for a named location from "
            "OpenStreetMap. Returns a GeoJSON polygon saved to S3. "
            "Use for getting precise boundaries of parks, cities, regions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Place name (e.g., 'Central Park', 'Paris')",
                },
            },
            "required": ["location"],
        },
    },
    {
        "name": "get_rasters",
        "description": (
            "Get Sentinel-2 satellite imagery bands. Searches backwards 60 days "
            "from the specified date. Returns S3 URLs for TCI, red, green, NIR, "
            "NIR08, and SWIR2 bands."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Place name (for filenames)",
                },
                "geometry_s3_url": {
                    "type": "string",
                    "description": (
                        "S3 URL of the boundary geometry from "
                        "find_location_boundary or create_bbox_from_coordinates"
                    ),
                },
                "current_date_str": {
                    "type": "string",
                    "description": (
                        "End date of 60-day search window (YYYY-MM-DD). "
                        "Searches from (date - 60 days) to date."
                    ),
                },
                "max_cloud": {
                    "type": "number",
                    "description": "Maximum cloud coverage percentage",
                    "default": 30,
                },
            },
            "required": ["location"],
        },
    },
    {
        "name": "run_bandmath",
        "description": (
            "Calculate spectral indices (NDVI, NDWI, NBR) from satellite imagery. "
            "NDVI measures vegetation health, NDWI detects water bodies, "
            "NBR assesses burn severity. Returns statistics and classified area "
            "percentages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Place name",
                },
                "index_type": {
                    "type": "string",
                    "description": "Type of spectral index",
                    "enum": ["NDVI", "NDWI", "NBR"],
                },
                "band1_url": {
                    "type": "string",
                    "description": (
                        "S3 URL of first band (red for NDVI, green for NDWI, "
                        "nir08 for NBR)"
                    ),
                },
                "band2_url": {
                    "type": "string",
                    "description": (
                        "S3 URL of second band (nir for NDVI/NDWI, "
                        "swir2 for NBR)"
                    ),
                },
                "date_str": {
                    "type": "string",
                    "description": "Date from get_rasters (YYYY-MM-DD)",
                },
                "geometry_s3_url": {
                    "type": "string",
                    "description": "S3 URL of geometry for clipping",
                },
            },
            "required": ["location", "index_type", "band1_url", "band2_url"],
        },
    },
    {
        "name": "display_visual",
        "description": (
            "Display geometry or imagery (TCI, NDVI, NDWI, NBR) on a map. "
            "Call immediately after each analysis result to visualize it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "s3_url": {
                    "type": "string",
                    "description": "S3 URL to GeoJSON or raster",
                },
                "title": {
                    "type": "string",
                    "description": "Display title",
                },
                "description": {
                    "type": "string",
                    "description": "Optional details",
                    "default": "",
                },
            },
            "required": ["s3_url", "title"],
        },
    },
]


def _jsonrpc_response(result, request_id):
    """Build a JSON-RPC 2.0 success response."""
    return _response(200, {"jsonrpc": "2.0", "result": result, "id": request_id})


def _jsonrpc_error(code, message, request_id, status_code=200):
    """Build a JSON-RPC 2.0 error response."""
    return _response(status_code, {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    })


def _handle_mcp(event):
    """POST /mcp — MCP JSON-RPC handler.

    Handles initialize, tools/list, and tools/call.
    tools/call creates an async job and waits for the result (synchronous
    from the caller's perspective, async internally via worker Lambda).

    Authentication is handled by API Gateway (x-api-key header validated
    against the MCP usage plan key).
    """
    raw_body = event.get("body")
    if not raw_body:
        return _jsonrpc_error(-32700, "Parse error: empty body", None, 400)

    try:
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    except (json.JSONDecodeError, TypeError):
        return _jsonrpc_error(-32700, "Parse error: invalid JSON", None, 400)

    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    # --- initialize ---
    if method == "initialize":
        return _jsonrpc_response({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        }, request_id)

    # --- notifications/initialized ---
    if method == "notifications/initialized":
        return _jsonrpc_response({}, request_id)

    # --- tools/list ---
    if method == "tools/list":
        return _jsonrpc_response({"tools": MCP_TOOLS}, request_id)

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        if not tool_name:
            return _jsonrpc_error(-32602, "Missing tool name", request_id)

        valid_names = {t["name"] for t in MCP_TOOLS}
        if tool_name not in valid_names:
            return _jsonrpc_error(-32602, f"Unknown tool: {tool_name}", request_id)

        # Create a job and invoke the worker synchronously.
        # The worker handles the actual agent invocation.
        job_id = str(uuid.uuid4())
        now = int(time.time())
        ttl = now + 86400

        table = dynamodb.Table(JOBS_TABLE_NAME)
        table.put_item(Item={
            "jobId": job_id,
            "status": "PENDING",
            "request": {"mcp_tool_call": True, "tool_name": tool_name, "arguments": tool_args},
            "createdAt": now,
            "ttl": ttl,
        })

        # Invoke worker synchronously (RequestResponse) so we can return
        # the result in this MCP response
        try:
            worker_response = lambda_client.invoke(
                FunctionName=WORKER_FUNCTION_NAME,
                InvocationType="RequestResponse",
                Payload=json.dumps({
                    "jobId": job_id,
                    "request": {"mcp_tool_call": True, "tool_name": tool_name, "arguments": tool_args},
                }),
            )

            # Read the worker result from DynamoDB
            resp = table.get_item(Key={"jobId": job_id})
            item = resp.get("Item", {})

            if item.get("status") == "COMPLETED":
                result_data = item.get("result", {})
                return _jsonrpc_response({
                    "content": [{"type": "text", "text": json.dumps(result_data) if isinstance(result_data, dict) else str(result_data)}],
                }, request_id)
            elif item.get("status") == "FAILED":
                error_msg = item.get("error", "Tool execution failed")
                return _jsonrpc_response({
                    "content": [{"type": "text", "text": error_msg}],
                    "isError": True,
                }, request_id)
            else:
                # Still pending — shouldn't happen with sync invoke but handle gracefully
                return _jsonrpc_response({
                    "content": [{"type": "text", "text": f"Tool execution in progress. Poll job {job_id} for results."}],
                    "isError": True,
                }, request_id)

        except Exception as e:
            return _jsonrpc_response({
                "content": [{"type": "text", "text": f"Tool execution failed: {str(e)}"}],
                "isError": True,
            }, request_id)

    # --- Unknown method ---
    return _jsonrpc_error(-32601, f"Method not found: {method}", request_id)
