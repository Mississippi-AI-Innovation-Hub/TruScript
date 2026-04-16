#!/usr/bin/env bash
# train-rekognition-model.sh
# Creates a Rekognition Custom Labels dataset from your S3 training images,
# trains the model, waits for completion, then prints the model ARN
# for you to put in terraform.tfvars.
#
# Usage:
#   ./scripts/train-rekognition-model.sh dev
#   ./scripts/train-rekognition-model.sh prod
#
# Prerequisites:
#   1. Run upload-ml-data.sh first — training images must be in S3
#   2. AWS CLI configured
#   3. Terraform backend applied (need project name + bucket ARN)
#
# Training takes ~30–60 minutes. The script waits and prints the ARN when done.
# Paste the ARN into terraform.tfvars as rekognition_project_version_arn.

set -euo pipefail

ENV=${1:-dev}
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infrastructure/backend"
TFVARS="$REPO_ROOT/infrastructure/environments/$ENV/terraform.tfvars"

# ── Validate ──────────────────────────────────────────────────────────────────

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "ERROR: env must be 'dev' or 'prod'. Got: $ENV"
  exit 1
fi

if [[ ! -f "$TFVARS" ]]; then
  echo "ERROR: $TFVARS not found. Copy .example → .tfvars and fill in values."
  exit 1
fi

# ── Read config from Terraform ────────────────────────────────────────────────

echo "Reading Terraform outputs..."
cd "$INFRA_DIR"
ML_BUCKET=$(terraform output -raw ml_data_bucket_name 2>/dev/null || echo "")
PROJECT_ARN=$(terraform output -raw rekognition_project_arn 2>/dev/null || echo "")
AWS_REGION=$(terraform output -raw aws_region 2>/dev/null || echo "us-east-1")
REKOGNITION_ROLE_ARN=$(terraform output -raw rekognition_s3_role_arn 2>/dev/null || echo "")

if [[ -z "$ML_BUCKET" || -z "$PROJECT_ARN" ]]; then
  echo "ERROR: Missing Terraform outputs (ml_data_bucket_name, rekognition_project_arn)."
  echo "  Run 'terraform apply' first."
  exit 1
fi

echo "Project ARN:  $PROJECT_ARN"
echo "ML bucket:    $ML_BUCKET"
echo "Region:       $AWS_REGION"
echo ""

# ── Create manifest file from S3 listing ─────────────────────────────────────
# Rekognition Custom Labels uses a manifest file in JSON Lines format.

echo "[1/5] Building training manifest from s3://$ML_BUCKET/training/..."
MANIFEST_FILE="/tmp/msbn_training_manifest.jsonl"
> "$MANIFEST_FILE"

for LABEL in legitimate fraud; do
  S3_PREFIX="training/$LABEL/"
  echo "  Processing label: $LABEL"

  # List all images under this prefix
  aws s3 ls "s3://$ML_BUCKET/$S3_PREFIX" --recursive \
    | awk '{print $4}' \
    | grep -iE '\.(png|jpg|jpeg|tiff|tif)$' \
    | while read -r KEY; do
        cat >> "$MANIFEST_FILE" <<EOF
{"source-ref":"s3://$ML_BUCKET/$KEY","class-name":"$LABEL"}
EOF
      done
done

MANIFEST_LINES=$(wc -l < "$MANIFEST_FILE" | tr -d ' ')
if [[ "$MANIFEST_LINES" -eq 0 ]]; then
  echo "ERROR: No images found in s3://$ML_BUCKET/training/."
  echo "  Run upload-ml-data.sh first."
  exit 1
fi

echo "  Manifest built: $MANIFEST_LINES images"

# Upload manifest to S3
MANIFEST_S3_KEY="rekognition-manifests/training-$(date +%Y%m%d%H%M%S).jsonl"
aws s3 cp "$MANIFEST_FILE" "s3://$ML_BUCKET/$MANIFEST_S3_KEY"
echo "  Manifest uploaded: s3://$ML_BUCKET/$MANIFEST_S3_KEY"
echo ""

# ── Create dataset ────────────────────────────────────────────────────────────

echo "[2/5] Creating Rekognition dataset..."
DATASET_ARN=$(aws rekognition create-dataset \
  --project-arn "$PROJECT_ARN" \
  --dataset-type TRAIN \
  --dataset-source "{
    \"GroundTruthManifest\": {
      \"S3Object\": {
        \"Bucket\": \"$ML_BUCKET\",
        \"Name\": \"$MANIFEST_S3_KEY\"
      }
    }
  }" \
  --region "$AWS_REGION" \
  --query "DatasetArn" \
  --output text)

echo "  Dataset ARN: $DATASET_ARN"
echo "  Waiting for dataset to be ready..."

# Wait for dataset
while true; do
  STATUS=$(aws rekognition describe-dataset \
    --dataset-arn "$DATASET_ARN" \
    --region "$AWS_REGION" \
    --query "DatasetDescription.Status" \
    --output text)
  echo "  Status: $STATUS"
  if [[ "$STATUS" == "CREATE_COMPLETE" ]]; then break; fi
  if [[ "$STATUS" == "CREATE_FAILED" ]]; then
    echo "ERROR: Dataset creation failed."
    exit 1
  fi
  sleep 15
done
echo ""

# ── Start training ────────────────────────────────────────────────────────────

VERSION_NAME="v$(date +%Y%m%d%H%M%S)"
echo "[3/5] Starting model training (version: $VERSION_NAME)..."
echo "  This typically takes 30–60 minutes."
echo ""

VERSION_ARN=$(aws rekognition create-project-version \
  --project-arn "$PROJECT_ARN" \
  --version-name "$VERSION_NAME" \
  --output-config "{
    \"S3Bucket\": \"$ML_BUCKET\",
    \"S3KeyPrefix\": \"rekognition-output/$VERSION_NAME/\"
  }" \
  --region "$AWS_REGION" \
  --query "ProjectVersionArn" \
  --output text)

echo "  Training ARN: $VERSION_ARN"
echo ""

# ── Wait for training ─────────────────────────────────────────────────────────

echo "[4/5] Waiting for training to complete..."
ELAPSED=0
while true; do
  STATUS=$(aws rekognition describe-project-versions \
    --project-arn "$PROJECT_ARN" \
    --version-names "$VERSION_NAME" \
    --region "$AWS_REGION" \
    --query "ProjectVersionDescriptions[0].Status" \
    --output text)

  echo "  [${ELAPSED}m] Status: $STATUS"

  if [[ "$STATUS" == "TRAINING_COMPLETED" ]]; then break; fi
  if [[ "$STATUS" == "TRAINING_FAILED" ]]; then
    echo "ERROR: Model training failed."
    echo "  Check the AWS Rekognition console for details."
    exit 1
  fi

  sleep 120  # check every 2 minutes
  ELAPSED=$((ELAPSED + 2))
done
echo ""

# ── Deploy (start) the model ──────────────────────────────────────────────────

echo "[5/5] Starting (deploying) the model..."
aws rekognition start-project-version \
  --project-version-arn "$VERSION_ARN" \
  --min-inference-units 1 \
  --region "$AWS_REGION"

echo "  Waiting for model to be RUNNING..."
while true; do
  STATUS=$(aws rekognition describe-project-versions \
    --project-arn "$PROJECT_ARN" \
    --version-names "$VERSION_NAME" \
    --region "$AWS_REGION" \
    --query "ProjectVersionDescriptions[0].Status" \
    --output text)

  echo "  Status: $STATUS"
  if [[ "$STATUS" == "RUNNING" ]]; then break; fi
  sleep 30
done

echo ""
echo "======================================================================"
echo "Model is RUNNING. Copy this ARN into your terraform.tfvars:"
echo ""
echo "  rekognition_project_version_arn = \"$VERSION_ARN\""
echo ""
echo "Then re-run terraform apply to wire it into the pipeline Lambda."
echo ""
echo "NOTE: The model costs ~\$4/hour while running."
echo "  Stop it when not in use:"
echo "  aws rekognition stop-project-version --project-version-arn \"$VERSION_ARN\" --region $AWS_REGION"
echo "======================================================================"
