import asyncio
import base64
import hmac
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from chatcheck_bot.database import WaterBotDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TZ = ZoneInfo(os.getenv("BOT_TZ", "UTC"))

db = WaterBotDB()

_ssm = boto3.client("ssm")


def _get_parameter(name: str) -> str:
    return _ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


TOKEN = _get_parameter(os.getenv("BOT_TOKEN_PARAM", "/telegram/bot_token"))
WEBHOOK_SECRET = _get_parameter(os.getenv("WEBHOOK_SECRET_PARAM", "/telegram/webhook_secret"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Share Contact to Register", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Welcome to the Water Bot! Please share your contact info to finish registration.",
        reply_markup=contact_keyboard,
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    sender = update.message.from_user
    # A user can forward someone else's contact card; only accept their own.
    if contact.user_id != sender.id:
        await update.message.reply_text("Please share your own contact using the button below.")
        return
    db.register_user(
        user_id=sender.id,
        chat_id=update.message.chat_id,
        phone=contact.phone_number,
        first_name=contact.first_name or sender.first_name or "",
    )
    await update.message.reply_text(
        "Registration successful! I'll check in with you daily.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_water_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # callback_data carries the date the question was asked about, so answers
    # sent after midnight still land on the correct day.
    _, status, date_str = query.data.split(":")
    db.log_water(query.from_user.id, date_str, status)
    reply = (
        f"Logged for {date_str}: you drank water. Nice!"
        if status == "yes"
        else f"Logged for {date_str}: no water. Tomorrow is a new day!"
    )
    await query.edit_message_text(text=reply)


app = Application.builder().token(TOKEN).updater(None).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
app.add_handler(
    CallbackQueryHandler(handle_water_response, pattern=r"^water:(yes|no):\d{4}-\d{2}-\d{2}$")
)

# One persistent event loop per Lambda execution environment. The Application
# is initialized once and reused across warm invocations; re-running
# asyncio.run() per request would bind PTB's HTTP client to a dead loop.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_initialized = False


async def _ensure_initialized():
    global _initialized
    if not _initialized:
        await app.initialize()
        _initialized = True


async def _process_update(payload: dict):
    await _ensure_initialized()
    update = Update.de_json(payload, app.bot)
    await app.process_update(update)


def webhook_handler(event, context):
    headers = event.get("headers") or {}
    secret = headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(secret, WEBHOOK_SECRET):
        return {"statusCode": 403, "body": "Forbidden"}

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    try:
        _loop.run_until_complete(_process_update(json.loads(body)))
    except Exception:
        # Return 200 regardless: a non-200 makes Telegram retry the same
        # update, which would wedge the webhook on a poison message.
        logger.exception("Failed to process update")
    return {"statusCode": 200, "body": "OK"}


async def _broadcast():
    await _ensure_initialized()
    now = datetime.now(BOT_TZ)
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Yes", callback_data=f"water:yes:{today_str}"),
                InlineKeyboardButton("No", callback_data=f"water:no:{today_str}"),
            ]
        ]
    )
    users = db.get_active_users()
    logger.info("Broadcasting to %d users", len(users))
    for user in users:
        user_id = user["user_id"]
        chat_id = int(user["chat_id"])
        # Close out yesterday's prompt as ignored if it was never answered.
        # Skip users registered today, who never received it.
        registered_date = str(user.get("registered_at", ""))[:10]
        if registered_date <= yesterday_str and not db.check_log_exists(user_id, yesterday_str):
            db.log_water(user_id, yesterday_str, "ignored")
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text="Did you drink enough water today?",
                reply_markup=keyboard,
            )
        except Forbidden:
            logger.info("User %s blocked the bot; deactivating", user_id)
            db.deactivate_user(user_id)
        except Exception:
            logger.exception("Failed to message user %s", user_id)


def cron_handler(event, context):
    _loop.run_until_complete(_broadcast())
    return {"statusCode": 200, "body": "Broadcast complete"}
