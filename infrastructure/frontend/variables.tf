variable "aws_region" {
  type        = string
  description = "AWS region"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile name to use for authentication"
}

variable "aws_account_id" {
  type        = string
  description = "12-digit AWS account ID — used for globally unique S3 bucket name"
}

variable "env" {
  type        = string
  description = "dev | prod"
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

variable "api_alb_dns_name" {
  type        = string
  description = "DNS name of the backend ALB (without scheme) — e.g. msbn-dev-alb-123.us-east-1.elb.amazonaws.com"
}
