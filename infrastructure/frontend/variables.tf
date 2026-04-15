variable "aws_region" {
  type        = string
  description = "AWS region"
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
