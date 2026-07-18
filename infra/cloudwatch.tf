# Lambda writes to /aws/lambda/<function_name>. Manage these groups explicitly
# so they carry a bounded retention and are torn down with the stack — otherwise
# Lambda auto-creates them with never-expire retention and they orphan on destroy.
# Names come from the shared locals (not the function resources) to avoid a
# dependency cycle with the Lambdas' depends_on.

resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${local.webhook_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "cron" {
  name              = "/aws/lambda/${local.cron_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "stats" {
  name              = "/aws/lambda/${local.stats_function_name}"
  retention_in_days = var.log_retention_days
}
