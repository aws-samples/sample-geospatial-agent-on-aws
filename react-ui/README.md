# React UI

React + TypeScript + MapLibre GL JS frontend for the Geospatial Agent on AWS.

## Architecture

```
Frontend (React + Vite)  →  Backend (Express)  →  Bedrock AgentCore
     ↓                           ↓
  MapLibre GL              S3 Pre-signed URLs
     ↓                           ↓
  TiTiler API              Satellite Imagery (COG)
```

- **Frontend**: React + TypeScript + Vite + MapLibre GL JS
- **Backend**: Node.js + Express (SSE streaming, S3 pre-signed URLs)
- **Tile Server**: TiTiler for COG processing

## Local Development

### Prerequisites

- Node.js 20+
- Deployed Geo Agent (from `geo_agent/`)
- Deployed TiTiler (from `titiler-cdk/`)
- AWS CLI configured

### 1. Configure Backend

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env` (or use `sed` as shown in the main README's "Local Development" section):
```bash
AWS_REGION=us-east-1
AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT_ID:runtime/YOUR_AGENT_ID
S3_BUCKET_NAME=geospatial-agent-on-aws-YOUR_ACCOUNT_ID
```

### 2. Configure Frontend

```bash
cd frontend
cp .env.example .env
```

Edit `frontend/.env`:
```bash
VITE_TITILER_URL=https://xxxxx.execute-api.us-east-1.amazonaws.com/prod
VITE_TITILER_API_KEY=your_api_key_here
```

### 3. Configure S3 CORS

Required for satellite imagery to load in browser:

```bash
aws s3api put-bucket-cors \
  --bucket YOUR_S3_BUCKET_NAME \
  --cors-configuration '{
    "CORSRules": [{
      "AllowedOrigins": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["Content-Length", "Content-Range", "ETag", "Content-Type", "Accept-Ranges"],
      "MaxAgeSeconds": 3600
    }]
  }'
```

### 4. Start Services

```bash
# Terminal 1: Backend
cd backend
npm install
npm run dev  # http://localhost:3001

# Terminal 2: Frontend
cd frontend
npm install
npm run dev  # http://localhost:5173
```

Open http://localhost:5173

## Security Notes

- **AWS credentials**: Stored on backend only, never exposed to browser
- **S3 bucket**: Remains private; pre-signed URLs (1-hour expiry) used for access
- **TiTiler API key**: `VITE_TITILER_API_KEY` is used for API Gateway rate limiting only (not authorization). For production, consider adding WAF rules or a Lambda@Edge proxy to keep the key server-side.
- **Production CORS**: Replace `"AllowedOrigins": ["*"]` with your domain:
  ```json
  "AllowedOrigins": ["https://yourdomain.cloudfront.net"]
  ```

## AWS Deployment

For production deployment to AWS (ECS, CloudFront, Cognito), see [`../frontend-cdk/README.md`](../frontend-cdk/README.md).

## Project Structure

```
react-ui/
├── backend/
│   ├── src/index.ts      # Express server (SSE, pre-signed URLs)
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/   # MapView, ChatSidebar, etc.
│   │   ├── pages/        # Chat, UseCases, Technology
│   │   └── utils/        # Auth, parsing
│   └── .env.example
└── docker-compose.yml    # Local container testing
```
