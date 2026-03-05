#!/bin/bash

# Diagnostic Script - Check ECS, ALB, CloudFront, and logs
# Run from frontend-cdk directory: ./scripts/diagnose.sh

export AWS_PAGER=""

REGION="us-east-1"
CLUSTER="geospatial-agent-dev"
SERVICE="geospatial-agent-dev"
STACK="GeospatialAgentStack"

echo "DIAGNOSTIC CHECK"
echo "================================"
echo ""

# 1. ECS Service Status
echo "1. ECS Service Status:"
aws ecs describe-services \
  --cluster $CLUSTER \
  --services $SERVICE \
  --region $REGION \
  --query 'services[0].[serviceName,status,runningCount,desiredCount,deployments[0].status]' \
  --output table 2>&1
echo ""

# 2. Task Health
echo "2. Task Health:"
TASK_ARNS=$(aws ecs list-tasks --cluster $CLUSTER --service $SERVICE --region $REGION --query 'taskArns' --output text 2>/dev/null)
if [ -z "$TASK_ARNS" ]; then
  echo "No running tasks found"
else
  for TASK_ARN in $TASK_ARNS; do
    aws ecs describe-tasks --cluster $CLUSTER --tasks $TASK_ARN --region $REGION \
      --query 'tasks[0].[lastStatus,healthStatus,containers[0].lastStatus]' \
      --output table 2>&1
  done
fi
echo ""

# 3. Environment Variables in Task Definition
echo "3. Environment Variables:"
TASK_DEF=$(aws ecs describe-services --cluster $CLUSTER --services $SERVICE --region $REGION --query 'services[0].taskDefinition' --output text 2>/dev/null)
if [ -n "$TASK_DEF" ] && [ "$TASK_DEF" != "None" ]; then
  aws ecs describe-task-definition --task-definition $TASK_DEF --region $REGION \
    --query 'taskDefinition.containerDefinitions[0].environment[?name==`NODE_ENV` || name==`PORT` || name==`COGNITO_USER_POOL_ID` || name==`COGNITO_CLIENT_ID`]' \
    --output table 2>&1
else
  echo "No task definition found"
fi
echo ""

# 4. Docker Image
echo "4. Docker Image:"
if [ -n "$TASK_DEF" ] && [ "$TASK_DEF" != "None" ]; then
  aws ecs describe-task-definition --task-definition $TASK_DEF --region $REGION \
    --query 'taskDefinition.containerDefinitions[0].image' \
    --output text 2>&1
else
  echo "No task definition found"
fi
echo ""

# 5. Recent Logs
echo "5. Recent Logs (last 20 lines):"
aws logs tail /ecs/geospatial-agent-dev --region $REGION --since 5m 2>&1 | tail -20
echo ""

# 6. ALB Health Check
echo "6. ALB Health Check:"
ALB_URL=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDNS`].OutputValue' --output text 2>/dev/null)
if [ -z "$ALB_URL" ] || [ "$ALB_URL" = "None" ]; then
  echo "Could not find ALB URL from stack $STACK (stack may not exist)"
else
  echo "ALB URL: http://$ALB_URL"
  curl -s --max-time 5 "http://$ALB_URL/health" | jq . 2>/dev/null || echo "Health endpoint not responding or not JSON"
fi
echo ""

# 7. ALB Root Path
echo "7. ALB Root Path:"
if [ -n "$ALB_URL" ] && [ "$ALB_URL" != "None" ]; then
  RESPONSE=$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" "http://$ALB_URL/")
  if [ "$RESPONSE" = "200" ]; then
    echo "Returns 200 OK"
  else
    echo "Returns $RESPONSE"
  fi
else
  echo "Skipped (no ALB URL)"
fi
echo ""

# 8. CloudFront
echo "8. CloudFront Status:"
CF_DOMAIN=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontDomain`].OutputValue' --output text 2>/dev/null)
if [ -z "$CF_DOMAIN" ] || [ "$CF_DOMAIN" = "None" ]; then
  echo "Could not find CloudFront domain from stack $STACK (stack may not exist)"
else
  echo "CloudFront URL: https://$CF_DOMAIN"
  CF_RESPONSE=$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" "https://$CF_DOMAIN/")
  echo "CloudFront response code: $CF_RESPONSE"
fi
echo ""

# 9. Target Group Health
echo "9. Target Group Health:"
TG_ARN=$(aws elbv2 describe-target-groups --region $REGION \
  --query "TargetGroups[?contains(LoadBalancerArns[0] || '', 'Agenti-Farga')].TargetGroupArn" \
  --output text 2>/dev/null | head -1)
if [ -n "$TG_ARN" ] && [ "$TG_ARN" != "None" ]; then
  aws elbv2 describe-target-health --target-group-arn $TG_ARN --region $REGION \
    --query 'TargetHealthDescriptions[*].[Target.Id,TargetHealth.State,TargetHealth.Reason]' \
    --output table 2>&1
else
  echo "Could not find target group"
fi
echo ""

echo "================================"
echo "Diagnostic complete"
