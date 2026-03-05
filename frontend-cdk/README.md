# React UI AWS Deployment (CDK)

Deploy the React UI to AWS using ECS Fargate with CloudFront, ALB, and Cognito authentication.

## 🏗️ Architecture

```
┌─────────────────┐
│   CloudFront    │  ← HTTPS, Global CDN, Custom Header
└────────┬────────┘
         │
┌────────▼────────┐
│   ALB (HTTP)    │  ← Load Balancer, Header Validation (403 for direct access)
└────────┬────────┘
         │
┌────────▼────────┐
│  ECS Fargate    │  ← Containers (React + Express)
│  - Frontend     │     PORT 3001
│  - Backend API  │     NODE_ENV=production
└────────┬────────┘
         │
┌────────▼────────────────────────┐
│  Backend API Routes             │
│  - /health (public)             │
│  - /api/agent/invoke (auth)     │
│  - /api/scenario/* (auth)       │
│  - /api/presigned-url (auth)    │
│  - /api/geometry (auth)         │
└─────────────────────────────────┘
         │
┌────────▼────────┐
│ Cognito (Auth)  │  ← JWT validation
└─────────────────┘
```

## 📋 Prerequisites

1. **AWS CLI configured** with appropriate credentials
2. **Docker running** (for building container images)
3. **Node.js 20+** installed (`nvm use 20`)
4. **CDK bootstrapped** (see main README "Initial Setup")

## ⚙️ Configuration

**1. Configure environment variables (`.env`):**

```bash
cp .env.example .env
```

Edit `.env`:
```bash
# Required: Get from AgentCore deployment (.bedrock_agentcore.yaml)
AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT_ID:runtime/YOUR_AGENT_ID

# Required: Same bucket created in Part 1
S3_BUCKET_NAME=geospatial-agent-on-aws-YOUR_ACCOUNT_ID

# Required: Admin user email (receives temp password)
ADMIN_EMAIL=your-email@example.com

# Optional: AWS Region (defaults to us-east-1)
AWS_REGION=us-east-1
```

**2. Configure TiTiler (required for satellite imagery):**

> **Note:** If you followed the main README deployment guide, `react-ui/frontend/.env` was already auto-populated in Part 2 (TiTiler). If not, see `titiler-cdk/README.md` for manual setup.

**3. Install CDK dependencies:**

```bash
npm install
```

## 🚀 Deploy

```bash
# Deploy with auto-approval (no prompts)
./deploy.sh -y

# Or deploy with manual confirmation
./deploy.sh
```

**What gets deployed:**
- **VPC**: New VPC with public/private subnets across 2 AZs
- **ECS Fargate**: Serverless container orchestration (2 tasks, auto-scaling 1-10)
- **Application Load Balancer**: HTTP traffic distribution with health checks
- **CloudFront**: Global CDN with HTTPS and caching
- **Cognito User Pool**: Admin-only authentication (no self-signup)
- **Container**: Multi-stage Docker build (React frontend + Express backend)
- **CloudWatch Logs**: Application logs retention (7 days)

**Deployment time:** ~7 minutes

## 🔑 Post-Deployment

**1. Get application URL:**

```bash
# Application URL (use this to access the app)
https://YOUR_DISTRIBUTION_ID.cloudfront.net

# Or via CloudFormation:
aws cloudformation describe-stacks \
  --stack-name GeospatialAgentStack \
  --query 'Stacks[0].Outputs[?OutputKey==`ApplicationURL`].OutputValue' \
  --output text
```

**2. First login:**
1. Open CloudFront URL in browser
2. Check your email (`ADMIN_EMAIL`) for the temporary password from Cognito
3. Login with your email and temporary password
4. You'll be prompted to change password on first login
5. Set a new secure password (min 12 chars, mixed case, numbers, symbols)

## 👥 Managing Users

**Add new users:**

1. Go to AWS Console → Cognito User Pools
2. Find pool: `geospatial-agent-dev`
3. Create user → Enter email
4. User receives temporary password via email
5. User changes password on first login

**Remove users:**

```bash
aws cognito-idp admin-delete-user \
  --user-pool-id us-east-1_XXXXXXXXX \
  --username user@example.com
```

**Reset user password:**

```bash
aws cognito-idp admin-set-user-password \
  --user-pool-id us-east-1_XXXXXXXXX \
  --username user@example.com \
  --password "TempPassword123!" \
  --permanent false
```

**Note:** Tokens expire after 1 hour of inactivity. Users are automatically redirected to login.

## 🔍 Monitoring & Troubleshooting

**View logs:**
```bash
# Live tail
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1

# Filter for errors
aws logs tail /ecs/geospatial-agent-dev --follow --region us-east-1 | grep -i error
```

**Common Issues:**

| Issue | Solution |
|-------|----------|
| Login screen doesn't appear | Clear browser cache/localStorage, hard refresh (Cmd+Shift+R) |
| "Cannot GET /" error | CloudFront cached old error - run `./scripts/fix-cloudfront-cache.sh` |
| API calls fail with 401 | JWT token expired - logout and login again |
| "Not authorized to invoke AgentCore" | IAM policy issue - redeploy CDK to fix permissions |
| Container not starting | Check logs: `aws logs tail /ecs/geospatial-agent-dev --follow` |

**Debug scripts:**

```bash
# Complete diagnostic check
./scripts/diagnose.sh

# Clear CloudFront cache
./scripts/fix-cloudfront-cache.sh
```

See [`scripts/README.md`](scripts/README.md) for full documentation and manual alternatives.

## ⚡ Fast Updates (Skip Full Deploy)

For code changes without infrastructure updates:

```bash
# Quick update (~8-10 mins, no CloudFormation)
./scripts/quick-update.sh
```

**What it does:**
- Builds Docker image with unique timestamp tag
- Pushes to ECR
- Updates ECS task definition
- Forces service redeployment

## 🧹 Teardown

**Delete all resources:**

```bash
cdk destroy

# Confirm deletion when prompted
```

**Manual cleanup (if needed):**
- CloudWatch log groups (`/ecs/geospatial-agent-dev`)
- ECR images in container assets repository

## 📁 Project Structure

```
cdk-stack/
├── lib/
│   └── geospatial-agent-stack.ts      # Main CDK infrastructure
├── bin/
│   └── app.ts                         # CDK app entry point
├── scripts/                            # Essential utility scripts
│   ├── quick-update.sh                # Fast deployment (~8-10 mins)
│   ├── diagnose.sh                    # Complete diagnostic check
│   ├── fix-cloudfront-cache.sh        # Cache invalidation
│   └── README.md                      # Script documentation
├── deploy.sh                          # Main deployment script
├── .env.example                       # Environment template
└── README.md                          # This file
```

## 📚 Additional Resources

- **Main Project README**: [`../README.md`](../README.md)
- **Deployment Scripts**: [`scripts/README.md`](scripts/README.md)
- **React UI Details**: [`../react-ui/README.md`](../react-ui/README.md)
