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

output "deploy_role_arn" {
  description = "Role the GitHub Actions deploy workflow assumes (hardcode in deploy.yml)"
  value       = aws_iam_role.deploy.arn
}
