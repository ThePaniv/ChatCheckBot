# Detailed Implementation Plan: Cost-Optimized Serverless Telegram Bot on AWS

This document outlines the complete architectural design, code structure, and deployment workflow for a Python-based Telegram bot. The bot registers users, collects contact data, and tracks daily water intake logs (Yes / No / Ignored).

> **⚠️ Superseded by the implementation (2026-07-17).** The code in this repo (`bot.py`, `database.py`, `terraform/`, `README.md`) is the source of truth and diverges from this plan in the following reviewed-and-fixed ways:
> - **API Gateway replaced with a Lambda Function URL** — API Gateway's free tier expires after 12 months; Function URLs are free forever and simpler.
> - **Webhook authentication added** via Telegram's `secret_token` / `X-Telegram-Bot-Api-Secret-Token` header (the plan had none — anyone could POST fake updates).
> - **Cron date logic fixed:** yesterday (not today) is marked `ignored` when unanswered, and the target date travels in the callback data so late answers land on the right day.
> - **Contact validation added:** only the sender's own contact card registers them.
> - **IAM scoped** to the specific tables/parameters instead of `dynamodb:*` on `*`; `kms:Decrypt` added (required for SecureString and missing here).
> - **Terraform corrected:** the single-line `attribute` blocks below are invalid HCL, and `${aws_account_id}` was undefined. EventBridge Scheduler (timezone-aware) is used instead of legacy CloudWatch Events.
> - **Paginated DynamoDB scan**, blocked-user deactivation, current library versions (PTB 21.x, Python 3.13), and a persistent event loop instead of per-request `asyncio.run`.
>
> See `README.md` for the working deployment steps.

---

## 1. Cost Optimization & Architecture Analysis

### The Cost Dilemma: Lightsail vs. Serverless Docker
While an AWS Lightsail Nano instance costs **$3.50/month**, we can achieve an architecture that costs **$0.00/month (completely Free Tier indefinitely)** by shifting from a traditional long-polling bot to a **Serverless Webhook architecture** running on AWS Lambda with Docker container images.

| Component | AWS Lightsail Nano (Long-Polling) | AWS Serverless (Webhook + Cron) | Cost (Low-Medium Volume) |
| :--- | :--- | :--- | :--- |
| **Compute** | Lightsail Instance ($3.50/mo fixed) | AWS Lambda (Docker Image) | $0.00 (1M free requests/mo) |
| **Ingress** | Included | Amazon API Gateway (HTTP API) | $0.00 ($1.00/million after free tier) |
| **Database** | Managed DB (~$15/mo) or Local SQLite | Amazon DynamoDB | $0.00 (25 GB storage free forever) |
| **Secrets** | Environment Variables on VPS | AWS SSM Parameter Store | $0.00 (Standard parameters are free) |
| **Cron Engine** | Linux Systemd / Cron | AWS EventBridge Scheduler | $0.00 (1M free invocations/mo) |
| **Total Cost** | **$3.50+ / month** | **$0.00 / month** | **Absolute Cheapest** |

### Optimized Architecture Overview
To run a completely serverless Docker bot, the design splits into two pathways using the **same Docker image** with different entry handlers:
1. **Interactive Inbound (Webhook):** Telegram Server -> API Gateway -> Lambda Handler -> Processes `/start`, contact sharing, and button inputs.
2. **Scheduled Outbound (Daily Cron):** AWS EventBridge Scheduler (Once daily) -> Lambda Handler -> Scans DynamoDB, sends water prompts, and logs uncompleted tasks as 'ignored'.

---

## 2. Database Schema (Amazon DynamoDB)

We will provision two decoupled tables using On-Demand (`PAY_PER_REQUEST`) capacity to maintain zero baseline costs.

### Table 1: `WaterBotUsers`
* **Partition Key (PK):** `user_id` (String) - Unique Telegram ID.
* **Attributes:**
  * `chat_id` (Number)
  * `phone_number` (String)
  * `first_name` (String)
  * `registered_at` (String - ISO Timestamp)

### Table 2: `WaterBotLogs`
* **Partition Key (PK):** `user_id` (String)
* **Sort Key (SK):** `date` (String - format `YYYY-MM-DD`)
* **Attributes:**
  * `status` (String: `yes` | `no` | `ignored`)
  * `updated_at` (String - ISO Timestamp)

---

## 3. Core Python Implementation

### `database.py`
```python
import boto3
from datetime import datetime
import os

class WaterBotDB:
    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        self.users_table = self.dynamodb.Table('WaterBotUsers')
        self.logs_table = self.dynamodb.Table('WaterBotLogs')

    def register_user(self, user_id, chat_id, phone, first_name):
        self.users_table.put_item(Item={
            'user_id': str(user_id),
            'chat_id': chat_id,
            'phone_number': phone,
            'first_name': first_name,
            'registered_at': datetime.utcnow().isoformat()
        })

    def get_all_users(self):
        response = self.users_table.scan()
        return response.get('Items', [])

    def log_water(self, user_id, date_str, status):
        self.logs_table.put_item(Item={
            'user_id': str(user_id),
            'date': date_str,
            'status': status,
            'updated_at': datetime.utcnow().isoformat()
        })

    def check_log_exists(self, user_id, date_str):
        response = self.logs_table.get_item(Key={'user_id': str(user_id), 'date': date_str})
        return 'Item' in response
```

### `bot.py`
```python
import os
import json
import asyncio
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import WaterBotDB

db = WaterBotDB()

def get_bot_token():
    import boto3
    ssm = boto3.client('ssm', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    parameter = ssm.get_parameter(Name='/telegram/bot_token', WithDecryption=True)
    return parameter['Parameter']['Value']

TOKEN = get_bot_token()
app = Application.builder().token(TOKEN).build()

async def start(update: Update, context):
    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Share Contact to Register", request_contact=True)]],
        one_time_keyboard=True, resize_keyboard=True
    )
    await update.message.reply_text(
        "Welcome to the Water Bot! Please share your contact info to finish registration.",
        reply_markup=contact_keyboard
    )

async def handle_contact(update: Update, context):
    contact = update.message.contact
    db.register_user(contact.user_id, update.message.chat_id, contact.phone_number, contact.first_name)
    await update.message.reply_text("Registration successful! Pinging you daily.")

async def handle_inline_buttons(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    status = query.data.split(":")[1]
    date_str = datetime.utcnow().strftime('%Y-%m-%d')
    db.log_water(user_id, date_str, status)
    await query.edit_message_text(text=f"Logged your response: {status.upper()}")

def webhook_handler(event, context):
    async def process():
        body = json.loads(event.get("body", "{}"))
        update = Update.de_json(body, app.bot)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        app.add_handler(CallbackQueryHandler(handle_inline_buttons, pattern="^water:"))
        async with app:
            await app.process_update(update)
    asyncio.run(process())
    return {"statusCode": 200, "body": "OK"}

def cron_handler(event, context):
    async def broadcast():
        users = db.get_all_users()
        yesterday_str = datetime.utcnow().strftime('%Y-%m-%d')
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes", callback_data="water:yes"),
             InlineKeyboardButton("No", callback_data="water:no")]
        ])
        async with app:
            for user in users:
                user_id = user['user_id']
                chat_id = int(user['chat_id'])
                if not db.check_log_exists(user_id, yesterday_str):
                    db.log_water(user_id, yesterday_str, "ignored")
                try:
                    await app.bot.send_message(chat_id=chat_id, text="Did you drink water today?", reply_markup=keyboard)
                except Exception:
                    pass
    asyncio.run(broadcast())
    return {"statusCode": 200, "body": "Broadcast Complete"}
```

---

## 4. Dockerization

```dockerfile
FROM public.ecr.aws/lambda/python:3.11

COPY requirements.txt ${LAMBDA_TASK_ROOT}
COPY bot.py ${LAMBDA_TASK_ROOT}
COPY database.py ${LAMBDA_TASK_ROOT}

RUN pip install --no-cache-dir -r requirements.txt

CMD [ "bot.webhook_handler" ]
```

### `requirements.txt`
```text
python-telegram-bot==20.3
boto3==1.28.0
```

---

## 5. Infrastructure as Code (Terraform)

```hcl
provider "aws" {
  region = "us-east-1"
}

resource "aws_dynamodb_table" "users" {
  name         = "WaterBotUsers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  attribute { name = "user_id"; type = "S" }
}

resource "aws_dynamodb_table" "logs" {
  name         = "WaterBotLogs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "date"
  attribute { name = "user_id"; type = "S" }
  attribute { name = "date"; type = "S" }
}

resource "aws_iam_role" "lambda_exec" {
  name = "telegram_water_bot_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_policy" {
  name = "telegram_water_bot_policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["dynamodb:*"], Resource = "*" },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = "*" },
      { Effect = "Allow", Action = ["logs:*"], Resource = "*" }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

resource "aws_lambda_function" "webhook" {
  function_name = "water_bot_webhook"
  role          = aws_iam_role.lambda_exec.arn
  image_uri     = "${aws_account_id}.dkr.ecr.us-east-1.amazonaws.com/water-bot:latest"
  package_type  = "Image"
  image_config { command = ["bot.webhook_handler"] }
}

resource "aws_lambda_function" "cron" {
  function_name = "water_bot_cron"
  role          = aws_iam_role.lambda_exec.arn
  image_uri     = "${aws_account_id}.dkr.ecr.us-east-1.amazonaws.com/water-bot:latest"
  package_type  = "Image"
  image_config { command = ["bot.cron_handler"] }
}

resource "aws_apigatewayv2_api" "http_api" {
  name          = "telegram_bot_gateway"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "int" {
  api_id             = aws_apigatewayv2_api.http_api.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.webhook.arn
  integration_method = "POST"
}

resource "aws_apigatewayv2_route" "route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.int.id}"
}

resource "aws_lambda_permission" "gw" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "apigateway.amazonaws.com"
}

resource "aws_apigatewayv2_stage" "stage" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_cloudwatch_event_rule" "cron_rule" {
  name                = "water_bot_cron_rule"
  schedule_expression = "cron(0 20 * * ? *)"
}

resource "aws_cloudwatch_event_target" "target" {
  rule = aws_cloudwatch_event_rule.cron_rule.name
  arn  = aws_lambda_function.cron.arn
}

resource "aws_lambda_permission" "cron_perm" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cron.function_name
  principal     = "events.amazonaws.com"
}

output "webhook_url" {
  value = "${aws_apigatewayv2_api.http_api.api_endpoint}/webhook"
}
```

---

## 6. Deployment Workflow

1. **Save Token:** Save your Telegram bot token in AWS Systems Manager (SSM) Parameter Store as a `SecureString` named `/telegram/bot_token`.
2. **Build & Push Docker:** Create your ECR repository named `water-bot`, then build and push your Docker image tagged as `latest`.
3. **Deploy Terraform:** Execute `terraform init` and `terraform apply` to provision all tables, permissions, gateways, and functions.
4. **Register Webhook:** Set your webhook with Telegram by invoking:
   ```bash
   curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_API_GATEWAY_URL>"
   ```
