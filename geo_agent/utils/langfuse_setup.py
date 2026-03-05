"""
Langfuse Setup Module
Handles Langfuse configuration with proper error handling
"""
import os
import base64
import logging

logger = logging.getLogger(__name__)

def setup_langfuse_env_vars():
    """
    Configure Langfuse environment variables for OpenTelemetry.
    
    This sets up the OTEL endpoint and auth headers that will be used
    when StrandsTelemetry.setup_otlp_exporter() is called.
    
    Returns True if configured successfully, False otherwise.
    """
    try:
        # Check required environment variables
        required_vars = ['LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL']
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        
        if missing_vars:
            logger.warning(f"⚠️ Missing Langfuse environment variables: {missing_vars}. Telemetry disabled.")
            logger.info("Set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL to enable telemetry.")
            return False
        
        # Build Basic Auth header for OTEL
        langfuse_auth = base64.b64encode(
            f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
        ).decode()
        
        # Configure OpenTelemetry endpoint & headers
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = os.environ["LANGFUSE_BASE_URL"] + "/api/public/otel"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {langfuse_auth}"
        
        logger.info("✅ Langfuse OTEL environment variables configured")
        logger.info(f"   OTEL Endpoint: {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to setup Langfuse env vars: {e}", exc_info=True)
        return False


def get_langfuse_client():
    """
    Get Langfuse client if available.
    Returns client or None if not available.
    """
    try:
        from langfuse import get_client
        return get_client()
    except ImportError:
        logger.warning("⚠️ Langfuse SDK not installed. Install with: pip install langfuse")
        return None
    except Exception as e:
        logger.error(f"❌ Failed to get Langfuse client: {e}")
        return None