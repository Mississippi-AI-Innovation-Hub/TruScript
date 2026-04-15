# ── ML Infrastructure ─────────────────────────────────────────────────────────
# Separate file for all ML-related resources so they can be reviewed
# independently from the core backend infrastructure.

# ── S3 — ML Data Bucket ───────────────────────────────────────────────────────
# Holds: training images, test images, fraud_rules.json, result audit trail
#
# Expected structure after you upload data:
#   training/legitimate/   ← real legitimate transcripts (labeled)
#   training/fraud/        ← confirmed fraud transcripts (labeled)
#   test/legitimate/
#   test/fraud/
#   rules/fraud_rules.json ← loaded by pipeline Lambda at runtime
#   results/{id}/result.json ← audit trail of every ML run

resource "aws_s3_bucket" "ml_data" {
  bucket = "${local.name_prefix}-ml-data-${var.aws_account_id}"
  tags   = merge(local.common_tags, { Purpose = "ml-training-and-inference" })
}

resource "aws_s3_bucket_versioning" "ml_data" {
  bucket = aws_s3_bucket.ml_data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ml_data" {
  bucket = aws_s3_bucket.ml_data.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "ml_data" {
  bucket                  = aws_s3_bucket.ml_data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload the fraud rules JSON immediately when infra is provisioned
resource "aws_s3_object" "fraud_rules" {
  bucket       = aws_s3_bucket.ml_data.id
  key          = "rules/fraud_rules.json"
  source       = "${path.root}/../lambdas/rules/fraud_rules.json"
  content_type = "application/json"
  etag         = filemd5("${path.root}/../lambdas/rules/fraud_rules.json")
  tags         = local.common_tags
}

# ── Rekognition Custom Labels Project ─────────────────────────────────────────
# NOTE: Managed outside Terraform due to a provider bug (auto_update field
# returned as unknown after apply for CUSTOM_LABELS projects).
#
# The project was created via CLI. To recreate manually:
#   aws rekognition create-project \
#     --project-name msbn-dev-transcript-fraud \
#     --feature CUSTOM_LABELS \
#     --profile msaih_IsbAdminsPS
#
# After training, put the deployed model ARN in terraform.tfvars as
# rekognition_project_version_arn.

# ── IAM — Rekognition access to ML data bucket ────────────────────────────────

resource "aws_iam_role" "rekognition_s3" {
  name = "${local.name_prefix}-rekognition-s3-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "rekognition.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "rekognition_s3" {
  name = "${local.name_prefix}-rekognition-s3-policy"
  role = aws_iam_role.rekognition_s3.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.ml_data.arn,
          "${aws_s3_bucket.ml_data.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.ml_data.arn}/rekognition-output/*"
      },
    ]
  })
}

# ── Lambda — Pipeline Orchestrator ───────────────────────────────────────────

module "pipeline_orchestrator_lambda" {
  source = "../modules/lambda-function"

  name        = "${local.name_prefix}-pipeline-orchestrator"
  source_dir  = "${path.root}/../lambdas/pipeline_orchestrator"
  handler     = "handler.lambda_handler"
  runtime     = "python3.12"
  timeout     = 600   # 10 min — Textract + Bedrock can be slow on large PDFs
  memory_size = 512

  environment = {
    AWS_REGION_NAME               = var.aws_region
    S3_DOCUMENTS_BUCKET           = aws_s3_bucket.documents.id
    S3_ML_DATA_BUCKET             = aws_s3_bucket.ml_data.id
    BEDROCK_MODEL_ID              = var.bedrock_model_id
    API_INTERNAL_URL              = var.api_internal_url
    API_CALLBACK_SECRET           = var.api_callback_secret
    REKOGNITION_PROJECT_VERSION_ARN = var.rekognition_project_version_arn
  }

  policy_statements = [
    # Textract — read documents from S3
    {
      Effect   = "Allow"
      Action   = [
        "textract:DetectDocumentText",
        "textract:AnalyzeDocument",
        "textract:StartDocumentTextDetection",
        "textract:GetDocumentTextDetection",
      ]
      Resource = "*"
    },
    # Rekognition — standard labels + custom labels
    {
      Effect   = "Allow"
      Action   = [
        "rekognition:DetectLabels",
        "rekognition:DetectText",
        "rekognition:DetectCustomLabels",
        "rekognition:DetectFaces",
      ]
      Resource = "*"
    },
    # Bedrock — invoke Claude for fraud analysis
    {
      Effect   = "Allow"
      Action   = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/*"
    },
    # S3 — read transcript documents
    {
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.documents.arn}/*"
    },
    # S3 — read/write objects in ML bucket
    {
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "${aws_s3_bucket.ml_data.arn}/*"
    },
    # S3 — list ML bucket
    {
      Effect   = "Allow"
      Action   = ["s3:ListBucket"]
      Resource = aws_s3_bucket.ml_data.arn
    },
  ]

  tags = local.common_tags
}

# Allow API task role to invoke the pipeline orchestrator
resource "aws_iam_role_policy" "api_invoke_pipeline" {
  name = "${local.name_prefix}-api-invoke-pipeline"
  role = aws_iam_role.api_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = module.pipeline_orchestrator_lambda.function_arn
    }]
  })
}

# ── Secrets Manager — API callback secret ─────────────────────────────────────
# Lambda uses this secret to authenticate its PATCH calls back to FastAPI

resource "aws_secretsmanager_secret" "api_callback_secret" {
  name                    = "${local.name_prefix}/api-callback-secret"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "api_callback_secret" {
  secret_id     = aws_secretsmanager_secret.api_callback_secret.id
  secret_string = var.api_callback_secret
}
