# ChatCheckBot — Serverless Telegram Water Bot on AWS

A Ukrainian-language Telegram bot that registers users via contact sharing and reminds them to drink water at a **cadence each user picks** (1 minute — test only, 3 / 12 / 24 hours), logging each answer as yes / no / ignored. Runs entirely on always-free-tier-friendly serverless components, deployed by CI on every merge.

## Architecture

- **Webhook path:** Telegram → **Lambda Function URL** → `chatcheck_bot.bot.webhook_handler` — handles `/start`, contact registration, `/frequency`, a pinned «⏰ Змінити частоту» menu button, the frequency picker, and the Так/Ні answer taps. (Function URLs are free forever; API Gateway's free tier expires after 12 months.)
- **Tick path:** **EventBridge Scheduler** fires `chatcheck_bot.bot.cron_handler` every minute (`rate(1 minute)`). Each tick prompts only the users whose next check is **due** (`next_check_at <= now`), closes out an unanswered previous prompt as `ignored`, and advances `next_check_at` by that user's frequency.
- Both handlers ship in the **same Docker image**; each Lambda overrides the entrypoint via `image_config.command`.
- **DynamoDB** (on-demand): `WaterBotUsers` (PK `user_id`; holds `frequency_seconds`, `next_check_at`, `pending_check`, and the Telegram `username`) and `WaterBotLogs` (PK `user_id`, SK `checked_at` — one row per prompt; `checked_at` is the send-time epoch, and `updated_at` (ISO) records when the row was written — the answer tap, or the tick closing it as `ignored`).
- **Stats path:** a third Lambda (`chatcheck_bot.bot.stats_handler`) behind its own **Function URL** serves read-only aggregated JSON for a **Grafana Cloud** dashboard (see [Dashboard](#dashboard)). Bearer-token auth; the same image as the other two handlers.
- **SSM Parameter Store** (SecureString): bot token and webhook secret.
- Webhook requests are authenticated by comparing Telegram's `X-Telegram-Bot-Api-Secret-Token` header against the stored secret; everything else gets a 403.

Answers carry the prompt's `checked_at` in the callback data (`water:yes:1752777300`), so a late tap is logged against the right check even after newer prompts have gone out.

## Development

Python 3.13, managed with [uv](https://docs.astral.sh/uv/) (`src/` layout).

```bash
uv sync                                               # create .venv from the lockfile
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run pytest                                         # tests (AWS mocked, no credentials needed)
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the same lint + tests on every pull request.

## Deployment

Continuous: every push/merge to `develop` triggers [deploy.yml](.github/workflows/deploy.yml) — lint + test, build the image, push it to ECR (commit-SHA + `latest` tags), and roll both Lambdas to the new image. AWS is reached with the dedicated `github-cli` IAM user (least-privilege: ECR push + Lambda update only), whose Terraform-generated access key is mounted as the `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` Actions secrets.

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
REGION=eu-central-1
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

Note the `webhook_url` output. The check cadence is per-user (chosen in-chat); the EventBridge tick that drives it is `tick_schedule` (default `rate(1 minute)`).

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

## Dashboard

Bot activity is visualized in **Grafana Cloud (free tier)** via the read-only stats
endpoint — an extra Lambda + Function URL that scans both tables and returns aggregated
JSON. No new AWS running cost (a handful of extra DynamoDB scans); Grafana Cloud's free
tier hosts and shares the dashboard.

The endpoint (Terraform output `stats_url`) takes a `view` query parameter and requires a
bearer token (output `stats_token`):

```bash
cd infra
STATS_URL=$(terraform output -raw stats_url)
STATS_TOKEN=$(terraform output -raw stats_token)

curl -H "Authorization: Bearer $STATS_TOKEN" "${STATS_URL}?view=summary"
```

| `?view=` | Shape | Use |
|---|---|---|
| `summary` (default) | one row of totals | total/active users, yes/no/ignored counts, response rate |
| `logs` | one row per check | time series of answers; `checked_at` (sent), `answered_at`, `latency_seconds`, joined user name |
| `users` | one row per user | per-user frequency, next check, active flag |

### Wire it into Grafana Cloud

1. Create a free account at [grafana.com](https://grafana.com/) and open your stack.
2. **Connections → Add new connection → Infinity** (install the plugin if prompted), then
   add an **Infinity** datasource. Under **Authentication**, add an HTTP header
   `Authorization` = `Bearer <stats_token>` (or use the Bearer-token auth field) so the
   token is stored in Grafana, not in every panel.
3. Add a panel; datasource **Infinity**, type **JSON**, method **GET**, URL
   `<stats_url>?view=summary` (or `logs` / `users`). Set **Parsing options → Rows/Root** to
   the array root and let it infer columns.
   - **Stat** panels off `summary` for the headline numbers.
   - A **Time series** off `logs`: set the time field to `checked_at` (format **Unix ns/s**
     → seconds), group/count by `status`.
   - A **Table** off `users` for the per-user list.
4. **Share:** with **signed-in members of your Grafana org** (Dashboard → **Share → Link**).
   Avoid a *public* dashboard for the `logs`/`users` panels — see the warning below.

Rather than building the panels by hand, import the ready-made dashboard in
[grafana/dashboard.json](grafana/dashboard.json) — see [grafana/README.md](grafana/README.md).

> ⚠️ A **public** dashboard makes whatever it displays world-readable. The `summary` view is
> anonymized (counts only); the `logs`/`users` views include first names and usernames. Keep
> per-user panels off any dashboard you publish publicly, or share it only with signed-in
> members of your Grafana org instead.

## Cost

Lambda (1M req/mo), DynamoDB (25 GB), SSM standard parameters, EventBridge Scheduler, and Function URLs are all in the **always-free** tier. The only post-first-year cost is private ECR storage beyond 500 MB — roughly $0.02–0.05/month for this image. Effectively free at low volume.

## Behavior notes

- Users register, then pick a reminder frequency (or change it later with `/frequency`). Nobody is prompted until they've chosen one.
- Users who block the bot are marked `active = false` and skipped on future ticks.
- Each tick marks a user's previous prompt `ignored` only if it went unanswered before the next one is due, so there are no phantom entries. Once a prompt is superseded (ignored) or answered it's **closed** — tapping its old buttons is rejected with a "no longer active" reply and never rewrites the record.
- All webhook processing errors return HTTP 200 to Telegram (with the error logged to CloudWatch) so a poison update can't wedge the webhook queue with retries.

## License

[MIT](LICENSE)
