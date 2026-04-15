#!/usr/bin/env bash
# deploy-frontend.sh
# Builds the React app and deploys it to S3, then invalidates CloudFront.
#
# Usage:
#   ./scripts/deploy-frontend.sh dev
#   ./scripts/deploy-frontend.sh prod
#
# Requirements:
#   - AWS CLI configured (AWS_PROFILE or AWS_ACCESS_KEY_ID set)
#   - terraform installed
#   - node + npm installed

set -euo pipefail

ENV=${1:-dev}
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/apps/frontend"
INFRA_DIR="$REPO_ROOT/infrastructure/frontend"
TFVARS="$REPO_ROOT/infrastructure/environments/$ENV/terraform.tfvars"

# ── Validate ──────────────────────────────────────────────────────────────────

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "ERROR: env must be 'dev' or 'prod'. Got: $ENV"
  exit 1
fi

if [[ ! -f "$TFVARS" ]]; then
  echo "ERROR: $TFVARS not found."
  echo "  Copy terraform.tfvars.example → terraform.tfvars and fill in values."
  exit 1
fi

echo "Deploying frontend → $ENV"
echo ""

# ── Step 1: Get Terraform outputs ────────────────────────────────────────────

echo "[1/4] Reading Terraform outputs..."
cd "$INFRA_DIR"
BUCKET=$(terraform output -raw s3_bucket_name 2>/dev/null || echo "")
DISTRIBUTION_ID=$(terraform output -raw cloudfront_distribution_id 2>/dev/null || echo "")

if [[ -z "$BUCKET" || -z "$DISTRIBUTION_ID" ]]; then
  echo "ERROR: Could not read Terraform outputs."
  echo "  Run 'terraform apply -var-file=\"../environments/$ENV/terraform.tfvars\"' first."
  exit 1
fi

echo "  S3 bucket:        $BUCKET"
echo "  CloudFront dist:  $DISTRIBUTION_ID"
echo ""

# ── Step 2: Build React app ───────────────────────────────────────────────────

echo "[2/4] Building React app..."
cd "$FRONTEND_DIR"
npm install --silent
npm run build
echo "  Build complete → dist/"
echo ""

# ── Step 3: Upload to S3 ──────────────────────────────────────────────────────

echo "[3/4] Uploading to S3..."

# All assets except index.html — long cache (hashed filenames are safe)
aws s3 sync "$FRONTEND_DIR/dist/" "s3://$BUCKET" \
  --delete \
  --exclude "index.html" \
  --cache-control "public, max-age=31536000, immutable" \
  --quiet

# index.html — no cache so users always get the latest version
aws s3 cp "$FRONTEND_DIR/dist/index.html" "s3://$BUCKET/index.html" \
  --cache-control "no-cache, no-store, must-revalidate" \
  --content-type "text/html"

echo "  Upload complete"
echo ""

# ── Step 4: Invalidate CloudFront ────────────────────────────────────────────

echo "[4/4] Invalidating CloudFront cache..."
INVALIDATION_ID=$(aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" \
  --paths "/*" \
  --query "Invalidation.Id" \
  --output text)

echo "  Invalidation created: $INVALIDATION_ID"
echo "  (propagates in ~30-60 seconds)"
echo ""

echo "Done. Frontend deployed to:"
DOMAIN=$(cd "$INFRA_DIR" && terraform output -raw cloudfront_domain_name 2>/dev/null || echo "check terraform output")
echo "  https://$DOMAIN"
