#!/bin/bash

# Deploy AgentCore agent without Langfuse observability
# Simpler deployment using only required environment variables

set -e

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found!"
    echo "Please create a .env file based on .env.example"
    exit 1
fi

echo "Loading configuration from .env file..."

# Load environment variables from .env file
set -a
source .env
set +a

# Validate required variables
required_vars=("S3_BUCKET_NAME" "AGENTCORE_ARN")
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        echo "Error: Required environment variable $var is not set in .env"
        exit 1
    fi
done

# Set defaults for optional variables
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Export region so agentcore CLI deploys to the correct region
export AWS_REGION
export AWS_DEFAULT_REGION="${AWS_REGION}"

echo "Configuration:"
echo "   S3 Bucket: ${S3_BUCKET_NAME}"
echo "   Model: ${MODEL_ID}"
echo "   Region: ${AWS_REGION}"
echo ""

# Configure agent
echo "Configuring agent..."
agentcore configure \
    --deployment-type container \
    --entrypoint geospatial_agent_on_aws.py \
    --name geospatial_agent_on_aws \
    --execution-role "${AGENTCORE_ARN}" \
    --disable-memory \
    --region "${AWS_REGION}" \
    --non-interactive

# Restore Dockerfile
cp Dockerfile_geospatial_agent_on_aws Dockerfile

echo ""
echo "Launching agent..."

# Deploy with environment variables
agentcore launch \
    --env "AWS_REGION=${AWS_REGION}" \
    --env "S3_BUCKET_NAME=${S3_BUCKET_NAME}" \
    --env "MODEL_ID=${MODEL_ID}" \
    --env "BEDROCK_MODEL_ID=${MODEL_ID}" \
    --auto-update-on-conflict

echo ""
echo "Deployment complete!"
echo ""
echo "Next steps:"
echo "   1. Get your Agent Runtime ARN: cat .bedrock_agentcore.yaml | grep agent_arn"
echo "   2. Test: agentcore invoke '{\"prompt\": \"Show vegetation for Hyde Park London\"}'"
