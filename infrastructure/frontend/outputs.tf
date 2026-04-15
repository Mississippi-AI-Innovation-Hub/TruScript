output "cloudfront_domain_name" {
  value       = aws_cloudfront_distribution.frontend.domain_name
  description = "CloudFront domain — point your CNAME here, or access directly"
}

output "cloudfront_distribution_id" {
  value       = aws_cloudfront_distribution.frontend.id
  description = "Used to invalidate the cache after deploying new FE builds"
}

output "s3_bucket_name" {
  value       = aws_s3_bucket.frontend.id
  description = "S3 bucket to upload the React build output (dist/)"
}
