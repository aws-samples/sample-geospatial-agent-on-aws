#!/bin/bash
set -e

# Quick ECS Update - Bypasses full CDK deploy
# Rebuilds Docker image, pushes to ECR, updates ECS service
# Takes ~5-8 minutes instead of 20+
# Run from frontend-cdk directory: ./scripts/quick-update.sh

export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

REGION="us-east-1"
CLUSTER_NAME="geospatial-agent-dev"
SERVICE_NAME="geospatial-agent-dev"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cdk-hnb659fds-container-assets-${ACCOUNT_ID}-${REGION}"

TAG="update-$(date +%Y%m%d-%H%M%S)"
echo "Building with tag: $TAG"

# Login to ECR
echo "Logging into ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# Build Docker image
echo "Building Docker image..."
cd "$PROJECT_ROOT/react-ui"
docker build --platform linux/amd64 -t ${ECR_REPO}:${TAG} -f Dockerfile .

# Push to ECR
echo "Pushing to ECR..."
docker push ${ECR_REPO}:${TAG}

# Get current task definition
echo "Getting current task definition..."
TASK_DEF=$(aws ecs describe-services \
  --cluster $CLUSTER_NAME \
  --services $SERVICE_NAME \
  --region $REGION \
  --query 'services[0].taskDefinition' \
  --output text)

echo "Current task definition: $TASK_DEF"

# Create new task definition with updated image
echo "Creating new task definition..."
TASK_DEF_JSON=$(aws ecs describe-task-definition --task-definition $TASK_DEF --region $REGION)

NEW_TASK_DEF=$(echo $TASK_DEF_JSON | jq -r ".taskDefinition |
  .containerDefinitions[0].image = \"${ECR_REPO}:${TAG}\" |
  {
    family: .family,
    taskRoleArn: .taskRoleArn,
    executionRoleArn: .executionRoleArn,
    networkMode: .networkMode,
    containerDefinitions: .containerDefinitions,
    volumes: .volumes,
    placementConstraints: .placementConstraints,
    requiresCompatibilities: .requiresCompatibilities,
    cpu: .cpu,
    memory: .memory
  }")

NEW_TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json "$NEW_TASK_DEF" \
  --region $REGION \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

echo "New task definition: $NEW_TASK_DEF_ARN"

# Update service
echo "Updating ECS service..."
aws ecs update-service \
  --cluster $CLUSTER_NAME \
  --service $SERVICE_NAME \
  --task-definition $NEW_TASK_DEF_ARN \
  --force-new-deployment \
  --region $REGION \
  --query 'service.[serviceName,status,desiredCount,runningCount]' \
  --output table

echo ""
echo "Update initiated. Deployment takes ~5-8 minutes."
echo ""
echo "Monitor deployment:"
echo "  aws ecs describe-services --cluster $CLUSTER_NAME --services $SERVICE_NAME --region $REGION --query 'services[0].deployments' --output table"
echo ""
echo "View logs:"
echo "  aws logs tail /ecs/geospatial-agent-dev --follow --region $REGION"
