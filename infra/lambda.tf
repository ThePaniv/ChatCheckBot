# --- Lambda functions (same image, different entry handlers) ------------------

locals {
  webhook_function_name = "water_bot_webhook"
  cron_function_name    = "water_bot_cron"
  stats_function_name   = "water_bot_stats"

  lambda_env = {
    USERS_TABLE          = aws_dynamodb_table.users.name
    LOGS_TABLE           = aws_dynamodb_table.logs.name
    BOT_TOKEN_PARAM      = var.bot_token_param
    WEBHOOK_SECRET_PARAM = var.webhook_secret_param
    STATS_TOKEN_PARAM    = var.stats_token_param
  }
}

resource "aws_lambda_function" "webhook" {
  function_name = local.webhook_function_name
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.bot.repository_url}:${var.image_tag}"
  timeout       = 30
  memory_size   = 512

  image_config {
    command = ["chatcheck_bot.bot.webhook_handler"]
  }

  environment {
    variables = local.lambda_env
  }

  # CI (deploy.yml) owns the running image via update-function-code with a
  # commit-pinned tag; Terraform only seeds it at creation. Ignore image_uri so
  # a later infra-only apply doesn't roll the function back to :latest.
  lifecycle {
    ignore_changes = [image_uri]
  }

  # Own the log group (with retention) before Lambda auto-creates an
  # unmanaged, never-expiring one.
  depends_on = [aws_cloudwatch_log_group.webhook]
}

resource "aws_lambda_function" "cron" {
  function_name = local.cron_function_name
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.bot.repository_url}:${var.image_tag}"
  timeout       = 300
  memory_size   = 512

  image_config {
    command = ["chatcheck_bot.bot.cron_handler"]
  }

  environment {
    variables = local.lambda_env
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_cloudwatch_log_group.cron]
}

resource "aws_lambda_function" "stats" {
  function_name = local.stats_function_name
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.bot.repository_url}:${var.image_tag}"
  timeout       = 30
  memory_size   = 512

  image_config {
    command = ["chatcheck_bot.bot.stats_handler"]
  }

  environment {
    variables = local.lambda_env
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_cloudwatch_log_group.stats]
}

# --- Webhook ingress: Lambda Function URL (free, no API Gateway needed) -------
# Authentication is handled in code via Telegram's secret_token header.

resource "aws_lambda_function_url" "webhook" {
  function_name      = aws_lambda_function.webhook.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.webhook.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Since October 2025 AWS additionally requires lambda:InvokeFunction in the
# resource policy for public (auth NONE) function URLs — without it every
# request gets a 403 from the URL front door. The AddPermission API only
# allows the scoping lambda:InvokedViaFunctionUrl condition via a parameter
# this provider version doesn't expose (hashicorp/terraform-provider-aws#44829),
# so the grant is unconditioned; that's acceptable because the handler
# authenticates every request itself via the Telegram secret-token header.
resource "aws_lambda_permission" "public_url_invoke" {
  statement_id  = "AllowPublicFunctionUrlInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "*"
}

# --- Stats egress: a second public Function URL --------------------------------
# Read-only JSON for Grafana's Infinity datasource. Auth NONE at the front door;
# the handler itself requires an `Authorization: Bearer <stats_token>` header
# (compared with hmac.compare_digest), so the public URL exposes nothing without
# the token. Same two-permission grant the webhook needs post-Oct-2025.

resource "aws_lambda_function_url" "stats" {
  function_name      = aws_lambda_function.stats.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "stats_public_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.stats.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "stats_public_url_invoke" {
  statement_id  = "AllowPublicFunctionUrlInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stats.function_name
  principal     = "*"
}
