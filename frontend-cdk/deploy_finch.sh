#!/bin/bash

# Deployment script for Geospatial Agent on AWS CDK Stack (using Finch)
# Usage: ./deploy_finch.sh [-y|--auto-approve]
set -e

export CDK_DOCKER=finch

# Parse arguments
AUTO_APPROVE=false
for arg in "$@"; do
  case $arg in
    -y|--auto-approve)
      AUTO_APPROVE=true
      shift
      ;;
  esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Geospatial Agent on AWS - CDK Deployment${NC}"
echo -e "${GREEN}(Using Finch)${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if .env file exists
if [ -f .env ]; then
    echo -e "${GREEN}✓${NC} Loading environment variables from .env"
    export $(cat .env | grep -v '^#' | xargs)
else
    echo -e "${YELLOW}⚠${NC} No .env file found. Using environment variables or context."
fi

# Validate required environment variables
if [ -z "$AGENT_RUNTIME_ARN" ]; then
    echo -e "${RED}✗${NC} AGENT_RUNTIME_ARN is not set"
    echo "   Please set it in .env file or environment"
    exit 1
fi

if [ -z "$S3_BUCKET_NAME" ]; then
    echo -e "${RED}✗${NC} S3_BUCKET_NAME is not set"
    echo "   Please set it in .env file or environment"
    exit 1
fi

echo -e "${GREEN}✓${NC} Configuration validated"
echo ""
echo "   Agent Runtime ARN: ${AGENT_RUNTIME_ARN}"
echo "   S3 Bucket Name: ${S3_BUCKET_NAME}"
echo "   AWS Region: ${AWS_REGION:-us-east-1}"
echo ""

# Check if Finch is installed and running
if ! command -v finch &> /dev/null; then
    echo -e "${RED}✗${NC} Finch is not installed"
    echo "   Please install Finch: https://github.com/runfinch/finch"
    exit 1
fi

if ! finch info > /dev/null 2>&1; then
    echo -e "${RED}✗${NC} Finch is not running"
    echo "   Please start Finch VM with 'finch vm start' and try again"
    exit 1
fi
echo -e "${GREEN}✓${NC} Finch is running"

# Check if AWS credentials are configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo -e "${RED}✗${NC} AWS credentials are not configured"
    echo "   Please run 'aws configure' or set AWS_PROFILE"
    exit 1
fi
echo -e "${GREEN}✓${NC} AWS credentials configured"
echo ""

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    npm install
    echo -e "${GREEN}✓${NC} Dependencies installed"
    echo ""
fi

# Build TypeScript
echo -e "${YELLOW}Building CDK stack...${NC}"
npm run build
echo -e "${GREEN}✓${NC} CDK stack built"
echo ""

# Synthesize CloudFormation template
echo -e "${YELLOW}Synthesizing CloudFormation template...${NC}"
npx cdk synth
echo -e "${GREEN}✓${NC} Template synthesized"
echo ""

# Show diff (optional)
echo -e "${YELLOW}Checking for changes...${NC}"
npx cdk diff || true
echo ""

# Confirm deployment (unless auto-approve)
if [ "$AUTO_APPROVE" = false ]; then
    read -p "Do you want to deploy? (yes/no): " -r
    echo
    if [[ ! $REPLY =~ ^[Yy]es$ ]]; then
        echo "Deployment cancelled"
        exit 0
    fi
fi

# Deploy
echo -e "${YELLOW}Deploying stack...${NC}"
echo "This may take 10-15 minutes..."
echo ""

npx cdk deploy \
    -c agentRuntimeArn="$AGENT_RUNTIME_ARN" \
    -c s3BucketName="$S3_BUCKET_NAME" \
    -c awsRegion="${AWS_REGION:-us-east-1}" \
    --require-approval never

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Deployment completed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "To view your application:"
echo "1. Check the 'ApplicationURL' output above"
echo "2. Access it in your browser"
echo ""
echo "To view logs:"
echo "  aws logs tail /ecs/geospatial-agent-dev --follow"
echo ""
