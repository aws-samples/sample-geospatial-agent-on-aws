# Deployment Scripts

Essential utility scripts for AWS deployment management.

## Scripts

### 1. quick-update.sh

Fast deployment for code changes without CloudFormation updates.

**Usage:**
```bash
./scripts/quick-update.sh
```

**What it does:**
- Builds Docker image with timestamp tag
- Pushes to ECR
- Updates ECS task definition
- Forces service redeployment

**Time:** ~8-10 minutes (vs 15-20 for full CDK deploy)

**When to use:**
- Code changes in React UI
- Backend API changes
- Frontend UI updates
- No infrastructure changes needed

---

### 2. diagnose.sh

Complete diagnostic check for the deployed stack.

**Usage:**
```bash
./scripts/diagnose.sh
```

**What it checks:**
- ECS service status and health
- Running tasks count
- Container environment variables
- Docker image details
- Recent CloudWatch logs (last 50 lines)
- ALB target group health
- CloudFront distribution status

**When to use:**
- Deployment issues
- Container not starting
- Health check failures
- General troubleshooting

---

### 3. fix-cloudfront-cache.sh

Invalidates CloudFront cache to force fresh content delivery.

**Usage:**
```bash
./scripts/fix-cloudfront-cache.sh
```

**What it does:**
- Gets CloudFront distribution ID from CloudFormation stack
- Creates invalidation for `/*` (all paths)
- Waits for invalidation to complete

**When to use:**
- "Cannot GET /" error after deployment
- Old cached content being served
- Static files not updating
- Login screen doesn't appear

**Note:** CloudFront invalidations can take 5-15 minutes to complete.

---

## Common Troubleshooting Workflows

### After Deployment - App Not Loading

```bash
# 1. Check if services are healthy
./scripts/diagnose.sh

# 2. Clear CloudFront cache
./scripts/fix-cloudfront-cache.sh

# 3. Check logs for errors
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1 | grep -i error
```

### Code Change - Fast Update

```bash
# Make your code changes, then:
./scripts/quick-update.sh

# Wait 8-10 minutes, then clear cache if needed:
./scripts/fix-cloudfront-cache.sh
```

### Container Issues

```bash
# 1. Run diagnostics
./scripts/diagnose.sh

# 2. Check full logs
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1

# 3. Restart unhealthy tasks (manual)
aws ecs list-tasks --cluster geospatial-agent-dev --service geospatial-agent-dev --region us-east-1
aws ecs stop-task --cluster geospatial-agent-dev --task <TASK_ARN> --region us-east-1
```

## Manual Alternative Commands

If scripts don't work, here are the manual commands:

### Test ALB Directly (Bypass CloudFront)

```bash
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name GeospatialAgentStack \
  --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDNS`].OutputValue' \
  --output text \
  --region us-east-1)

# Should return 403 (custom header missing)
curl -I "http://${ALB_DNS}"

# Health endpoint should work
curl "http://${ALB_DNS}/health"
```

### View Logs

```bash
# Live tail
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1

# Last 10 minutes
aws logs tail /ecs/geospatial-agent-dev --since 10m --region us-east-1

# Filter errors
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1 | grep -i error
```

### Check ECS Service

```bash
# Service status
aws ecs describe-services \
  --cluster geospatial-agent-dev \
  --services geospatial-agent-dev \
  --region us-east-1 \
  --query 'services[0].[serviceName,status,runningCount,desiredCount]' \
  --output table

# Task health
aws ecs list-tasks \
  --cluster geospatial-agent-dev \
  --service geospatial-agent-dev \
  --region us-east-1
```

## Prerequisites

All scripts require:
- AWS CLI configured with appropriate credentials
- Deployed CloudFormation stack: `GeospatialAgentStack`
- Region: `us-east-1` (or set `AWS_REGION` environment variable)

## Notes

- Scripts use CloudFormation outputs to get resource names/IDs dynamically
- No hardcoded ARNs or IDs needed
- Safe to run multiple times (idempotent where possible)
- Scripts exit with error code 1 on failure for CI/CD integration
