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
