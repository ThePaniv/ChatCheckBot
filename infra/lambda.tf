# --- Lambda functions (same image, different entry handlers) ------------------

locals {
  lambda_env = {
    USERS_TABLE          = aws_dynamodb_table.users.name
    LOGS_TABLE           = aws_dynamodb_table.logs.name
    BOT_TOKEN_PARAM      = var.bot_token_param
    WEBHOOK_SECRET_PARAM = var.webhook_secret_param
    BOT_TZ               = var.bot_timezone
  }
}

resource "aws_lambda_function" "webhook" {
  function_name = "water_bot_webhook"
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
}

resource "aws_lambda_function" "cron" {
  function_name = "water_bot_cron"
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
