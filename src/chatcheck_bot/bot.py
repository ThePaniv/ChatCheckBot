import asyncio
import base64
import hmac
import json
import logging
import os
import time

import boto3
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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

db = WaterBotDB()

_ssm = boto3.client("ssm")


def _get_parameter(name: str) -> str:
    return _ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


TOKEN = _get_parameter(os.getenv("BOT_TOKEN_PARAM", "/telegram/bot_token"))
WEBHOOK_SECRET = _get_parameter(os.getenv("WEBHOOK_SECRET_PARAM", "/telegram/webhook_secret"))

# Check-in cadences the user can pick, keyed by interval in seconds. Each value
# is (button label, phrase for the confirmation message). The 1-minute option
# exists only for testing the flow end to end.
FREQUENCIES = {
    60: ("1 хвилина (тест)", "щохвилини"),
    3 * 3600: ("Кожні 3 години", "кожні 3 години"),
    12 * 3600: ("Кожні 12 годин", "кожні 12 годин"),
    24 * 3600: ("Кожні 24 години", "кожні 24 години"),
}

PROMPT_TEXT = "Ти вже випив(-ла) достатньо води? 💧"

# Persistent options panel shown once onboarding completes. Tapping a button
# sends its label as a normal text message, routed below to the matching
# handler. The label doubles as the routing key, so it lives in one constant
# referenced by both the markup and the MessageHandler.
BTN_FREQUENCY = "⏰ Змінити частоту"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_FREQUENCY)]],
    resize_keyboard=True,
    is_persistent=True,
)


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=f"freq:{seconds}")]
            for seconds, (label, _) in FREQUENCIES.items()
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Поділитися контактом", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Привіт! Я нагадуватиму тобі пити воду. 💧\n\n"
        "Поділися своїм контактом, щоб завершити реєстрацію.",
        reply_markup=contact_keyboard,
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    sender = update.message.from_user
    # A user can forward someone else's contact card; only accept their own.
    if contact.user_id != sender.id:
        await update.message.reply_text(
            "Будь ласка, поділися своїм власним контактом за допомогою кнопки нижче."
        )
        return
    db.register_user(
        user_id=sender.id,
        chat_id=update.message.chat_id,
        phone=contact.phone_number,
        first_name=contact.first_name or sender.first_name or "",
        username=sender.username,
    )
    await update.message.reply_text("Реєстрацію завершено! ✅", reply_markup=MAIN_MENU)
    await update.message.reply_text("Як часто тобі нагадувати?", reply_markup=_frequency_keyboard())


async def frequency_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Обери, як часто нагадувати:", reply_markup=_frequency_keyboard()
    )


async def handle_frequency_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seconds = int(query.data.split(":")[1])
    choice = FREQUENCIES.get(seconds)
    if choice is None:
        return  # stale/retired button; the tap is already acknowledged
    _, phrase = choice
    # Schedule the first prompt one interval out (so the 1-minute test fires
    # within a minute, and a daily user isn't pinged the instant they choose).
    next_check_at = int(time.time()) + seconds
    if db.set_frequency(query.from_user.id, seconds, next_check_at):
        await query.edit_message_text(f"Готово! Тепер нагадуватиму {phrase}. 💧")
    else:
        # No registered row to update (never shared a contact) — guide them.
        await query.edit_message_text(
            "Спершу надішли /start і поділися контактом, щоб я міг тобі писати. 🙏"
        )


async def handle_water_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # callback_data carries the checked_at of the prompt being answered. Only
    # the current open prompt is accepted; a tap on an already-ignored or
    # already-answered check is rejected so it can't rewrite a closed check.
    _, status, checked_at = query.data.split(":")
    if db.answer_check(query.from_user.id, checked_at, status):
        reply = (
            "Занотовано: води достатньо. Так тримати! 💪"
            if status == "yes"
            else "Занотовано. Саме час випити склянку води! 🥤"
        )
    else:
        reply = "Це нагадування вже застаріле — дочекайся наступного. ⏳"
    await query.edit_message_text(text=reply)


app = Application.builder().token(TOKEN).updater(None).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("frequency", frequency_command))
app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
app.add_handler(MessageHandler(filters.Text([BTN_FREQUENCY]), frequency_command))
app.add_handler(CallbackQueryHandler(handle_frequency_choice, pattern=r"^freq:\d+$"))
app.add_handler(CallbackQueryHandler(handle_water_response, pattern=r"^water:(yes|no):\d+$"))

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


async def _tick():
    await _ensure_initialized()
    now = int(time.time())
    users = db.get_due_users(now)
    logger.info("Tick: %d users due", len(users))
    for user in users:
        user_id = user["user_id"]
        chat_id = user.get("chat_id")
        if chat_id is None:
            # A malformed row (e.g. a frequency somehow set before registration)
            # would crash int() below; skip it rather than wedge the whole tick.
            logger.warning("Due user %s has no chat_id; skipping", user_id)
            continue
        chat_id = int(chat_id)
        frequency = int(user["frequency_seconds"])
        # Close out the previous prompt as ignored if it was never answered.
        pending = user.get("pending_check")
        if pending:
            db.log_check(user_id, str(pending), "ignored")
        checked_at = str(now)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Так 👍", callback_data=f"water:yes:{checked_at}"),
                    InlineKeyboardButton("Ні 👎", callback_data=f"water:no:{checked_at}"),
                ]
            ]
        )
        try:
            await app.bot.send_message(chat_id=chat_id, text=PROMPT_TEXT, reply_markup=keyboard)
            # Advance the schedule only after a confirmed send, inside the same
            # try: if this write fails the user stays due and is retried next
            # tick (at worst a duplicate), never silently dropped mid-batch.
            db.mark_sent(user_id, checked_at, now + frequency)
        except Forbidden:
            logger.info("User %s blocked the bot; deactivating", user_id)
            db.deactivate_user(user_id)
        except Exception:
            # Leave next_check_at due so this user is retried on the next tick.
            logger.exception("Failed to process user %s", user_id)


def cron_handler(event, context):
    _loop.run_until_complete(_tick())
    return {"statusCode": 200, "body": "OK"}
