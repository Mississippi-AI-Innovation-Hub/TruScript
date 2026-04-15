terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment after creating the S3 state bucket:
  # backend "s3" {
  #   bucket         = "TODO: msbn-terraform-state-<your-account-id>"
  #   key            = "msbn/frontend/terraform.tfstate"
  #   region         = "TODO: us-east-1"
  #   dynamodb_table = "TODO: msbn-terraform-state-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# CloudFront requires ACM certificates in us-east-1 regardless of region
provider "aws" {
  alias   = "us_east_1"
  region  = "us-east-1"
  profile = var.aws_profile
}

locals {
  name_prefix = "msbn-${var.env}"
  common_tags = {
    Project     = "msbn"
    Environment = var.env
    ManagedBy   = "terraform"
  }
}

# ── S3 — React Static Assets ──────────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket = "${local.name_prefix}-frontend-${var.aws_account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# OAC — allows CloudFront to read from private S3 bucket
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name_prefix}-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
        }
      }
    }]
  })
}

# ── CloudFront Distribution ───────────────────────────────────────────────────

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  tags                = local.common_tags

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    # Cache for 1 hour in prod, 0 in dev for faster iteration
    default_ttl = var.env == "prod" ? 3600 : 0
    max_ttl     = var.env == "prod" ? 86400 : 0
    min_ttl     = 0
  }

  # SPA fallback — redirect 403/404 to index.html so React Router works
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    # To use a custom domain, replace with:
    # acm_certificate_arn      = "TODO: arn:aws:acm:us-east-1:...:certificate/..."
    # ssl_support_method       = "sni-only"
    # minimum_protocol_version = "TLSv1.2_2021"
  }
}
