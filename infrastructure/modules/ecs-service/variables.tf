variable "name" {
  type        = string
  description = "Name prefix for all resources"
}

variable "cluster_id" {
  type        = string
  description = "ECS cluster ID"
}

variable "image" {
  type        = string
  description = "Docker image URI (ECR)"
}

variable "cpu" {
  type        = number
  default     = 512
  description = "Fargate task CPU units"
}

variable "memory" {
  type        = number
  default     = 1024
  description = "Fargate task memory (MB)"
}

variable "desired_count" {
  type        = number
  default     = 1
  description = "Number of running tasks"
}

variable "container_port" {
  type        = number
  description = "Port the container listens on"
}

variable "task_execution_role_arn" {
  type        = string
  description = "IAM role ARN for ECS task execution (ECR pull, CloudWatch)"
}

variable "task_role_arn" {
  type        = string
  default     = null
  description = "IAM role ARN granted to the running container"
}

variable "subnets" {
  type        = list(string)
  description = "Private subnet IDs"
}

variable "security_groups" {
  type        = list(string)
  description = "Security group IDs for the task"
}

variable "target_group_arn" {
  type        = string
  description = "ALB target group ARN"
}

variable "log_group" {
  type        = string
  description = "CloudWatch log group name"
}

variable "aws_region" {
  type        = string
  description = "AWS region (for CloudWatch log driver)"
}

variable "environment" {
  type = list(object({
    name  = string
    value = string
  }))
  default     = []
  description = "Plain-text environment variables"
}

variable "secrets" {
  type = list(object({
    name      = string
    valueFrom = string
  }))
  default     = []
  description = "Secrets from Secrets Manager / SSM"
}

variable "tags" {
  type    = map(string)
  default = {}
}
