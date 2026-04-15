variable "name" {
  type        = string
  description = "Lambda function name"
}

variable "source_dir" {
  type        = string
  description = "Path to the directory containing Lambda handler code"
}

variable "handler" {
  type        = string
  description = "Handler in format filename.function_name (e.g. handler.lambda_handler)"
}

variable "runtime" {
  type        = string
  default     = "python3.12"
  description = "Lambda runtime"
}

variable "timeout" {
  type        = number
  default     = 30
  description = "Lambda timeout in seconds"
}

variable "memory_size" {
  type        = number
  default     = 256
  description = "Lambda memory in MB"
}

variable "environment" {
  type        = map(string)
  default     = {}
  description = "Environment variables for the Lambda"
}

variable "policy_statements" {
  type        = list(any)
  default     = []
  description = "Additional IAM policy statements. Resource can be a string or list of strings."
}

variable "tags" {
  type    = map(string)
  default = {}
}
