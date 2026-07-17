variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use (null = default credential chain)"
  type        = string
  default     = null
}

variable "ecr_repo_name" {
  description = "Name of the ECR repository holding the bot image"
  type        = string
  default     = "chatcheck-bot"
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}

variable "bot_timezone" {
  description = "IANA timezone for daily reminders and date bucketing (must match the BOT_TZ the code uses)"
  type        = string
  default     = "UTC"
}

variable "reminder_schedule" {
  description = "Cron expression for the daily reminder, evaluated in bot_timezone"
  type        = string
  default     = "cron(0 20 * * ? *)"
}

variable "bot_token_param" {
  description = "SSM SecureString parameter name holding the Telegram bot token"
  type        = string
  default     = "/telegram/bot_token"
}

variable "webhook_secret_param" {
  description = "SSM SecureString parameter name holding the webhook secret token"
  type        = string
  default     = "/telegram/webhook_secret"
}

variable "telegram_bot_token" {
  description = "Telegram bot token from @BotFather. Set it in terraform.tfvars (git-ignored) — never commit it."
  type        = string
  sensitive   = true
}

