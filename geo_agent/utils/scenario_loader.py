"""
Scenario Loader - Loads pre-configured use cases from S3
"""
import json
import boto3
import logging
from typing import Dict, Optional
import config

logger = logging.getLogger(__name__)

s3_client = boto3.client('s3', region_name=config.AWS_REGION)


def load_scenario(scenario_id: str) -> Optional[Dict]:
    """
    Load a pre-configured scenario from S3.

    Args:
        scenario_id: The scenario identifier (e.g., 'la-fires-2025')

    Returns:
        Dict with scenario metadata, asset URLs, and narrative
        Returns None if scenario not found

    Expected S3 structure:
        s3://bucket/use-cases/{scenario_id}/
            ├── config.json
            ├── geometry.geojson
            ├── before/
            ├── after/
            ├── analysis.json
            └── narrative.md
    """
    bucket = config.S3_BUCKET_NAME
    scenario_prefix = f"use-cases/{scenario_id}/"

    logger.info(f"Loading scenario: {scenario_id} from {bucket}/{scenario_prefix}")

    try:
        # Load config.json
        config_key = f"{scenario_prefix}config.json"
        config_obj = s3_client.get_object(Bucket=bucket, Key=config_key)
        scenario_config = json.loads(config_obj['Body'].read().decode('utf-8'))

        # Load narrative.md
        narrative_key = f"{scenario_prefix}narrative.md"
        try:
            narrative_obj = s3_client.get_object(Bucket=bucket, Key=narrative_key)
            narrative = narrative_obj['Body'].read().decode('utf-8')
        except s3_client.exceptions.NoSuchKey:
            logger.warning(f"No narrative.md found for {scenario_id}")
            narrative = ""

        # Load analysis.json (pre-computed metrics)
        analysis_key = f"{scenario_prefix}analysis.json"
        try:
            analysis_obj = s3_client.get_object(Bucket=bucket, Key=analysis_key)
            analysis = json.loads(analysis_obj['Body'].read().decode('utf-8'))
        except s3_client.exceptions.NoSuchKey:
            logger.warning(f"No analysis.json found for {scenario_id}")
            analysis = {}

        # Build asset URLs
        assets = {
            "geometry_url": f"s3://{bucket}/{scenario_prefix}geometry.geojson",
            "before": {
                "tci": f"s3://{bucket}/{scenario_prefix}before/tci.tif",
                "red": f"s3://{bucket}/{scenario_prefix}before/red.tif",
                "nir08": f"s3://{bucket}/{scenario_prefix}before/nir08.tif",
                "swir2": f"s3://{bucket}/{scenario_prefix}before/swir2.tif",
                "nbr": f"s3://{bucket}/{scenario_prefix}before/nbr.tif",
            },
            "after": {
                "tci": f"s3://{bucket}/{scenario_prefix}after/tci.tif",
                "red": f"s3://{bucket}/{scenario_prefix}after/red.tif",
                "nir08": f"s3://{bucket}/{scenario_prefix}after/nir08.tif",
                "swir2": f"s3://{bucket}/{scenario_prefix}after/swir2.tif",
                "nbr": f"s3://{bucket}/{scenario_prefix}after/nbr.tif",
            }
        }

        # Construct full scenario object
        scenario = {
            "id": scenario_id,
            "name": scenario_config.get("name", scenario_id),
            "description": scenario_config.get("description", ""),
            "location": scenario_config.get("location", ""),
            "dates": scenario_config.get("dates", {}),
            "s3_prefix": f"s3://{bucket}/{scenario_prefix}",
            "assets": assets,
            "analysis": analysis,
            "narrative": narrative,
            "config": scenario_config
        }

        logger.info(f"✅ Scenario loaded: {scenario['name']}")
        return scenario

    except s3_client.exceptions.NoSuchKey as e:
        logger.error(f"❌ Scenario not found: {scenario_id} - Missing {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Error loading scenario {scenario_id}: {e}")
        return None


def build_scenario_context(scenario: Dict) -> str:
    """
    Build system prompt context for a loaded scenario.

    Args:
        scenario: Scenario dict from load_scenario()

    Returns:
        String to append to system prompt
    """

    assets_summary = f"""

═══════════════════════════════════════════════════════════
🔥 SCENARIO MODE ACTIVE: {scenario['name']}
═══════════════════════════════════════════════════════════

**CRITICAL**: This is a PRE-LOADED scenario. All data is already available.

📍 **Location**: {scenario['location']}
📅 **Dates**:
  - Before: {scenario['dates'].get('before', 'N/A')}
  - After: {scenario['dates'].get('after', 'N/A')}

📊 **PRE-COMPUTED ANALYSIS** (Use these metrics IMMEDIATELY):
{json.dumps(scenario['analysis'], indent=2) if scenario['analysis'] else 'None'}

📄 **Background Context**:
{scenario['narrative']}

🎯 **SCENARIO MODE INSTRUCTIONS** (FOLLOW THESE):

1. **IMMEDIATELY provide the analysis** when user asks about this event:
   - State the location, dates, and key metrics from the pre-computed analysis above
   - Reference the total area burned, structures at risk, severity distribution
   - Include context from the narrative (weather conditions, terrain, etc.)

2. **DO NOT run normal workflows**:
   - DO NOT call search_places or find_location_boundary (location already known)
   - DO NOT call get_rasters (imagery already available if needed)
   - You CAN call display_visual() to show pre-loaded assets if requested

3. **Available pre-loaded assets** (use ONLY if user specifically requests visualization):
   - Geometry: `{scenario['assets']['geometry_url']}`
   - Before imagery: TCI, Red, NIR08, SWIR2, NBR at `{scenario['s3_prefix']}before/`
   - After imagery: TCI, Red, NIR08, SWIR2, NBR at `{scenario['s3_prefix']}after/`

4. **For follow-up questions**:
   - Continue referencing the pre-computed analysis
   - Break down severity distributions, structure impacts, etc.
   - Provide context from the narrative
   - DO NOT revert to normal "I need to analyze" workflows

**YOU ARE IN SCENARIO MODE - Treat this as a pre-analyzed case study, NOT a new analysis request!**

═══════════════════════════════════════════════════════════
"""

    return assets_summary


def list_available_scenarios(bucket: str = None) -> list:
    """
    List all available scenarios in S3.

    Args:
        bucket: S3 bucket name (defaults to config.S3_BUCKET_NAME)

    Returns:
        List of scenario IDs
    """
    if bucket is None:
        bucket = config.S3_BUCKET_NAME

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix="use-cases/",
            Delimiter="/"
        )

        scenarios = []
        if 'CommonPrefixes' in response:
            for prefix in response['CommonPrefixes']:
                # Extract scenario ID from prefix
                # e.g., "use-cases/la-fires-2025/" → "la-fires-2025"
                scenario_id = prefix['Prefix'].replace('use-cases/', '').rstrip('/')
                scenarios.append(scenario_id)

        logger.info(f"Found {len(scenarios)} scenarios: {scenarios}")
        return scenarios

    except Exception as e:
        logger.error(f"Error listing scenarios: {e}")
        return []
