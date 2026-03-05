#!/bin/bash

# Cleanup script for Geospatial Agent on AWS CDK Stack
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${RED}========================================${NC}"
echo -e "${RED}Geospatial Agent on AWS - Stack Cleanup${NC}"
echo -e "${RED}========================================${NC}"
echo ""
echo -e "${YELLOW}⚠  WARNING: This will destroy all resources!${NC}"
echo ""
echo "The following will be deleted:"
echo "  • ECS Cluster and Services"
echo "  • Application Load Balancer"
echo "  • CloudFront Distribution"
echo "  • Cognito User Pool"
echo "  • VPC and Networking"
echo "  • CloudWatch Log Groups"
echo ""
echo "The following will NOT be deleted:"
echo "  • S3 Bucket (satellite data)"
echo "  • Bedrock AgentCore Runtime"
echo "  • TiTiler Stack (if deployed separately)"
echo ""

# Confirm destruction
read -p "Are you absolutely sure? Type 'delete' to confirm: " -r
echo
if [[ ! $REPLY == "delete" ]]; then
    echo "Cleanup cancelled"
    exit 0
fi

echo -e "${YELLOW}Destroying stack...${NC}"
echo "This may take 5-10 minutes..."
echo ""

npx cdk destroy --force

echo ""
echo -e "${GREEN}✓ Stack destroyed successfully${NC}"
echo ""
