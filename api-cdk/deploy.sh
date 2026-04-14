#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# deploy.sh — One-command deployment for the Geospatial Agent API
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STACK_NAME="GeospatialAgentApiStack"

# ── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Prerequisite checks ──────────────────────────────────────
info "Checking prerequisites..."

if ! command -v node &>/dev/null; then
  err "Node.js is not installed. Please install Node.js 18+ and try again."
  exit 1
fi
ok "Node.js $(node --version)"

if ! command -v aws &>/dev/null; then
  err "AWS CLI is not installed. Please install the AWS CLI and try again."
  exit 1
fi
ok "AWS CLI $(aws --version 2>&1 | head -1)"

if ! command -v npx &>/dev/null; then
  err "npx is not available. Please install Node.js 18+ (includes npm/npx)."
  exit 1
fi
ok "npx available"

# ── .env file ─────────────────────────────────────────────────
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    warn ".env file not found — copying from .env.example"
    cp .env.example .env
    err "Please edit .env and fill in the required values, then re-run this script."
    exit 1
  else
    err ".env file not found and no .env.example to copy from."
    exit 1
  fi
fi

info "Loading .env file..."
# shellcheck disable=SC2046
export $(grep -v '^\s*#' .env | grep -v '^\s*$' | xargs)

# ── Validate required env vars ────────────────────────────────
MISSING=0

if [ -z "${AGENT_RUNTIME_ARN:-}" ] || [[ "$AGENT_RUNTIME_ARN" == *"YOUR_"* ]]; then
  err "AGENT_RUNTIME_ARN is not set or still contains placeholder values. Update .env."
  MISSING=1
fi

if [ -z "${S3_BUCKET_NAME:-}" ] || [[ "$S3_BUCKET_NAME" == *"YOUR_"* ]]; then
  err "S3_BUCKET_NAME is not set or still contains placeholder values. Update .env."
  MISSING=1
fi

if [ "$MISSING" -eq 1 ]; then
  exit 1
fi

ok "AGENT_RUNTIME_ARN = ${AGENT_RUNTIME_ARN}"
ok "S3_BUCKET_NAME    = ${S3_BUCKET_NAME}"
ok "AWS_REGION        = ${AWS_REGION:-eu-central-1}"

# ── Install dependencies ──────────────────────────────────────
if [ ! -d node_modules ]; then
  info "Installing npm dependencies..."
  npm install
else
  ok "node_modules already present"
fi

# ── CDK Bootstrap (first-time setup) ─────────────────────────
info "Ensuring CDK is bootstrapped..."
npx cdk bootstrap 2>/dev/null || {
  warn "CDK bootstrap may have already been run or encountered a non-fatal issue — continuing."
}

# ── Deploy ────────────────────────────────────────────────────
info "Deploying ${STACK_NAME}..."
npx cdk deploy --require-approval never

# ── Print outputs ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Deployment complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""

API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text 2>/dev/null || echo "")

API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [ -n "$API_URL" ]; then
  echo -e "  ${CYAN}API URL:${NC}    $API_URL"
fi
if [ -n "$API_KEY_ID" ]; then
  echo -e "  ${CYAN}API Key ID:${NC} $API_KEY_ID"
fi

echo ""
echo -e "${YELLOW}To retrieve your API key value, run:${NC}"
if [ -n "$API_KEY_ID" ]; then
  echo -e "  aws apigateway get-api-key --api-key ${API_KEY_ID} --include-value --query 'value' --output text"
else
  echo -e "  aws apigateway get-api-key --api-key <API_KEY_ID> --include-value --query 'value' --output text"
fi

echo ""
echo -e "${YELLOW}Test the API:${NC}"
if [ -n "$API_URL" ]; then
  echo -e "  curl -s -H 'x-api-key: <YOUR_API_KEY>' ${API_URL}capabilities | jq ."
else
  echo -e "  curl -s -H 'x-api-key: <YOUR_API_KEY>' <API_URL>/capabilities | jq ."
fi
echo ""
