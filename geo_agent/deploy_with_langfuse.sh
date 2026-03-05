#!/bin/bash

# Deploy AgentCore agent with Langfuse environment variables
# This script deploys the satellite image analyzer agent with Langfuse observability enabled

set -e

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found!"
    echo "Please create a .env file based on .env.example"
    exit 1
fi

echo "📋 Loading configuration from .env file..."

# Load environment variables from .env file
set -a  # automatically export all variables
source .env
set +a  # stop automatically exporting

# Validate required variables
required_vars=("S3_BUCKET_NAME" "LANGFUSE_SECRET_KEY" "LANGFUSE_PUBLIC_KEY" "LANGFUSE_BASE_URL" "AGENTCORE_ARN")
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        echo "❌ Error: Required environment variable $var is not set in .env"
        exit 1
    fi
done

echo "🚀 Deploying AgentCore agent with Langfuse observability..."

# Set defaults for optional variables
BEDROCK_MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Build Basic Auth header for OTEL
LANGFUSE_AUTH=$(echo -n "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" | base64)
OTEL_ENDPOINT="${LANGFUSE_BASE_URL}/api/public/otel"
OTEL_HEADERS="Authorization=Basic ${LANGFUSE_AUTH}"

echo "📝 Configuration:"
echo "   Agent: geospatial_agent_on_aws"
echo "   Entrypoint: geospatial_agent_on_aws.py"
echo "   Langfuse URL: ${LANGFUSE_BASE_URL}"
echo "   OTEL Endpoint: ${OTEL_ENDPOINT}"
echo ""

# # Configure agent (if not already configured)
# echo "⚙️  Configuring agent..."
# Use yes to auto-accept all prompts
yes "" | agentcore configure \
  --entrypoint geospatial_agent_on_aws.py \
  --name geospatial_agent_on_aws \
  --execution-role ${AGENTCORE_ARN} \
  --disable-otel

echo ""
echo "🚀 Launching agent with Langfuse environment variables..."

#overwrite Dockerfile with custom Docker
cp Dockerfile_geospatial_agent_on_aws Dockerfile

# Deploy with environment variables for LangFuse observability and app configuration
agentcore launch \
  --env "AWS_REGION=${AWS_REGION}" \
  --env "S3_BUCKET_NAME=${S3_BUCKET_NAME}" \
  --env "MODEL_ID=${BEDROCK_MODEL_ID}" \
  --env "BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID}" \
  --env "OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_ENDPOINT}" \
  --env "LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}" \
  --env "LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}" \
  --env "LANGFUSE_BASE_URL=${LANGFUSE_BASE_URL}" \
  --env "OTEL_EXPORTER_OTLP_HEADERS=${OTEL_HEADERS}" \
  --env "LANGFUSE_PROJECT_NAME=${LANGFUSE_PROJECT_NAME}" \
  --env "DISABLE_ADOT_OBSERVABILITY=true" \
  --auto-update-on-conflict

echo ""
echo "✅ Deployment complete!"
echo "📊 Langfuse observability is enabled"
echo "🔗 View traces at: ${LANGFUSE_BASE_URL}"
echo ""
echo "💡 Next steps:"
echo "   1. Invoke your agent"
echo "   2. Check CloudWatch logs for: '✅ OTLP exporter initialized'"
echo "   3. View traces in Langfuse dashboard"
