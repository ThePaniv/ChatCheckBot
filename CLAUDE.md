# CLAUDE.md

Guidance for working in this repository.

## What this is

**ChatCheckBot** — a serverless Telegram bot ("Water Bot") that registers users via
contact sharing and tracks daily water intake (`yes` / `no` / `ignored`). Two pathways,
both served by the **same Docker image** with different Lambda entry handlers:

1. **Webhook** (`chatcheck_bot.bot.webhook_handler`): Telegram → Lambda Function URL —
   handles `/start`, contact registration, and Yes/No button taps.
2. **Daily cron** (`chatcheck_bot.bot.cron_handler`): EventBridge Scheduler → marks
   *yesterday* `ignored` for anyone who never answered, then sends today's prompt.

## Tech stack

Python **3.13**, managed with **uv** (`pyproject.toml` + `uv.lock`, `src/` layout).
Key deps: `python-telegram-bot` 21.x, `boto3`. Tooling: `ruff`, `pytest`, Docker, Terraform.
AWS: Lambda (container image), DynamoDB, SSM Parameter Store, EventBridge Scheduler, ECR.

## Commands

```bash
uv sync                                               # create .venv from the lockfile
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run pytest                                         # tests (AWS is mocked; no network)
docker build -t chatcheck-bot .                       # build the Lambda image
```

## Architecture (`src/chatcheck_bot/`)

| File | Role |
|---|---|
| `bot.py` | PTB `Application`, both Lambda handlers, one persistent event loop |
| `database.py` | `WaterBotDB`: DynamoDB access (users + daily logs) |

`tests/` mocks all AWS calls (no network, no credentials needed).

## Conventions

- `ruff` formatted, line length 100, target `py313`.
- Config is **env-driven only** (`USERS_TABLE`, `LOGS_TABLE`, `BOT_TOKEN_PARAM`,
  `WEBHOOK_SECRET_PARAM`, `BOT_TZ`); secrets live in **SSM SecureStrings**, never in code.
- `chatcheck_bot.bot` does AWS work (SSM fetch, DB client) **at import time** on
  purpose — that's the Lambda init phase. Tests stub `boto3` before importing it.

## Gotchas / do-not-break

- **Webhook auth:** every request must present Telegram's
  `X-Telegram-Bot-Api-Secret-Token` header matching the stored secret
  (compared with `hmac.compare_digest`); everything else gets a 403. Don't remove it —
  the Function URL is public.
- **Always return HTTP 200** from the webhook handler, even on processing errors —
  a non-200 makes Telegram retry the same update and a poison message wedges the queue.
- **Callback data carries the date** (`water:yes:2026-07-17`) so answers after midnight
  land on the day the question was asked. Don't recompute the date server-side.
- **One persistent event loop** per Lambda execution environment; never switch back to
  `asyncio.run()` per request (it binds PTB's HTTP client to a dead loop on warm starts).
- The cron only backfills `ignored` for users registered before yesterday, so new users
  don't get a phantom entry. Users who block the bot are deactivated, not deleted.

## Deployment

**CD pipeline** ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)): runs on
**every push/merge to `develop`** (or manual dispatch). Lints + tests (the deploy is
gated on that passing), builds the image, pushes it to ECR (`chatcheck-bot`, commit-SHA +
`latest` tags), then `aws lambda update-function-code` on both functions.
([ci.yml](.github/workflows/ci.yml) runs the same lint+test on pull requests.)

**No GitHub secrets.** AWS is reached by assuming `github-actions-chatcheck-deploy` via
GitHub OIDC (the role ARN is hardcoded in the workflow — an ARN isn't sensitive; the
trust policy only allows this repo's `develop` ref).

Manual deploy steps (first bootstrap included): see [README.md](README.md).

## Infrastructure (Terraform)

Everything AWS lives in [infra/](infra/): ECR repo, DynamoDB tables, both Lambdas +
Function URL, EventBridge schedule, the Lambda exec role (scoped to the two tables and
two SSM params), and the OIDC deploy role. Run Terraform from `infra/`. Notes:

- **State is local and git-ignored** — don't commit `*.tfstate` or `.terraform/`.
- **Secret *values* never go in committed code** — the Telegram token lives in
  `infra/terraform.tfvars` (git-ignored) and the webhook secret is a `random_password`;
  both end up in local state only. The repo is public — treat any committed string as leaked.
- The account-wide GitHub OIDC **provider** is owned by VuDrochkaBot's Terraform and
  referenced here via a data source — don't create a second one.
- First apply bootstraps in two steps (ECR repo → push image → full apply); see
  [infra/README.md](infra/README.md).
