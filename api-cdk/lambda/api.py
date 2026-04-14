"""API Lambda — handles submit, poll, and capabilities (sync, fast)."""

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
    if not location or not isinstance(location, str) or not location.strip():
        return _response(400, {"message": "Missing or invalid 'location' field"})

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

    return _response(404, {"message": "Not Found"})
