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
| Deploy role | `aws_iam_role.deploy` | `github-actions-chatcheck-deploy`, trust scoped to this repo's `develop` |

**Not managed here:** the SSM SecureString *values* (`/telegram/bot_token`,
`/telegram/webhook_secret` — create them out-of-band before applying, see the
root README) and the account-wide GitHub OIDC provider (owned by
VuDrochkaBot's Terraform; referenced via a data source).

## Prerequisites

- Terraform ≥ 1.6.
- Authenticated AWS CLI (or pass `-var aws_profile=<name>`).

## Usage

```bash
cd infra
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
