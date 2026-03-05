#!/bin/bash

# Invalidate CloudFront cache
# Auto-detects CloudFront domain from CloudFormation stack
# Run from frontend-cdk directory: ./scripts/fix-cloudfront-cache.sh

export AWS_PAGER=""

REGION="us-east-1"
STACK="GeospatialAgentStack"

# Get CloudFront domain from stack outputs
CF_DOMAIN=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomain`].OutputValue' --output text)

if [ -z "$CF_DOMAIN" ]; then
  echo "Could not find CloudFront domain from stack $STACK"
  exit 1
fi

echo "CloudFront domain: $CF_DOMAIN"

# Get distribution ID
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?DomainName=='$CF_DOMAIN'].Id" \
  --output text)

if [ -z "$DIST_ID" ]; then
  echo "Could not find CloudFront distribution for $CF_DOMAIN"
  exit 1
fi

echo "Distribution ID: $DIST_ID"

# Create invalidation
INVALIDATION_ID=$(aws cloudfront create-invalidation \
  --distribution-id $DIST_ID \
  --paths "/*" \
  --query 'Invalidation.Id' \
  --output text)

echo "Invalidation created: $INVALIDATION_ID"
echo ""
echo "Checking status (takes 2-3 minutes)..."

sleep 10
STATUS=$(aws cloudfront get-invalidation \
  --distribution-id $DIST_ID \
  --id $INVALIDATION_ID \
  --query 'Invalidation.Status' \
  --output text)

echo "Status: $STATUS"
echo ""
echo "Monitor invalidation:"
echo "  aws cloudfront get-invalidation --distribution-id $DIST_ID --id $INVALIDATION_ID --query 'Invalidation.Status'"
echo ""
echo "Test after completion:"
echo "  curl -I https://$CF_DOMAIN/"
