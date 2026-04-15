output "endpoint" {
  value       = aws_db_instance.this.endpoint
  description = "RDS connection endpoint (host:port)"
}

output "address" {
  value       = aws_db_instance.this.address
  description = "RDS hostname"
}

output "port" {
  value       = aws_db_instance.this.port
  description = "RDS port"
}

output "instance_id" {
  value = aws_db_instance.this.id
}
