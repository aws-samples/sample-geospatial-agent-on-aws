"""
Geospatial Agent on AWS - Amazon Bedrock AgentCore with observability
(AgentCore/CloudWatch via ADOT, optional Langfuse)
"""
import logging
import os

# Set logging early
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

from strands.telemetry import StrandsTelemetry

# Import BedrockAgentCoreApp
from bedrock_agentcore.runtime import BedrockAgentCoreApp, context
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands.types.content import SystemContentBlock
from mcp import stdio_client, StdioServerParameters
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.session.s3_session_manager import S3SessionManager


from utils.tools import (find_location_boundary, create_bbox_from_coordinates,
                         get_best_geometry, get_rasters, run_bandmath,
                         display_visual, calculator, list_session_assets, calculate_environmental_impact)
from utils.scenario_loader import load_scenario, build_scenario_context
import config

app = BedrockAgentCoreApp()

bedrock_model = BedrockModel(
    model_id=config.MODEL_ID,
    temperature=config.MODEL_TEMPERATURE,
)
    
mcp_client = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(
            command='python3',
            args=['-m', 'awslabs.aws_location_server.server'],
            env={
                'AWS_REGION': config.AWS_REGION,
                'FASTMCP_LOG_LEVEL': 'ERROR',
                **{k: v for k, v in os.environ.items()
                    if k.startswith('AWS_') and v}
            }
        )
    ),
    tool_filters={"allowed": ["search_places"]}
)


@app.entrypoint
async def sat_image_analyzer_agent(payload, context=None):
    """Agent entrypoint with streaming and built-in memory management"""

    # Extract payload data
    trace_id = payload.get("trace_id")
    parent_obs_id = payload.get("parent_obs_id")
    # Get session ID and user ID from context
    session_id = getattr(context, 'session_id', None) or "default_session"
    user_id = getattr(context, 'user_id', None) or payload.get("user_id", "anonymous")
    os.environ['AGENT_SESSION_ID'] = session_id
    os.environ['AGENT_USER_ID'] = user_id
    logger.info(f"📊 Session ID: {session_id}, User ID: {user_id}")

    # Telemetry setup:
    # - If Langfuse is configured: use StrandsTelemetry to export to Langfuse endpoint
    # - Otherwise: do NOT create StrandsTelemetry — let ADOT (aws-opentelemetry-distro)
    #   handle everything via the global TracerProvider it sets up automatically.
    #   Strands auto-detects the global provider (see Strands docs "Option 1").
    from utils.langfuse_setup import setup_langfuse_env_vars
    setup_langfuse_env_vars()  # Sets OTEL endpoint/headers if Langfuse vars are present

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        strands_telemetry = StrandsTelemetry()
        strands_telemetry.setup_otlp_exporter()
        strands_telemetry.setup_meter(enable_console_exporter=False, enable_otlp_exporter=True)
        logger.info(f"✅ OTLP exporter initialized: {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")
    else:
        logger.info("ℹ️ Using ADOT global TracerProvider for AgentCore Observability")

    try:
        with mcp_client:
            # Get the tools from the MCP server
            mcp_tools = mcp_client.list_tools_sync()

            # Check if this is a scenario mode request
            scenario_id = payload.get('scenario_id', None)
            scenario = None
            scenario_context_text = ""

            if scenario_id:
                logger.info(f"📍 Scenario mode detected: {scenario_id}")
                scenario = load_scenario(scenario_id)

                if scenario:
                    # Store scenario in environment for tools to access
                    os.environ['SCENARIO_ID'] = scenario_id
                    os.environ['SCENARIO_PREFIX'] = scenario['s3_prefix']

                    # Build scenario context to inject into system prompt
                    scenario_context_text = build_scenario_context(scenario)
                    logger.info(f"✅ Scenario loaded: {scenario['name']}")
                else:
                    logger.warning(f"⚠️ Scenario {scenario_id} not found, continuing in normal mode")

            # Configure S3 Session Manager for conversation history
            try:
                session_manager = S3SessionManager(
                    session_id=session_id,
                    bucket=config.S3_BUCKET_NAME,
                    prefix="sessions"
                )
                logger.info(f"✅ S3 Session Manager enabled for session: {session_id}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize S3 session manager, continuing without it: {e}")
                logger.exception("Full S3SessionManager error:")
                session_manager = None

            # Create system content with cache point for prompt caching
            base_prompt = config.AGENT_PROMPT + scenario_context_text

            system_content = [
                SystemContentBlock(text=base_prompt),
                SystemContentBlock(cachePoint={"type": "default"})
            ]

            # Create agent with session manager, cached system prompt and langfuse trace attributes
            agent = Agent(
                tools=mcp_tools + [find_location_boundary, create_bbox_from_coordinates,
                                    get_best_geometry, get_rasters, run_bandmath, 
                                    display_visual, calculator, list_session_assets, 
                                    calculate_environmental_impact],
                model=bedrock_model,
                system_prompt=system_content,
                record_direct_tool_call=True,
                conversation_manager=SlidingWindowConversationManager(
                    window_size=3,
                    should_truncate_results=True,
                ),
                session_manager=session_manager,
                trace_attributes={ # for langfuse tracing
                    "session.id": session_id,  
                    "user.id": user_id,
                    "langfuse.tags": ["agentcore","strands","geoagent"]
                }
            )

            logger.info(f"✅ Agent created with S3SessionManager - automatic session persistence enabled")
            
            sent_tool_uses = {}
            open_tools = set()

            # Stream each chunk as it becomes available
            async for event in agent.stream_async(payload["prompt"]):
                
                # Extract text data if present
                if "data" in event:
                    # Close all open tools before sending text
                    for tool_id in list(open_tools):
                        yield '"}'
                        open_tools.remove(tool_id)
                    
                    yield event["data"]

                # Extract current tool use if present
                if "current_tool_use" in event:
                    tool_info = event["current_tool_use"]
                    tool_id = tool_info.get("toolUseId")
                    current_input = tool_info.get("input", "")

                    # Check if this is a new tool or if the input has changed
                    if tool_id not in sent_tool_uses:
                        # Close any other open tools before starting a new one
                        for other_tool_id in list(open_tools):
                            if other_tool_id != tool_id:
                                yield '"}'
                                open_tools.remove(other_tool_id)

                        # Start streaming the JSON
                        tool_start = f'{{"toolUseId": "{tool_id}", "name": "{tool_info.get("name")}", "input": "'
                        yield tool_start
                        open_tools.add(tool_id)
                        sent_tool_uses[tool_id] = ""

                        # If there's initial input, send it (escaped for JSON string)
                        if current_input:
                            escaped = current_input.replace('\\', '\\\\').replace('"', '\\"')
                            yield escaped
                            sent_tool_uses[tool_id] = current_input
                            
                    elif sent_tool_uses[tool_id] != current_input:
                        # Tool input has changed - send only the new part
                        previous_input = sent_tool_uses[tool_id]
                        if current_input.startswith(previous_input):
                            # Send only the delta (new characters), escaped
                            delta = current_input[len(previous_input):]
                            escaped = delta.replace('\\', '\\\\').replace('"', '\\"')
                            yield escaped
                        else:
                            # Input changed completely (shouldn't happen but handle it)
                            escaped = current_input.replace('\\', '\\\\').replace('"', '\\"')
                            yield escaped
                        sent_tool_uses[tool_id] = current_input

            # Close any remaining open tools at the end
            for tool_id in list(open_tools):
                yield '"}'
                open_tools.remove(tool_id)

            logger.info(f"✅ Turn completed - session automatically persisted to S3")


    except Exception as e:
        logger.error(f"Error processing request: {e}", exc_info=True)
        yield f"Error: {str(e)}"


if __name__ == "__main__":
    app.run()