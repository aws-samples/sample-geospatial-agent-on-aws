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
    """Process an analysis job."""
    job_id = event.get("jobId")
    request = event.get("request", {})

    location = request.get("location", "").strip()
    analysis_type = request.get("analysisType", "")
    date_range = request.get("dateRange")

    # Mark as running
    _update_job(job_id, "RUNNING")

    try:
        prompt = _build_prompt(location, analysis_type, date_range)
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
        _update_job(job_id, "FAILED", error=f"Unable to process location '{location}'")
        return

    # Parse and enrich result
    result = _parse_agent_response(raw_response, location, analysis_type)

    for url_key, url_value in result["imageUrls"].items():
        if url_value is not None:
            result["imageUrls"][url_key] = _generate_presigned_url(url_value)

    _update_job(job_id, "COMPLETED", result=result)
