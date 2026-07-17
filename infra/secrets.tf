# SSM SecureString parameters holding the bot's secrets.
#
# The token VALUE comes from terraform.tfvars (git-ignored) and ends up in the
# local state file (also git-ignored) — it must never appear in committed code.

resource "aws_ssm_parameter" "bot_token" {
  name  = var.bot_token_param
  type  = "SecureString"
  value = var.telegram_bot_token
}

# Random string Telegram echoes back on every webhook call; the webhook
# handler 403s anything that doesn't present it.
resource "random_password" "webhook_secret" {
  length  = 64
  special = false
}

resource "aws_ssm_parameter" "webhook_secret" {
  name  = var.webhook_secret_param
  type  = "SecureString"
  value = random_password.webhook_secret.result
}
