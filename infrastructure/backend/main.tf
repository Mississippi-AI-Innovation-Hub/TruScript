terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment after creating the S3 state bucket + DynamoDB lock table:
  # backend "s3" {
  #   bucket         = "TODO: msbn-terraform-state-<your-account-id>"
  #   key            = "msbn/backend/terraform.tfstate"
  #   region         = "TODO: us-east-1"
  #   dynamodb_table = "TODO: msbn-terraform-state-lock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region  = var.aws_region
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

# ── Data ─────────────────────────────────────────────────────────────────────

data "aws_availability_zones" "available" {
  state = "available"
}

# ── VPC ──────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.common_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Name = "${local.name_prefix}-public-${count.index + 1}" })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + 2)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.common_tags, { Name = "${local.name_prefix}-private-${count.index + 1}" })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.common_tags, { Name = "${local.name_prefix}-nat" })
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-rt-public" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-rt-private" })
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── Security Groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB - allow inbound HTTP/HTTPS"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-sg" })
}

resource "aws_security_group" "api" {
  name        = "${local.name_prefix}-api-sg"
  description = "FastAPI ECS task - inbound from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-api-sg" })
}

resource "aws_security_group" "keycloak" {
  name        = "${local.name_prefix}-keycloak-sg"
  description = "Keycloak ECS task - inbound from ALB and API"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id, aws_security_group.api.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-keycloak-sg" })
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "RDS PostgreSQL - inbound from API only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-rds-sg" })
}

# ── ECR ───────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "api" {
  name                 = "${local.name_prefix}-api"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecr_repository" "keycloak" {
  name                 = "${local.name_prefix}-keycloak"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.common_tags
}

# ── S3 — Document Uploads ─────────────────────────────────────────────────────

resource "aws_s3_bucket" "documents" {
  bucket = "${local.name_prefix}-documents-${var.aws_account_id}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Secrets Manager ───────────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.name_prefix}/db-password"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name_prefix}/app-secrets"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SECRET_KEY             = var.app_secret_key
    KEYCLOAK_CLIENT_SECRET = var.keycloak_client_secret
  })
}

resource "aws_secretsmanager_secret" "keycloak_admin" {
  name                    = "${local.name_prefix}/keycloak-admin-password"
  recovery_window_in_days = var.env == "prod" ? 30 : 0
  tags                    = local.common_tags
}

resource "aws_secretsmanager_secret_version" "keycloak_admin" {
  secret_id     = aws_secretsmanager_secret.keycloak_admin.id
  secret_string = var.keycloak_admin_password
}

# ── IAM — ECS Task Execution Role ─────────────────────────────────────────────

resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.name_prefix}-ecs-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "${local.name_prefix}-ecs-execution-secrets"
  role = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.db_password.arn,
        aws_secretsmanager_secret.app.arn,
        aws_secretsmanager_secret.keycloak_admin.arn,
      ]
    }]
  })
}

# ── IAM — API Task Role (permissions the running container has) ────────────────

resource "aws_iam_role" "api_task" {
  name = "${local.name_prefix}-api-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "api_task" {
  name = "${local.name_prefix}-api-task-policy"
  role = aws_iam_role.api_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Documents"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*",
        ]
      },
      {
        Sid    = "InvokeLambdas"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          module.textract_lambda.function_arn,
          module.fraud_analyzer_lambda.function_arn,
          module.rekognition_lambda.function_arn,
        ]
      },
      {
        Sid      = "ReadSecrets"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [
          aws_secretsmanager_secret.db_password.arn,
          aws_secretsmanager_secret.app.arn,
        ]
      },
    ]
  })
}

# ── ALB ───────────────────────────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags               = local.common_tags
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name_prefix}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
  }
  tags = local.common_tags
}

resource "aws_lb_target_group" "keycloak" {
  name        = "${local.name_prefix}-kc-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path     = "/health/ready"
    port     = "8080"
    interval = 30
  }
  tags = local.common_tags
}

# Port 80 → API (default), /auth/* → Keycloak
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener_rule" "keycloak" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.keycloak.arn
  }

  condition {
    path_pattern { values = ["/auth/*", "/realms/*", "/resources/*", "/admin", "/admin/*"] }
  }
}

# ── ECS Cluster ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name_prefix}/api"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "keycloak" {
  name              = "/ecs/${local.name_prefix}/keycloak"
  retention_in_days = var.log_retention_days
  tags              = local.common_tags
}

# ── ECS — FastAPI ─────────────────────────────────────────────────────────────

module "api_service" {
  source = "../modules/ecs-service"

  name                    = "${local.name_prefix}-api"
  cluster_id              = aws_ecs_cluster.main.id
  task_execution_role_arn = aws_iam_role.ecs_task_execution.arn
  task_role_arn           = aws_iam_role.api_task.arn
  image                   = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
  cpu                     = var.api_cpu
  memory                  = var.api_memory
  desired_count           = var.api_desired_count
  container_port          = 8000
  subnets                 = aws_subnet.private[*].id
  security_groups         = [aws_security_group.api.id]
  target_group_arn        = aws_lb_target_group.api.arn
  log_group               = aws_cloudwatch_log_group.api.name
  aws_region              = var.aws_region

  environment = [
    { name = "APP_ENV",                  value = var.env },
    { name = "DEBUG",                    value = var.env == "prod" ? "false" : "true" },
    { name = "DATABASE_URL",             value = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${module.database.endpoint}/${var.db_name}" },
    { name = "DATABASE_SYNC_URL",        value = "postgresql+psycopg2://${var.db_username}:${var.db_password}@${module.database.endpoint}/${var.db_name}" },
    { name = "KEYCLOAK_URL",             value = "http://${aws_lb.main.dns_name}" },
    { name = "KEYCLOAK_REALM",           value = "msbn" },
    { name = "KEYCLOAK_CLIENT_ID",       value = "msbn-api" },
    { name = "AWS_REGION_NAME",          value = var.aws_region },
    { name = "S3_DOCUMENTS_BUCKET",      value = aws_s3_bucket.documents.id },
    { name = "LAMBDA_TEXTRACT_ARN",      value = module.textract_lambda.function_arn },
    { name = "LAMBDA_FRAUD_ARN",         value = module.fraud_analyzer_lambda.function_arn },
    { name = "LAMBDA_REKOGNITION_ARN",   value = module.rekognition_lambda.function_arn },
    { name = "ALLOWED_ORIGINS",          value = jsonencode(split(",", var.allowed_origins)) },
  ]

  secrets = [
    { name = "SECRET_KEY",             valueFrom = "${aws_secretsmanager_secret.app.arn}:SECRET_KEY::" },
    { name = "KEYCLOAK_CLIENT_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:KEYCLOAK_CLIENT_SECRET::" },
  ]

  tags = local.common_tags
}

# ── ECS — Keycloak ────────────────────────────────────────────────────────────

module "keycloak_service" {
  source = "../modules/keycloak"

  name                      = "${local.name_prefix}-keycloak"
  cluster_id                = aws_ecs_cluster.main.id
  task_execution_role_arn   = aws_iam_role.ecs_task_execution.arn
  image                     = "${aws_ecr_repository.keycloak.repository_url}:${var.keycloak_image_tag}"
  cpu                       = var.keycloak_cpu
  memory                    = var.keycloak_memory
  desired_count             = 1
  subnets                   = aws_subnet.private[*].id
  security_groups           = [aws_security_group.keycloak.id]
  target_group_arn          = aws_lb_target_group.keycloak.arn
  log_group                 = aws_cloudwatch_log_group.keycloak.name
  aws_region                = var.aws_region
  admin_password_secret_arn = aws_secretsmanager_secret.keycloak_admin.arn
  hostname                  = var.keycloak_hostname
  tags                      = local.common_tags
}

# ── RDS PostgreSQL ────────────────────────────────────────────────────────────

module "database" {
  source = "../modules/rds-postgres"

  name               = local.name_prefix
  db_name            = var.db_name
  db_username        = var.db_username
  db_password        = var.db_password
  instance_class     = var.db_instance_class
  subnet_ids         = aws_subnet.private[*].id
  security_group_ids = [aws_security_group.rds.id]
  multi_az           = var.env == "prod"
  deletion_protection = var.env == "prod"
  skip_final_snapshot = var.env != "prod"
  tags               = local.common_tags
}

# ── Lambda — Textract ─────────────────────────────────────────────────────────

module "textract_lambda" {
  source = "../modules/lambda-function"

  name        = "${local.name_prefix}-textract-processor"
  source_dir  = "${path.root}/../lambdas/textract_processor"
  handler     = "handler.lambda_handler"
  runtime     = "python3.12"
  timeout     = 300
  memory_size = 512

  environment = {
    ENV             = var.env
    S3_BUCKET       = aws_s3_bucket.documents.id
    AWS_REGION_NAME = var.aws_region
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["textract:AnalyzeDocument", "textract:DetectDocumentText", "textract:StartDocumentTextDetection", "textract:GetDocumentTextDetection"]
      Resource = "*"
    },
    {
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.documents.arn}/*"
    },
  ]

  tags = local.common_tags
}

# ── Lambda — Bedrock Fraud Analyzer ──────────────────────────────────────────

module "fraud_analyzer_lambda" {
  source = "../modules/lambda-function"

  name        = "${local.name_prefix}-fraud-analyzer"
  source_dir  = "${path.root}/../lambdas/fraud_analyzer"
  handler     = "handler.lambda_handler"
  runtime     = "python3.12"
  timeout     = 120
  memory_size = 256

  environment = {
    ENV              = var.env
    BEDROCK_MODEL_ID = var.bedrock_model_id
    AWS_REGION_NAME  = var.aws_region
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
    },
  ]

  tags = local.common_tags
}

# ── Lambda — Rekognition Checker ──────────────────────────────────────────────

module "rekognition_lambda" {
  source = "../modules/lambda-function"

  name        = "${local.name_prefix}-rekognition-checker"
  source_dir  = "${path.root}/../lambdas/rekognition_checker"
  handler     = "handler.lambda_handler"
  runtime     = "python3.12"
  timeout     = 60
  memory_size = 256

  environment = {
    ENV             = var.env
    S3_BUCKET       = aws_s3_bucket.documents.id
    AWS_REGION_NAME = var.aws_region
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["rekognition:DetectText", "rekognition:DetectLabels", "rekognition:DetectFaces"]
      Resource = "*"
    },
    {
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.documents.arn}/*"
    },
  ]

  tags = local.common_tags
}
