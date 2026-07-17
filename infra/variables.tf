variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
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
  description = "SSM SecureString parameter name holding the Telegram bot token (create manually before apply)"
  type        = string
  default     = "/telegram/bot_token"
}

variable "webhook_secret_param" {
  description = "SSM SecureString parameter name holding the webhook secret token (create manually before apply)"
  type        = string
  default     = "/telegram/webhook_secret"
}

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to assume the deploy role via OIDC"
  type        = string
  default     = "ThePaniv/ChatCheckBot"
}

variable "deploy_ref_branches" {
  description = "Branches whose pushes may assume the deploy role"
  type        = list(string)
  default     = ["develop"]
}
