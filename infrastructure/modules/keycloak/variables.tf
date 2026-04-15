variable "name" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "image" {
  type        = string
  description = "Keycloak Docker image URI from ECR"
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "task_execution_role_arn" {
  type = string
}

variable "subnets" {
  type = list(string)
}

variable "security_groups" {
  type = list(string)
}

variable "target_group_arn" {
  type = string
}

variable "log_group" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "admin_password_secret_arn" {
  type        = string
  description = "Secrets Manager ARN for KEYCLOAK_ADMIN_PASSWORD"
}

variable "tags" {
  type    = map(string)
  default = {}
}
