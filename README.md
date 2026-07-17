# ChatCheckBot — Serverless Telegram Water Bot on AWS

A Telegram bot that registers users via contact sharing and tracks daily water intake (yes / no / ignored). Runs entirely on always-free-tier-friendly serverless components, deployed by CI on every merge.

## Architecture

- **Webhook path:** Telegram → **Lambda Function URL** → `chatcheck_bot.bot.webhook_handler` — handles `/start`, contact registration, and Yes/No button taps. (Function URLs are free forever; API Gateway's free tier expires after 12 months.)
- **Daily reminder path:** **EventBridge Scheduler** (timezone-aware daily cron) → `chatcheck_bot.bot.cron_handler` — marks *yesterday* as `ignored` for anyone who never answered, then sends today's prompt.
- Both handlers ship in the **same Docker image**; each Lambda overrides the entrypoint via `image_config.command`.
- **DynamoDB** (on-demand): `WaterBotUsers` (PK `user_id`) and `WaterBotLogs` (PK `user_id`, SK `date`).
- **SSM Parameter Store** (SecureString): bot token and webhook secret.
- Webhook requests are authenticated by comparing Telegram's `X-Telegram-Bot-Api-Secret-Token` header against the stored secret; everything else gets a 403.

Answers carry their date in the callback data (`water:yes:2026-07-17`), so a tap after midnight still logs against the day the question was asked.

## Development

Python 3.13, managed with [uv](https://docs.astral.sh/uv/) (`src/` layout).

```bash
uv sync                                               # create .venv from the lockfile
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run pytest                                         # tests (AWS mocked, no credentials needed)
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the same lint + tests on every pull request.

## Deployment

Continuous: every push/merge to `develop` triggers [deploy.yml](.github/workflows/deploy.yml) — lint + test, build the image, push it to ECR (commit-SHA + `latest` tags), and roll both Lambdas to the new image. AWS is reached via GitHub OIDC (role `github-actions-chatcheck-deploy`, managed in [infra/](infra/)) — no long-lived keys, no GitHub secrets.

First-time bootstrap is manual:

Prerequisites: AWS CLI (authenticated), Docker, Terraform ≥ 1.6.

### 1. Provide the bot token

Terraform creates both SSM SecureString parameters itself: the bot token comes from a **git-ignored** `infra/terraform.tfvars`, and the webhook secret is generated (`random_password`). Create the tfvars file:

```hcl
# infra/terraform.tfvars — never commit this file
telegram_bot_token = "<YOUR_BOT_TOKEN>"
```

### 2. Create the ECR repo, build, and push

The Lambdas can't be created until an image exists, so target the repo first:

```bash
cd infra
terraform init
terraform apply -target=aws_ecr_repository.bot
cd ..

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
REPO=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/chatcheck-bot

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $REPO
docker build --platform linux/amd64 -t $REPO:latest .
docker push $REPO:latest
```

### 3. Deploy everything else

```bash
cd infra
terraform apply
```

Set `bot_timezone` (e.g. `-var bot_timezone=Europe/Kyiv`) to control both the reminder hour and how "today" is bucketed. Note the `webhook_url` output.

### 4. Register the webhook with Telegram

```bash
cd infra
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -d "url=$(terraform output -raw webhook_url)" \
  -d "secret_token=$(terraform output -raw webhook_secret)" \
  -d 'allowed_updates=["message","callback_query"]'
```

`allowed_updates` keeps Telegram from invoking the Lambda for update types the bot doesn't handle.

After bootstrap, code changes deploy themselves on merge to `develop`. Manual fallback:

```bash
docker build --platform linux/amd64 -t $REPO:latest . && docker push $REPO:latest
aws lambda update-function-code --function-name water_bot_webhook --image-uri $REPO:latest
aws lambda update-function-code --function-name water_bot_cron --image-uri $REPO:latest
```

## Cost

Lambda (1M req/mo), DynamoDB (25 GB), SSM standard parameters, EventBridge Scheduler, and Function URLs are all in the **always-free** tier. The only post-first-year cost is private ECR storage beyond 500 MB — roughly $0.02–0.05/month for this image. Effectively free at low volume.

## Behavior notes

- Users who block the bot are marked `active = false` and skipped on future broadcasts.
- The cron only backfills `ignored` for users registered before yesterday, so brand-new users don't get a phantom `ignored` entry.
- All webhook processing errors return HTTP 200 to Telegram (with the error logged to CloudWatch) so a poison update can't wedge the webhook queue with retries.

## License

[MIT](LICENSE)
