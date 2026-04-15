variable "name" {
  type        = string
  description = "Name prefix for the RDS instance"
}

variable "db_name" {
  type        = string
  description = "Database name"
}

variable "db_username" {
  type        = string
  description = "Master username"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Master password"
}

variable "instance_class" {
  type        = string
  default     = "db.t3.micro"
  description = "RDS instance type"
}

variable "allocated_storage" {
  type        = number
  default     = 20
  description = "Initial allocated storage in GB"
}

variable "max_allocated_storage" {
  type        = number
  default     = 100
  description = "Maximum storage autoscaling limit in GB"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Private subnet IDs for the DB subnet group"
}

variable "security_group_ids" {
  type        = list(string)
  description = "Security group IDs to attach to the RDS instance"
}

variable "multi_az" {
  type        = bool
  default     = false
  description = "Enable Multi-AZ deployment (recommended for prod)"
}

variable "backup_retention_period" {
  type        = number
  default     = 7
  description = "Number of days to retain automated backups"
}

variable "deletion_protection" {
  type        = bool
  default     = false
  description = "Enable deletion protection (set true for prod)"
}

variable "skip_final_snapshot" {
  type        = bool
  default     = true
  description = "Skip final snapshot on destroy (set false for prod)"
}

variable "tags" {
  type    = map(string)
  default = {}
}
