#!/usr/bin/env bash
# upload-ml-data.sh
# Uploads transcript images (training/test data) to the ML data S3 bucket.
#
# Usage:
#   ./scripts/upload-ml-data.sh dev
#   ./scripts/upload-ml-data.sh prod
#
# Expected local folder structure (place your images here before running):
#   ml-data/
#   ├── training/
#   │   ├── legitimate/   ← real, confirmed-legitimate transcript images
#   │   └── fraud/        ← confirmed-fraud transcript images
#   └── test/
#       ├── legitimate/
#       └── fraud/
#
# Supported image formats: PNG, JPEG, TIFF (for Rekognition and Textract)
#
# Requirements:
#   - AWS CLI configured
#   - terraform output available (infrastructure/backend must be applied)

set -euo pipefail

ENV=${1:-dev}
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infrastructure/backend"
LOCAL_DATA_DIR="$REPO_ROOT/ml-data"

# ── Validate ──────────────────────────────────────────────────────────────────

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "ERROR: env must be 'dev' or 'prod'. Got: $ENV"
  exit 1
fi

if [[ ! -d "$LOCAL_DATA_DIR" ]]; then
  echo "ERROR: ml-data/ directory not found at: $LOCAL_DATA_DIR"
  echo ""
  echo "Create the following structure and add your transcript images:"
  echo "  ml-data/"
  echo "  ├── training/"
  echo "  │   ├── legitimate/   ← paste legitimate transcript images here"
  echo "  │   └── fraud/        ← paste confirmed fraud images here"
  echo "  └── test/"
  echo "      ├── legitimate/"
  echo "      └── fraud/"
  exit 1
fi

# ── Get bucket name from Terraform ───────────────────────────────────────────

echo "Reading Terraform outputs..."
cd "$INFRA_DIR"
ML_BUCKET=$(terraform output -raw ml_data_bucket_name 2>/dev/null || echo "")

if [[ -z "$ML_BUCKET" ]]; then
  echo "ERROR: Could not read ml_data_bucket_name from Terraform outputs."
  echo "  Run 'terraform apply -var-file=\"../environments/$ENV/terraform.tfvars\"' first."
  exit 1
fi

echo "ML data bucket: $ML_BUCKET"
echo ""

# ── Upload training data ──────────────────────────────────────────────────────

TOTAL_UPLOADED=0

for SPLIT in training test; do
  for LABEL in legitimate fraud; do
    LOCAL_PATH="$LOCAL_DATA_DIR/$SPLIT/$LABEL"
    S3_PATH="s3://$ML_BUCKET/$SPLIT/$LABEL/"

    if [[ ! -d "$LOCAL_PATH" ]]; then
      echo "[SKIP] $LOCAL_PATH does not exist"
      continue
    fi

    FILE_COUNT=$(find "$LOCAL_PATH" -type f \( -iname "*.png" -o -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.tiff" -o -iname "*.tif" \) | wc -l | tr -d ' ')

    if [[ "$FILE_COUNT" -eq 0 ]]; then
      echo "[SKIP] $LOCAL_PATH is empty — add images first"
      continue
    fi

    echo "Uploading $SPLIT/$LABEL ($FILE_COUNT images)..."
    aws s3 sync "$LOCAL_PATH" "$S3_PATH" \
      --include "*.png" \
      --include "*.jpg" \
      --include "*.jpeg" \
      --include "*.tiff" \
      --include "*.tif" \
      --exclude ".*" \
      --storage-class STANDARD_IA

    TOTAL_UPLOADED=$((TOTAL_UPLOADED + FILE_COUNT))
    echo "  Uploaded $FILE_COUNT images → $S3_PATH"
  done
done

# ── Upload rules JSON ─────────────────────────────────────────────────────────

echo ""
echo "Uploading fraud rules..."
aws s3 cp "$REPO_ROOT/infrastructure/lambdas/rules/fraud_rules.json" \
  "s3://$ML_BUCKET/rules/fraud_rules.json" \
  --content-type "application/json"
echo "  Rules uploaded → s3://$ML_BUCKET/rules/fraud_rules.json"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "Done. Uploaded $TOTAL_UPLOADED images to s3://$ML_BUCKET"
echo ""
echo "Next step — train the Rekognition Custom Labels model:"
echo "  ./scripts/train-rekognition-model.sh $ENV"
