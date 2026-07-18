import asyncio
import base64
import hmac
import json
import logging
import os
import time
from datetime import datetime
from decimal import Decimal

import boto3
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, Forbidden
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

# The stats bearer token is fetched lazily (only the stats Lambda needs it), so
# the webhook/cron handlers never require this parameter to exist — a deploy
# that ships this code before the param is created can't crash the live bot.
_stats_token = None


def _get_stats_token() -> str:
    global _stats_token
    if _stats_token is None:
        _stats_token = _get_parameter(os.getenv("STATS_TOKEN_PARAM", "/telegram/stats_token"))
    return _stats_token


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
# handler. Each label doubles as its routing key, so it lives in one constant
# referenced by both the markup and the MessageHandler.
BTN_FREQUENCY = "⏰ Змінити частоту"
BTN_CANCEL = "🛑 Скасувати нагадування"


def _frequency_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=f"freq:{seconds}")]
            for seconds, (label, _) in FREQUENCIES.items()
        ]
    )


def _water_keyboard(checked_at: str) -> InlineKeyboardMarkup:
    # checked_at (epoch seconds) rides in the callback data so a late tap is
    # logged against the right prompt. Shared by the tick and the first-check
    # sent immediately on frequency selection.
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Так 👍", callback_data=f"water:yes:{checked_at}"),
                InlineKeyboardButton("Ні 👎", callback_data=f"water:no:{checked_at}"),
            ]
        ]
    )


def _main_menu(with_cancel: bool) -> ReplyKeyboardMarkup:
    # Cancel appears only once the user has a live schedule — there's nothing to
    # cancel before a frequency is picked, or after cancelling.
    row = [KeyboardButton(BTN_FREQUENCY)]
    if with_cancel:
        row.append(KeyboardButton(BTN_CANCEL))
    return ReplyKeyboardMarkup([row], resize_keyboard=True, is_persistent=True)


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
    await update.message.reply_text(
        "Реєстрацію завершено! ✅", reply_markup=_main_menu(with_cancel=False)
    )
    await update.message.reply_text("Як часто тобі нагадувати?", reply_markup=_frequency_keyboard())


async def frequency_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Обери, як часто нагадувати:", reply_markup=_frequency_keyboard()
    )


async def cancel_checks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Stop reminders and mark the user inactive (cancel_checks). They resume by
    # picking a frequency again. Hide the Cancel button now — nothing to cancel.
    if db.cancel_checks(update.effective_user.id):
        text = "Нагадування скасовано. Щоб відновити, обери частоту. 🛑"
    else:
        text = "Активних нагадувань немає. Обери частоту, щоб почати. 💧"
    await update.message.reply_text(text, reply_markup=_main_menu(with_cancel=False))


async def handle_frequency_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    seconds = int(query.data.split(":")[1])
    choice = FREQUENCIES.get(seconds)
    if choice is None:
        return  # stale/retired button; the tap is already acknowledged
    _, phrase = choice
    now = int(time.time())
    # Record the cadence. next_check_at = now is a due-now fallback: if the
    # immediate send below fails for a non-block reason, the tick still prompts
    # within a minute. set_frequency returns False for someone unregistered.
    if not db.set_frequency(query.from_user.id, seconds, now):
        await query.edit_message_text(
            "Спершу надішли /start і поділися контактом, щоб я міг тобі писати. 🙏"
        )
        return
    await query.edit_message_text(f"Готово! Тепер нагадуватиму {phrase}. 💧")
    chat_id = update.effective_chat.id
    # Send the FIRST check right now instead of waiting for the next tick, then
    # advance next_check_at by one interval so the cadence is counted from this
    # first prompt. mark_sent also sets pending_check, so the answer is accepted.
    checked_at = str(now)
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=PROMPT_TEXT, reply_markup=_water_keyboard(checked_at)
        )
    except Forbidden:
        db.deactivate_user(query.from_user.id)
        return
    db.mark_sent(query.from_user.id, checked_at, now + seconds)
    # Now that reminders are scheduled, reveal the Cancel button on the panel.
    await context.bot.send_message(
        chat_id=chat_id,
        text="Керувати нагадуваннями — кнопками внизу. 👇",
        reply_markup=_main_menu(with_cancel=True),
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
app.add_handler(MessageHandler(filters.Text([BTN_CANCEL]), cancel_checks_command))
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
    # Compare on bytes: hmac.compare_digest raises TypeError on a non-ASCII str,
    # which on this attacker-reachable header would escape as a 500, not a 403.
    if not hmac.compare_digest(secret.encode(), WEBHOOK_SECRET.encode()):
        return {"statusCode": 403, "body": "Forbidden"}

    try:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        _loop.run_until_complete(_process_update(json.loads(body)))
    except Exception:
        # Return 200 regardless: a non-200 makes Telegram retry the same update,
        # which would wedge the webhook on a poison message. The base64/utf-8
        # decode is inside the try for the same reason.
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
        # Everything below is inside the try so one bad row or transient write
        # error (a bad frequency_seconds, a DynamoDB blip) can't abort the whole
        # remaining batch — the user is just skipped and retried on the next tick.
        try:
            checked_at = str(now)
            await app.bot.send_message(
                chat_id=int(chat_id), text=PROMPT_TEXT, reply_markup=_water_keyboard(checked_at)
            )
            # Advance the schedule only after a confirmed send. mark_sent swaps
            # pending_check atomically and returns the PRIOR open prompt (when the
            # user is still active); close that one as ignored. Closing via
            # mark_sent's ALL_OLD — not the stale scan value — means a concurrent
            # answer isn't clobbered and a closed check isn't rewritten.
            old_pending = db.mark_sent(user_id, checked_at, now + int(user["frequency_seconds"]))
            db.close_pending(user_id, old_pending)
        except (Forbidden, BadRequest):
            # Blocked, or a permanent bad-chat error (e.g. stale chat_id):
            # deactivate (not delete) so the user stops re-appearing in
            # get_due_users every tick. mark_sent didn't run, so nothing to undo.
            logger.info("User %s unreachable; deactivating", user_id)
            db.deactivate_user(user_id)
        except Exception:
            # Transient error: leave next_check_at due so this user is retried on
            # the next tick, never silently dropped mid-batch.
            logger.exception("Failed to process user %s", user_id)


def cron_handler(event, context):
    _loop.run_until_complete(_tick())
    return {"statusCode": 200, "body": "OK"}


# --- Read-only stats endpoint (for a Grafana Cloud "Infinity" JSON datasource) -
# A third Lambda entry point behind its own Function URL. It scans both tables
# and returns aggregated JSON. Authenticated by a bearer token (Authorization:
# Bearer <token>) compared with hmac.compare_digest, so the public URL doesn't
# leak data. Three views, chosen by ?view=: `summary` (one row of totals),
# `logs` (one row per check, enriched with the user's name), `users` (one row
# per user). Grafana points a query at each URL and charts the JSON.


def _json_default(o):
    # DynamoDB returns numbers as Decimal, which json.dumps can't serialize.
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _iso_to_epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


def _summary_view(users: list[dict], logs: list[dict]) -> list[dict]:
    counts = {"yes": 0, "no": 0, "ignored": 0}
    for log in logs:
        status = log.get("status")
        if status in counts:
            counts[status] += 1
    total = sum(counts.values())
    answered = counts["yes"] + counts["no"]
    return [
        {
            "total_users": len(users),
            "active_users": sum(1 for u in users if u.get("active")),
            # Registered users who cancelled: inactive AND unscheduled. A blocked
            # user is inactive but keeps next_check_at, so this excludes them.
            "cancelled_users": sum(
                1 for u in users if not u.get("active") and u.get("next_check_at") is None
            ),
            "total_checks": total,
            "yes": counts["yes"],
            "no": counts["no"],
            "ignored": counts["ignored"],
            "answered": answered,
            # Share of prompts the user actually answered (yes or no).
            "response_rate": round(answered / total, 3) if total else 0,
        }
    ]


def _logs_view(users: list[dict], logs: list[dict]) -> list[dict]:
    names = {u["user_id"]: u for u in users}
    rows = []
    for log in logs:
        checked_at = int(log["checked_at"])
        answered_at = _iso_to_epoch(log.get("updated_at"))
        user = names.get(log["user_id"], {})
        rows.append(
            {
                "user_id": log["user_id"],
                "first_name": user.get("first_name"),
                "username": user.get("username"),
                "status": log["status"],
                "checked_at": checked_at,  # prompt sent (epoch seconds)
                "updated_at": log.get("updated_at"),  # answered/closed (ISO)
                "answered_at": answered_at,  # same, as epoch seconds
                "latency_seconds": (answered_at - checked_at) if answered_at else None,
            }
        )
    return rows


def _users_view(users: list[dict]) -> list[dict]:
    def as_int(value):
        return int(value) if value is not None else None

    return [
        {
            "user_id": u["user_id"],
            "first_name": u.get("first_name"),
            "username": u.get("username"),
            "active": bool(u.get("active")),
            "frequency_seconds": as_int(u.get("frequency_seconds")),
            "next_check_at": as_int(u.get("next_check_at")),
            "pending_check": u.get("pending_check"),
        }
        for u in users
    ]


def stats_handler(event, context):
    headers = event.get("headers") or {}
    auth = headers.get("authorization", "")
    # Byte comparison: a non-ASCII Authorization header would make the str form
    # of compare_digest raise TypeError (a 500) instead of returning 403.
    if not hmac.compare_digest(auth.encode(), f"Bearer {_get_stats_token()}".encode()):
        return {"statusCode": 403, "body": "Forbidden"}

    params = event.get("queryStringParameters") or {}
    view = params.get("view", "summary")

    users = db.scan_all_users()
    if view == "users":
        data = _users_view(users)
    elif view == "logs":
        data = _logs_view(users, db.scan_all_logs())
    else:  # summary
        data = _summary_view(users, db.scan_all_logs())

    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(data, default=_json_default),
    }
