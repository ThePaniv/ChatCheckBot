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

variable "tick_schedule" {
  description = "EventBridge Scheduler expression for the check tick. Must be fine enough to serve the shortest user frequency (the 1-minute test option)."
  type        = string
  default     = "rate(1 minute)"
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda log groups"
  type        = number
  default     = 14
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

variable "stats_token_param" {
  description = "SSM SecureString parameter name holding the read-only stats endpoint bearer token"
  type        = string
  default     = "/telegram/stats_token"
}

variable "telegram_bot_token" {
  description = "Telegram bot token from @BotFather. Set it in terraform.tfvars (git-ignored) — never commit it."
  type        = string
  sensitive   = true
}

