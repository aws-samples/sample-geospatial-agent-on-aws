# TiTiler CDK Deployment

Deploys TiTiler Lambda + API Gateway with API Key authentication.

## What Gets Deployed

- **TiTiler v0.24.2** - COG tile server
- **AWS Lambda** - Serverless compute with Docker (3008 MB, 120s timeout)
- **Lambda Web Adapter v0.8.3** - Bridges web server to Lambda
- **API Gateway REST API** - With CORS and binary image support
- **API Key + Usage Plan** - Rate limiting: 100 req/sec, 20K/day quota

## Prerequisites

- **AWS CLI** configured with credentials
- **Docker** installed and running
- **Node.js 20+** and npm

**Note for Apple Silicon (M1/M2/M3) Users:**
The deployment automatically builds for x86_64 (linux/amd64) platform, even on ARM-based Macs. This ensures compatibility with Lambda's x86_64 architecture. CDK handles the cross-platform build automatically via Docker's `--platform` flag.

## Quick Deploy (3 Commands)

```bash
cd titiler-cdk

# 1. Install dependencies
npm install

# 2. Bootstrap CDK (only needed once per account/region, see main README)
npx cdk bootstrap aws://YOUR_ACCOUNT_ID/us-east-1

# 3. Deploy
npx cdk deploy
```

## What Gets Created

- ✅ Lambda function (CDK builds and pushes Docker image automatically)
- ✅ API Gateway REST API with CORS and binary support
- ✅ API Key with usage plan (100 req/sec, 20K/day quota)
- ✅ IAM permissions for S3 read access
- ✅ CloudWatch logs with Lambda insights

## After Deployment

**1. Get your API URL** (printed in outputs):
```
TitilerStack.ApiUrl = https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/
```

**2. Get your API Key** (run the command from outputs):
```bash
aws apigateway get-api-key --api-key <KEY_ID> --include-value --query 'value' --output text
```

**3. Test it:**
```bash
# With API key (should work)
curl -H "x-api-key: YOUR_KEY" https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/healthz

# Without API key (should fail with 403)
curl https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/healthz
```

## Update React Frontend

If you followed the main README, `react-ui/frontend/.env` was auto-populated in Part 2. Otherwise, create or edit `react-ui/frontend/.env`:

```bash
VITE_TITILER_URL=https://xxxxx.execute-api.us-east-1.amazonaws.com/prod/
VITE_TITILER_API_KEY=YOUR_API_KEY
```

The frontend automatically reads these environment variables. The API key is added to tile requests via MapLibre's `transformRequest`:

```typescript
// How the frontend uses these values (already implemented)
transformRequest: (url, resourceType) => {
  if (url.startsWith(TITILER_URL)) {
    return {
      url: url,
      headers: { 'x-api-key': TITILER_API_KEY }
    }
  }
}
```

## Cleanup

```bash
npx cdk destroy
```
