resource "aws_ecs_task_definition" "keycloak" {
  family                   = var.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.task_execution_role_arn

  container_definitions = jsonencode([{
    name      = "keycloak"
    image     = var.image
    essential = true

    # "start-dev" uses embedded H2 database — fine for dev; switch to "start"
    # with KC_DB/KC_DB_URL env vars for production.
    command = ["start-dev"]

    portMappings = [{
      containerPort = 8080
      protocol      = "tcp"
    }]

    environment = [
      { name = "KEYCLOAK_ADMIN",           value = "admin" },
      { name = "KC_HEALTH_ENABLED",        value = "true" },
      { name = "KC_LOG_LEVEL",             value = "INFO" },
      { name = "KC_HTTP_ENABLED",          value = "true" },
      { name = "KC_HOSTNAME_STRICT",       value = "false" },
      { name = "KC_HOSTNAME_STRICT_HTTPS", value = "false" },
      { name = "KC_HTTP_PORT",             value = "8080" },
    ]

    secrets = [
      { name = "KEYCLOAK_ADMIN_PASSWORD", valueFrom = var.admin_password_secret_arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = var.log_group
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  tags = var.tags
}

resource "aws_ecs_service" "keycloak" {
  name                              = var.name
  cluster                           = var.cluster_id
  task_definition                   = aws_ecs_task_definition.keycloak.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  # Keycloak takes ~90s to start in start-dev mode; grace period prevents ECS
  # from killing the task based on failed health checks during startup.
  health_check_grace_period_seconds = 210

  network_configuration {
    subnets          = var.subnets
    security_groups  = var.security_groups
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "keycloak"
    container_port   = 8080
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  tags = var.tags
}
