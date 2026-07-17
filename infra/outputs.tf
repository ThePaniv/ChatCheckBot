output "webhook_url" {
  description = "Register this URL with Telegram via setWebhook"
  value       = aws_lambda_function_url.webhook.function_url
}

output "ecr_repository_url" {
  description = "Push the bot image here"
  value       = aws_ecr_repository.bot.repository_url
}

output "webhook_secret" {
  description = "Pass as secret_token when registering the webhook (terraform output -raw webhook_secret)"
  value       = random_password.webhook_secret.result
  sensitive   = true
}

output "github_ci_access_key_id" {
  description = "Mount as the AWS_ACCESS_KEY_ID Actions secret"
  value       = aws_iam_access_key.github_ci.id
  sensitive   = true
}

output "github_ci_secret_access_key" {
  description = "Mount as the AWS_SECRET_ACCESS_KEY Actions secret"
  value       = aws_iam_access_key.github_ci.secret
  sensitive   = true
}
