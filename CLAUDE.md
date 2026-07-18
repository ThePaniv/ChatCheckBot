# CLAUDE.md

Guidance for working in this repository.

## What this is

**ChatCheckBot** — a Ukrainian-language serverless Telegram bot ("Water Bot") that
registers users via contact sharing and reminds them to drink water at a **per-user
cadence** they pick (1 min test / 3h / 12h / 24h), logging each answer `yes` / `no` /
`ignored`. Two pathways, both served by the **same Docker image** with different Lambda
entry handlers:

1. **Webhook** (`chatcheck_bot.bot.webhook_handler`): Telegram → Lambda Function URL —
   handles `/start`, contact registration, `/frequency`, the frequency picker, and the
   Так/Ні answer taps.
2. **Tick** (`chatcheck_bot.bot.cron_handler`): EventBridge Scheduler fires it every
   minute; it prompts only users whose `next_check_at <= now`, closes an unanswered
   previous prompt as `ignored`, then advances `next_check_at` by that user's frequency.
3. **Stats** (`chatcheck_bot.bot.stats_handler`): a third Lambda + Function URL serving
   read-only aggregated JSON for a Grafana Cloud "Infinity" datasource. Bearer-token
   auth; `?view=summary|logs|users`. See [README.md](README.md) for the Grafana setup.

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
| `bot.py` | PTB `Application`, Ukrainian text, `FREQUENCIES`, both Lambda handlers (`webhook_handler` + the `cron_handler` tick), one persistent event loop |
| `database.py` | `WaterBotDB`: DynamoDB access — frequency/scheduling on users, per-check logs |

`tests/` mocks all AWS calls (no network, no credentials needed).

## Conventions

- `ruff` formatted, line length 100, target `py313`.
- Config is **env-driven only** (`USERS_TABLE`, `LOGS_TABLE`, `BOT_TOKEN_PARAM`,
  `WEBHOOK_SECRET_PARAM`); secrets live in **SSM SecureStrings**, never in code.
- All user-facing strings are **Ukrainian** (informal «ти»). Keep them in `bot.py`.
- `chatcheck_bot.bot` does AWS work (SSM fetch, DB client) **at import time** on
  purpose — that's the Lambda init phase. Tests stub `boto3` before importing it.

## Gotchas / do-not-break

- **Webhook auth:** every request must present Telegram's
  `X-Telegram-Bot-Api-Secret-Token` header matching the stored secret
  (compared with `hmac.compare_digest`); everything else gets a 403. Don't remove it —
  the Function URL is public.
- **Always return HTTP 200** from the webhook handler, even on processing errors —
  a non-200 makes Telegram retry the same update and a poison message wedges the queue.
- **Callback data carries the `checked_at`** (`water:yes:1752777300`, epoch seconds) so a
  late tap is logged against the right prompt. It's also the `WaterBotLogs` sort key —
  **when the prompt was sent**. Each log row additionally stores `updated_at` (ISO), **when
  the row was written**: the answer tap, or the tick's `ignored` close. The gap between the
  two is the user's response latency (surfaced by the stats endpoint). Answer callbacks match
  `^water:(yes|no):\d+$`; frequency callbacks match `^freq:\d+$`. Keep callback_data ≤ 64 bytes.
- **Stats endpoint auth:** `stats_handler` requires `Authorization: Bearer <stats_token>`
  (SSM `/telegram/stats_token`, compared with `hmac.compare_digest`) — the Function URL is
  public. The token is fetched **lazily** (only in `stats_handler`), so shipping this code
  before the SSM param exists can't crash the webhook/cron cold start.
- **Per-user scheduling:** `next_check_at` (epoch) decides when a user is due; the tick runs
  every minute and must stay fine enough for the shortest frequency (60s). `pending_check`
  holds the id of the current open prompt; it's logged `ignored` when superseded by the next
  tick. An answer is accepted **only** when it matches `pending_check` (conditional clear-then-log
  in `answer_check`), so a tap on an already-ignored or already-answered check is rejected —
  a closed check is never rewritten.
- **One persistent event loop** per Lambda execution environment; never switch back to
  `asyncio.run()` per request (it binds PTB's HTTP client to a dead loop on warm starts).
- Users who block the bot are deactivated (`active=false`), not deleted.

## Deployment

**CD pipeline** ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)): runs on
**every push/merge to `develop`** (or manual dispatch). Lints + tests (the deploy is
gated on that passing), builds the image, pushes it to ECR (`chatcheck-bot`, commit-SHA +
`latest` tags), then `aws lambda update-function-code` on both functions.
([ci.yml](.github/workflows/ci.yml) runs the same lint+test on pull requests.)

AWS is reached with the **`github-cli` IAM user's** access key, mounted as the
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` Actions secrets. The user, its
least-privilege policies (ECR push + Lambda update only), and the key are all managed in
[infra/github-user.tf](infra/github-user.tf); rotate with
`terraform apply -replace=aws_iam_access_key.github_ci` and re-set the two secrets.

Manual deploy steps (first bootstrap included): see [README.md](README.md).

## Infrastructure (Terraform)

Everything AWS lives in [infra/](infra/): ECR repo, DynamoDB tables, all three Lambdas
(webhook + cron + stats) with two Function URLs, EventBridge schedule, the Lambda exec role
(scoped to the two tables and three SSM params), and the `github-cli` CI user. Run Terraform
from `infra/` (AWS profile `claude` = IAM user `claude-cli`). Notes:

- **State is local and git-ignored** — don't commit `*.tfstate` or `.terraform/`.
- **Secret *values* never go in committed code** — the Telegram token lives in
  `infra/terraform.tfvars` (git-ignored) and the webhook secret is a `random_password`;
  both end up in local state only. The repo is public — treat any committed string as leaked.
- First apply bootstraps in two steps (ECR repo → push image → full apply); see
  [infra/README.md](infra/README.md).

## AWS Agent Toolkit (reference)

When doing AWS work here (Terraform, Lambda, DynamoDB, IAM, deploys), consult the vendored
overview [aws_agent_toolkit.md](aws_agent_toolkit.md) — AWS's Agent Toolkit for AI coding
agents (AWS MCP Server, agent skills, plugins, rules files), which gives current AWS docs,
tested procedures, and IAM-scoped API access. It's a **read-only reference**, not something
this project installs or depends on. Source: AWS docs, downloaded 2026-07-18; re-fetch the
URL in that file's header comment to refresh it.
