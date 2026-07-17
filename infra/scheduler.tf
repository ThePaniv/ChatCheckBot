# --- Check tick: EventBridge Scheduler fires the cron Lambda every minute; ----
# the handler prompts only the users whose per-user frequency is currently due.

resource "aws_iam_role" "scheduler" {
  name = "telegram_water_bot_scheduler_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke_cron_lambda"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.cron.arn
    }]
  })
}

resource "aws_scheduler_schedule" "tick" {
  name = "water_bot_tick"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = var.tick_schedule

  target {
    arn      = aws_lambda_function.cron.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
