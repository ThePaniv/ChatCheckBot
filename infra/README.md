# infra/ — Terraform for ChatCheckBot's AWS resources

Infrastructure-as-code for the whole serverless stack in `us-east-1` (override
with `-var aws_region=...`).

## What's managed here

| Resource | Terraform address | Notes |
|---|---|---|
| ECR repo | `aws_ecr_repository.bot` | `chatcheck-bot` — holds the Lambda image |
| DynamoDB tables | `aws_dynamodb_table.users`, `.logs` | `WaterBotUsers`, `WaterBotLogs` (on-demand) |
| Lambda functions | `aws_lambda_function.webhook`, `.cron` | same image, different `image_config.command` |
| Function URL | `aws_lambda_function_url.webhook` | public; auth is the Telegram secret-token header |
| Daily schedule | `aws_scheduler_schedule.daily_reminder` | EventBridge Scheduler, timezone-aware |
| Lambda exec role | `aws_iam_role.lambda_exec` | scoped to the two tables + the two SSM params |
| CI user | `aws_iam_user.github_ci` | `github-cli` — ECR push + Lambda update only; its access key is mounted as Actions secrets |
| SSM parameters | `aws_ssm_parameter.bot_token`, `.webhook_secret` | SecureStrings; token value from git-ignored `terraform.tfvars`, secret generated |

**Secrets:** the Telegram token lives only in `terraform.tfvars` and the local
state file — both git-ignored. Never commit either. The CI user's access key
also lives in state; mount it into GitHub with:

```bash
terraform output -raw github_ci_access_key_id     | gh secret set AWS_ACCESS_KEY_ID
terraform output -raw github_ci_secret_access_key | gh secret set AWS_SECRET_ACCESS_KEY
```

## Prerequisites

- Terraform ≥ 1.6.
- Authenticated AWS CLI (or pass `-var aws_profile=<name>`).

## Usage

```bash
cd infra
echo 'telegram_bot_token = "<YOUR_BOT_TOKEN>"' > terraform.tfvars
terraform init

# First run only — the Lambdas need an image to exist:
terraform apply -target=aws_ecr_repository.bot
# ...build & push the image (see root README), then:
terraform apply
```

## State

State is **local** (`terraform.tfstate`) and git-ignored because it can contain
sensitive values. If you work from more than one machine or want durability,
switch to an S3 backend in `versions.tf` and `terraform init -migrate-state`.
