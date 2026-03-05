#!/bin/bash

# Teardown script for Geospatial Agent on AWS
# Destroys all deployed resources in the correct order

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}========================================${NC}"
echo -e "${RED}  Geospatial Agent - Full Teardown${NC}"
echo -e "${RED}========================================${NC}"
echo ""
echo "This will destroy ALL deployed resources."
read -p "Are you sure? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy]es$ ]]; then
    echo "Teardown cancelled."
    exit 0
fi

# Set variables
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=${AWS_REGION:-us-east-1}
export BUCKET_NAME="geospatial-agent-on-aws-${ACCOUNT_ID}"

echo -e "${YELLOW}Step 1/6: Destroying React UI Frontend...${NC}"
(cd frontend-cdk && npx cdk destroy -f 2>&1) || echo -e "${YELLOW}  Warning: Frontend stack may already be destroyed${NC}"
echo -e "${GREEN}Done${NC}"

echo -e "${YELLOW}Step 2/6: Destroying TiTiler...${NC}"
(cd titiler-cdk && npx cdk destroy -f 2>&1) || echo -e "${YELLOW}  Warning: TiTiler stack may already be destroyed${NC}"
echo -e "${GREEN}Done${NC}"

echo -e "${YELLOW}Step 3/6: Destroying Geo Agent...${NC}"
(cd geo_agent && echo "y" | agentcore destroy 2>&1) || echo -e "${YELLOW}  Warning: Agent may already be destroyed${NC}"
echo -e "${GREEN}Done${NC}"

echo -e "${YELLOW}Step 4/6: Deleting S3 bucket...${NC}"
if aws s3api head-bucket --bucket ${BUCKET_NAME} 2>/dev/null; then
    aws s3 rm s3://${BUCKET_NAME} --recursive
    aws s3 rb s3://${BUCKET_NAME}
    echo -e "${GREEN}  Bucket deleted: ${BUCKET_NAME}${NC}"
else
    echo -e "${YELLOW}  Bucket not found (already deleted)${NC}"
fi

echo -e "${YELLOW}Step 5/6: Cleaning up Secrets Manager...${NC}"
aws secretsmanager delete-secret --secret-id geospatial-agent/dev/admin-temp-password --force-delete-without-recovery --region ${AWS_REGION} 2>/dev/null || true
aws secretsmanager delete-secret --secret-id geospatial-agent/dev/cloudfront-custom-header --force-delete-without-recovery --region ${AWS_REGION} 2>/dev/null || true
echo -e "${GREEN}Done${NC}"

echo -e "${YELLOW}Step 6/6: Cleaning up CloudWatch log groups...${NC}"
aws logs delete-log-group --log-group-name /ecs/geospatial-agent-dev --region ${AWS_REGION} 2>/dev/null || true
aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/runtimes/geospatial_agent_on_aws --region ${AWS_REGION} 2>/dev/null || true
echo -e "${GREEN}Done${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Teardown complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Also check in AWS Console:"
echo "  - ECR: Container images in bedrock-agentcore-* and cdk-* repositories"
echo "  - CloudFormation: Any remaining stacks"
echo "  - IAM: Role agentcore-geospatial-agent-on-aws-role if deletion was blocked"
