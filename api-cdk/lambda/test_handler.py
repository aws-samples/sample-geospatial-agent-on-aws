"""Tests for the proxy Lambda handler."""

import json
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

from handler import handler, CAPABILITIES_RESPONSE, VALID_ANALYSIS_TYPES, _parse_agent_response, _generate_presigned_url, _is_invalid_location_response


class TestCapabilities(unittest.TestCase):
    """GET /capabilities returns the static capabilities payload."""

    def test_returns_200(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 200)

    def test_body_matches_static_payload(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        resp = handler(event, None)
        body = json.loads(resp["body"])
        self.assertEqual(body, CAPABILITIES_RESPONSE)

    def test_analysis_types_count(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        body = json.loads(handler(event, None)["body"])
        self.assertEqual(len(body["analysisTypes"]), 3)

    def test_analysis_type_ids(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        body = json.loads(handler(event, None)["body"])
        ids = [t["id"] for t in body["analysisTypes"]]
        self.assertEqual(ids, ["NDVI", "NDWI", "NBR"])

    def test_fixed_fields(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        body = json.loads(handler(event, None)["body"])
        self.assertEqual(body["satellite"], "Sentinel-2")
        self.assertEqual(body["coverage"], "global")
        self.assertEqual(body["temporalRange"], "60 days rolling")
        self.assertEqual(body["resolution"], "10m")

    def test_cors_headers(self):
        event = {"httpMethod": "GET", "resource": "/capabilities"}
        resp = handler(event, None)
        self.assertEqual(resp["headers"]["Content-Type"], "application/json")
        self.assertIn("Access-Control-Allow-Origin", resp["headers"])

    def test_path_fallback(self):
        """When 'resource' is absent, handler falls back to 'path'."""
        event = {"httpMethod": "GET", "path": "/capabilities"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 200)


class TestAnalyzeValidation(unittest.TestCase):
    """POST /analyze request validation."""

    def _post(self, body=None):
        event = {"httpMethod": "POST", "resource": "/analyze"}
        if body is not None:
            event["body"] = json.dumps(body) if isinstance(body, dict) else body
        return handler(event, None)

    def test_missing_body_returns_400(self):
        resp = self._post()
        self.assertEqual(resp["statusCode"], 400)

    def test_invalid_json_returns_400(self):
        event = {"httpMethod": "POST", "resource": "/analyze", "body": "not json"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 400)

    def test_missing_location_returns_400(self):
        resp = self._post({"analysisType": "NDVI"})
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertIn("location", body["message"].lower())

    def test_empty_location_returns_400(self):
        resp = self._post({"location": "  ", "analysisType": "NDVI"})
        self.assertEqual(resp["statusCode"], 400)

    def test_missing_analysis_type_returns_400(self):
        resp = self._post({"location": "Central Park"})
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertIn("analysisType", body["message"])

    def test_invalid_analysis_type_returns_400(self):
        resp = self._post({"location": "Central Park", "analysisType": "INVALID"})
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertIn("INVALID", body["message"])

    def test_all_valid_types_accepted(self):
        for t in VALID_ANALYSIS_TYPES:
            self.assertIn(t, ["NDVI", "NDWI", "NBR"])


class TestAnalyzeInvocation(unittest.TestCase):
    """POST /analyze with mocked AgentCore invocation."""

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_successful_invocation(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [{"chunk": {"bytes": b"NDVI result text"}}]
        }
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        event = {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps({"location": "Central Park", "analysisType": "NDVI"}),
        }
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 200)

        body = json.loads(resp["body"])
        self.assertEqual(body["location"], "Central Park")
        self.assertEqual(body["analysisType"], "NDVI")
        self.assertEqual(body["textAnalysis"], "NDVI result text")
        self.assertIn("date", body)
        self.assertIsNone(body["statistics"])
        self.assertEqual(body["metadata"]["satellite"], "Sentinel-2")
        self.assertEqual(body["metadata"]["source"], "Copernicus / ESA")

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_invocation_with_date_range(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {"stream": [{"text": "result"}]}

        event = {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps({
                "location": "Vardar River",
                "analysisType": "NDWI",
                "dateRange": {"start": "2024-01-01", "end": "2024-01-31"},
            }),
        }
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 200)

        # Verify the prompt includes the date range
        call_args = mock_client.invoke_agent_runtime.call_args
        prompt = call_args[1].get("prompt") or call_args[0][0] if call_args[0] else call_args[1]["prompt"]
        self.assertIn("2024-01-01", prompt)
        self.assertIn("2024-01-31", prompt)

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_agent_error_returns_500(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.side_effect = Exception("Agent timeout")

        event = {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps({"location": "Central Park", "analysisType": "NBR"}),
        }
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 500)
        body = json.loads(resp["body"])
        self.assertIn("AgentCore invocation failed", body["message"])


class TestUnknownRoute(unittest.TestCase):
    """Unknown routes return 404."""

    def test_unknown_path(self):
        event = {"httpMethod": "GET", "resource": "/unknown"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 404)

    def test_wrong_method(self):
        event = {"httpMethod": "DELETE", "resource": "/capabilities"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 404)


class TestParseAgentResponse(unittest.TestCase):
    """Tests for _parse_agent_response parsing logic."""

    def test_embedded_json_statistics(self):
        raw = (
            'Vegetation analysis complete. '
            '{"classes": [{"name": "Dense Vegetation", "area_m2": 5000.0, "percentage": 60.5}], '
            '"meanIndex": 0.72, "medianIndex": 0.68} '
            'The area shows healthy vegetation on 2024-06-15.'
        )
        result = _parse_agent_response(raw, "Central Park", "NDVI")
        self.assertIsNotNone(result["statistics"])
        self.assertEqual(len(result["statistics"]["classes"]), 1)
        self.assertEqual(result["statistics"]["classes"][0]["name"], "Dense Vegetation")
        self.assertAlmostEqual(result["statistics"]["classes"][0]["area_m2"], 5000.0)
        self.assertAlmostEqual(result["statistics"]["classes"][0]["percentage"], 60.5)
        self.assertAlmostEqual(result["statistics"]["meanIndex"], 0.72)
        self.assertAlmostEqual(result["statistics"]["medianIndex"], 0.68)
        self.assertEqual(result["date"], "2024-06-15")

    def test_s3_urls_extracted(self):
        raw = (
            'Analysis complete. True color image: s3://my-bucket/truecolor/img.tif '
            'Index map: s3://my-bucket/ndvi-index/map.tif '
            'Boundary: s3://my-bucket/boundary/area.geojson'
        )
        result = _parse_agent_response(raw, "Vardar River", "NDVI")
        self.assertEqual(result["imageUrls"]["trueColor"], "s3://my-bucket/truecolor/img.tif")
        self.assertEqual(result["imageUrls"]["indexMap"], "s3://my-bucket/ndvi-index/map.tif")
        self.assertEqual(result["imageUrls"]["boundary"], "s3://my-bucket/boundary/area.geojson")

    def test_no_statistics_returns_none(self):
        raw = "Simple text analysis with no JSON data."
        result = _parse_agent_response(raw, "Test Location", "NDWI")
        self.assertIsNone(result["statistics"])
        self.assertEqual(result["textAnalysis"], "Simple text analysis with no JSON data.")
        self.assertEqual(result["imageUrls"]["trueColor"], None)
        self.assertEqual(result["imageUrls"]["indexMap"], None)
        self.assertEqual(result["imageUrls"]["boundary"], None)

    def test_tool_use_json_stripped(self):
        raw = (
            '{"toolUseId": "abc123", "name": "find_location_boundary", "input": "some data"}'
            ' The vegetation health is good. '
            '{"toolUseId": "def456", "name": "run_bandmath", "input": "more data"}'
            ' NDVI values are above average.'
        )
        result = _parse_agent_response(raw, "Test Area", "NDVI")
        self.assertNotIn("toolUseId", result["textAnalysis"])
        self.assertIn("vegetation health is good", result["textAnalysis"])
        self.assertIn("NDVI values are above average", result["textAnalysis"])

    def test_metadata_always_present(self):
        raw = "Any text."
        result = _parse_agent_response(raw, "Loc", "NBR")
        self.assertEqual(result["metadata"]["satellite"], "Sentinel-2")
        self.assertEqual(result["metadata"]["resolution"], "10m")
        self.assertEqual(result["metadata"]["source"], "Copernicus / ESA")
        self.assertIsNone(result["metadata"]["cloudCoverage"])

    def test_cloud_coverage_extracted(self):
        raw = "Analysis done. Cloud coverage: 12.5% over the area."
        result = _parse_agent_response(raw, "Loc", "NDVI")
        self.assertAlmostEqual(result["metadata"]["cloudCoverage"], 12.5)

    def test_date_defaults_to_today(self):
        raw = "No date mentioned in this text."
        result = _parse_agent_response(raw, "Loc", "NDVI")
        self.assertEqual(result["date"], date.today().isoformat())

    def test_presigned_s3_urls(self):
        raw = (
            'Here is the true color image: '
            'https://my-bucket.s3.amazonaws.com/truecolor/img.tif?X-Amz-Signature=abc '
            'and the index: '
            'https://my-bucket.s3.us-east-1.amazonaws.com/ndvi-index/map.tif?sig=def'
        )
        result = _parse_agent_response(raw, "Loc", "NDVI")
        self.assertIsNotNone(result["imageUrls"]["trueColor"])
        self.assertIn("truecolor", result["imageUrls"]["trueColor"])


class TestGeneratePresignedUrl(unittest.TestCase):
    """Tests for _generate_presigned_url."""

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1"})
    def test_s3_protocol_url(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        result = _generate_presigned_url("s3://my-bucket/path/to/image.tif")
        self.assertEqual(result, "https://presigned-url")
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "my-bucket", "Key": "path/to/image.tif"},
            ExpiresIn=3600,
        )

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-west-2"})
    def test_https_s3_url_with_region(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        result = _generate_presigned_url(
            "https://my-bucket.s3.us-west-2.amazonaws.com/ndvi/map.tif"
        )
        self.assertEqual(result, "https://presigned-url")
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "my-bucket", "Key": "ndvi/map.tif"},
            ExpiresIn=3600,
        )

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1"})
    def test_https_s3_url_without_region(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        result = _generate_presigned_url(
            "https://my-bucket.s3.amazonaws.com/truecolor/img.tif"
        )
        self.assertEqual(result, "https://presigned-url")
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "my-bucket", "Key": "truecolor/img.tif"},
            ExpiresIn=3600,
        )

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1"})
    def test_https_url_with_query_params_stripped(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        result = _generate_presigned_url(
            "https://my-bucket.s3.amazonaws.com/img.tif?X-Amz-Signature=abc"
        )
        self.assertEqual(result, "https://presigned-url")
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "my-bucket", "Key": "img.tif"},
            ExpiresIn=3600,
        )

    def test_non_s3_url_returned_unchanged(self):
        url = "https://example.com/image.tif"
        result = _generate_presigned_url(url)
        self.assertEqual(result, url)

    def test_empty_string_returned_unchanged(self):
        result = _generate_presigned_url("")
        self.assertEqual(result, "")

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1"})
    def test_custom_expiration(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        _generate_presigned_url("s3://bucket/key.tif", expiration=7200)
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "bucket", "Key": "key.tif"},
            ExpiresIn=7200,
        )

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "S3_BUCKET_NAME": "fallback-bucket"})
    def test_fallback_bucket_from_env(self, mock_boto3):
        """When bucket can't be parsed but key is available, use S3_BUCKET_NAME."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        # s3:// with empty netloc but a path — bucket is empty, key is present
        result = _generate_presigned_url("s3:///just-a-key.tif")
        # bucket is empty, key is "just-a-key.tif", fallback bucket used
        self.assertEqual(result, "https://presigned-url")
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "fallback-bucket", "Key": "just-a-key.tif"},
            ExpiresIn=3600,
        )

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1"})
    def test_boto3_error_returns_original(self, mock_boto3):
        mock_boto3.client.side_effect = Exception("Connection error")
        url = "s3://my-bucket/image.tif"
        result = _generate_presigned_url(url)
        self.assertEqual(result, url)


class TestAnalyzePresignedIntegration(unittest.TestCase):
    """Verify _handle_analyze converts S3 URLs to presigned URLs."""

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_image_urls_are_presigned(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [
                {"chunk": {"bytes": b"True color: s3://bucket/truecolor/img.tif Index: s3://bucket/ndvi/map.tif"}}
            ]
        }
        mock_client.generate_presigned_url.return_value = "https://presigned-url"

        event = {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps({"location": "Central Park", "analysisType": "NDVI"}),
        }
        resp = handler(event, None)
        body = json.loads(resp["body"])

        self.assertEqual(body["imageUrls"]["trueColor"], "https://presigned-url")
        self.assertEqual(body["imageUrls"]["indexMap"], "https://presigned-url")
        # generate_presigned_url should have been called for each non-None URL
        self.assertEqual(mock_client.generate_presigned_url.call_count, 2)

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_none_urls_not_presigned(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [{"chunk": {"bytes": b"No images here."}}]
        }

        event = {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps({"location": "Central Park", "analysisType": "NDVI"}),
        }
        resp = handler(event, None)
        body = json.loads(resp["body"])

        self.assertIsNone(body["imageUrls"]["trueColor"])
        self.assertIsNone(body["imageUrls"]["indexMap"])
        self.assertIsNone(body["imageUrls"]["boundary"])
        mock_client.generate_presigned_url.assert_not_called()


class TestAgentTimeoutHandling(unittest.TestCase):
    """Agent timeout errors return 504 Gateway Timeout."""

    def _post(self, body):
        return {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps(body),
        }

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_read_timeout_returns_504(self, mock_boto3):
        from botocore.exceptions import ReadTimeoutError
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.side_effect = ReadTimeoutError(endpoint_url="https://test")

        resp = handler(self._post({"location": "Central Park", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 504)
        body = json.loads(resp["body"])
        self.assertIn("timed out", body["message"])

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_connect_timeout_returns_504(self, mock_boto3):
        from botocore.exceptions import ConnectTimeoutError
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.side_effect = ConnectTimeoutError(endpoint_url="https://test")

        resp = handler(self._post({"location": "Central Park", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 504)
        body = json.loads(resp["body"])
        self.assertIn("timed out", body["message"])

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_client_error_request_timeout_returns_504(self, mock_boto3):
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "RequestTimeout", "Message": "Request timed out"}},
            "InvokeAgentRuntime",
        )

        resp = handler(self._post({"location": "Central Park", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 504)
        body = json.loads(resp["body"])
        self.assertIn("timed out", body["message"])

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_non_timeout_client_error_returns_500(self, mock_boto3):
        from botocore.exceptions import ClientError
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
            "InvokeAgentRuntime",
        )

        resp = handler(self._post({"location": "Central Park", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 500)
        body = json.loads(resp["body"])
        self.assertIn("AgentCore invocation failed", body["message"])


class TestInvalidLocationHandling(unittest.TestCase):
    """Invalid location detection returns 422."""

    def _post(self, body):
        return {
            "httpMethod": "POST",
            "resource": "/analyze",
            "body": json.dumps(body),
        }

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_empty_agent_response_returns_422(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {"stream": []}

        resp = handler(self._post({"location": "xyznonexistent", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 422)
        body = json.loads(resp["body"])
        self.assertIn("xyznonexistent", body["message"])
        self.assertIn("could not be found", body["message"])

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_could_not_find_location_returns_422(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [{"chunk": {"bytes": b"I could not find the specified location."}}]
        }

        resp = handler(self._post({"location": "Atlantis", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 422)
        body = json.loads(resp["body"])
        self.assertIn("Atlantis", body["message"])

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_unable_to_locate_returns_422(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [{"chunk": {"bytes": b"Unable to locate the area you specified."}}]
        }

        resp = handler(self._post({"location": "Nowhere Land", "analysisType": "NBR"}), None)
        self.assertEqual(resp["statusCode"], 422)

    @patch("handler.boto3")
    @patch.dict("os.environ", {"AWS_REGION": "us-east-1", "AGENT_RUNTIME_ARN": "arn:aws:test"})
    def test_valid_response_not_flagged_as_invalid(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_agent_runtime.return_value = {
            "stream": [{"chunk": {"bytes": b"NDVI analysis for Central Park shows healthy vegetation."}}]
        }

        resp = handler(self._post({"location": "Central Park", "analysisType": "NDVI"}), None)
        self.assertEqual(resp["statusCode"], 200)


class TestIsInvalidLocationResponse(unittest.TestCase):
    """Unit tests for _is_invalid_location_response helper."""

    def test_empty_string_is_invalid(self):
        self.assertTrue(_is_invalid_location_response(""))

    def test_none_is_invalid(self):
        self.assertTrue(_is_invalid_location_response(None))

    def test_whitespace_only_is_invalid(self):
        self.assertTrue(_is_invalid_location_response("   "))

    def test_could_not_find_is_invalid(self):
        self.assertTrue(_is_invalid_location_response("I could not find that location."))

    def test_invalid_location_is_invalid(self):
        self.assertTrue(_is_invalid_location_response("The invalid location was provided."))

    def test_normal_response_is_valid(self):
        self.assertFalse(_is_invalid_location_response("NDVI analysis complete. Vegetation is healthy."))


class TestMissingAnalysisTypeErrorMessage(unittest.TestCase):
    """Verify missing/invalid analysisType error messages include valid options."""

    def _post(self, body):
        event = {"httpMethod": "POST", "resource": "/analyze"}
        event["body"] = json.dumps(body)
        return handler(event, None)

    def test_missing_analysis_type_message(self):
        resp = self._post({"location": "Central Park"})
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        self.assertIn("analysisType", body["message"])

    def test_invalid_analysis_type_lists_valid_options(self):
        resp = self._post({"location": "Central Park", "analysisType": "FAKE"})
        self.assertEqual(resp["statusCode"], 400)
        body = json.loads(resp["body"])
        for valid_type in VALID_ANALYSIS_TYPES:
            self.assertIn(valid_type, body["message"])


if __name__ == "__main__":
    unittest.main()
