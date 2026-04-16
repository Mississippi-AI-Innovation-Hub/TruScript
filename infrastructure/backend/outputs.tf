output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "ALB public DNS — point your domain's CNAME here"
}

output "api_ecr_repository_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "Push your FastAPI Docker image to this ECR repo"
}

output "keycloak_ecr_repository_url" {
  value       = aws_ecr_repository.keycloak.repository_url
  description = "Push your Keycloak Docker image to this ECR repo"
}

output "documents_bucket_name" {
  value       = aws_s3_bucket.documents.id
  description = "S3 bucket name for transcript document uploads"
}

output "rds_endpoint" {
  value       = module.database.endpoint
  description = "RDS PostgreSQL connection endpoint"
}

output "textract_lambda_arn" {
  value = module.textract_lambda.function_arn
}

output "fraud_analyzer_lambda_arn" {
  value = module.fraud_analyzer_lambda.function_arn
}

output "rekognition_lambda_arn" {
  value = module.rekognition_lambda.function_arn
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "ml_data_bucket_name" {
  value       = aws_s3_bucket.ml_data.id
  description = "S3 bucket for ML training/test data and fraud rules"
}

output "pipeline_orchestrator_lambda_arn" {
  value       = module.pipeline_orchestrator_lambda.function_arn
  description = "Main ML pipeline Lambda — invoke this from FastAPI after document upload"
}

output "rekognition_project_arn" {
  value       = "arn:aws:rekognition:${var.aws_region}:${var.aws_account_id}:project/${local.name_prefix}-transcript-fraud/1776280793116"
  description = "Rekognition Custom Labels project ARN — used by train-rekognition-model.sh"
}

output "rekognition_s3_role_arn" {
  value       = aws_iam_role.rekognition_s3.arn
  description = "IAM role ARN that allows Rekognition to access the ML data bucket"
}

output "aws_region" {
  value = var.aws_region
}
