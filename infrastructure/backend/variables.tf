# ── AWS ──────────────────────────────────────────────────────────────────────

variable "aws_region" {
  type        = string
  description = "AWS region to deploy into (e.g. us-east-1)"
}

variable "aws_account_id" {
  type        = string
  description = "12-digit AWS account ID — used to name globally unique S3 buckets"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile name to use for authentication"
}

# ── Environment ───────────────────────────────────────────────────────────────

variable "env" {
  type        = string
  description = "Deployment environment: dev | prod"
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

# ── Networking ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "CIDR block for the VPC (dev: 10.0.0.0/16, prod: 10.1.0.0/16)"
}

# ── Database ──────────────────────────────────────────────────────────────────

variable "db_name" {
  type    = string
  default = "msbn_db"
}

variable "db_username" {
  type    = string
  default = "msbn_user"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "RDS master password — set via tfvars, never hardcode"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

# ── Secrets ───────────────────────────────────────────────────────────────────

variable "app_secret_key" {
  type        = string
  sensitive   = true
  description = "FastAPI SECRET_KEY — minimum 32 characters"
}

variable "keycloak_client_secret" {
  type      = string
  sensitive = true
}

variable "keycloak_admin_password" {
  type      = string
  sensitive = true
}

# ── Container Images ──────────────────────────────────────────────────────────

variable "api_image_tag" {
  type    = string
  default = "latest"
}

variable "keycloak_image_tag" {
  type    = string
  default = "latest"
}

# ── ECS Sizing ────────────────────────────────────────────────────────────────

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "keycloak_cpu" {
  type    = number
  default = 512
}

variable "keycloak_memory" {
  type    = number
  default = 1024
}

# ── ML ────────────────────────────────────────────────────────────────────────

variable "bedrock_model_id" {
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
  description = "Bedrock foundation model ID for fraud analysis"
}

variable "rekognition_project_version_arn" {
  type        = string
  default     = ""
  description = "ARN of a trained + deployed Rekognition Custom Labels model. Leave empty to skip custom classification and use standard labels only."
}

variable "api_internal_url" {
  type        = string
  default     = ""
  description = "Internal ALB URL the pipeline Lambda uses to PATCH results back to FastAPI (e.g. http://msbn-dev-alb-xxxx.us-east-1.elb.amazonaws.com). Fill in after first terraform apply."
}

variable "api_callback_secret" {
  type        = string
  sensitive   = true
  description = "Shared secret the Lambda sends as X-Lambda-Secret header when calling FastAPI. FastAPI verifies this to accept ML result callbacks."
}

# ── Misc ──────────────────────────────────────────────────────────────────────

variable "allowed_origins" {
  type        = string
  description = "Comma-separated CORS origins (e.g. https://app.example.com)"
}

variable "log_retention_days" {
  type    = number
  default = 7
}
