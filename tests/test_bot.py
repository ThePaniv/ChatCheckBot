"""Frequency-picker and tick tests. AWS is stubbed by conftest before import."""

import asyncio
from unittest import mock

import pytest
from telegram.error import Forbidden

from chatcheck_bot import bot


def test_frequencies_match_the_offered_options():
    assert set(bot.FREQUENCIES) == {60, 3 * 3600, 12 * 3600, 24 * 3600}


def test_frequency_keyboard_callback_data():
    markup = bot._frequency_keyboard()
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert set(datas) == {"freq:60", "freq:10800", "freq:43200", "freq:86400"}


def test_answer_callback_data_within_telegram_limit():
    # water:<yesno>:<10-digit epoch> must stay under Telegram's 64-byte cap.
    assert len("water:no:9999999999") <= 64


def test_main_menu_hides_cancel_until_scheduled():
    without = bot._main_menu(with_cancel=False)
    assert [b.text for row in without.keyboard for b in row] == [bot.BTN_FREQUENCY]
    with_cancel = bot._main_menu(with_cancel=True)
    assert [b.text for row in with_cancel.keyboard for b in row] == [
        bot.BTN_FREQUENCY,
        bot.BTN_CANCEL,
    ]
    assert with_cancel.is_persistent is True
    assert with_cancel.resize_keyboard is True


def _routed_message_handlers(callback):
    return [
        h
        for h in bot.app.handlers[0]
        if isinstance(h, bot.MessageHandler) and h.callback is callback
    ]


def test_menu_button_routes_to_frequency_command():
    # The reply-keyboard button sends its label as text; that text must reach
    # frequency_command and nothing else.
    routed = _routed_message_handlers(bot.frequency_command)
    assert len(routed) == 1
    match = routed[0].filters
    assert match.filter(mock.Mock(text=bot.BTN_FREQUENCY))
    assert not match.filter(mock.Mock(text=bot.BTN_CANCEL))


def test_cancel_button_routes_to_cancel_command():
    routed = _routed_message_handlers(bot.cancel_checks_command)
    assert len(routed) == 1
    match = routed[0].filters
    assert match.filter(mock.Mock(text=bot.BTN_CANCEL))
    assert not match.filter(mock.Mock(text=bot.BTN_FREQUENCY))


def test_frequency_choice_sends_first_check_immediately(monkeypatch):
    # Picking a frequency prompts the user right away (not on the next tick),
    # and schedules the next check one interval after this first prompt.
    fake_db = mock.Mock()
    fake_db.set_frequency.return_value = True
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000_000)

    query = mock.Mock()
    query.data = "freq:10800"
    query.from_user.id = 42
    query.answer = mock.AsyncMock()
    query.edit_message_text = mock.AsyncMock()
    update = mock.Mock(callback_query=query)
    update.effective_chat.id = 555
    context = mock.Mock()
    context.bot.send_message = mock.AsyncMock()

    asyncio.run(bot.handle_frequency_choice(update, context))

    calls = context.bot.send_message.await_args_list
    assert len(calls) == 2
    # First: the immediate check prompt, to the user's chat, water keyboard.
    check = calls[0].kwargs
    assert check["chat_id"] == 555
    datas = [b.callback_data for row in check["reply_markup"].inline_keyboard for b in row]
    assert datas == ["water:yes:1000000", "water:no:1000000"]
    # Second: panel refresh exposing the Cancel button now that a schedule exists.
    panel = calls[1].kwargs["reply_markup"]
    assert bot.BTN_CANCEL in [b.text for row in panel.keyboard for b in row]
    # Next check scheduled one interval (10800s) after this first prompt.
    fake_db.mark_sent.assert_called_once_with(42, "1000000", 1_000_000 + 10800)


def test_frequency_choice_unregistered_user_is_not_prompted(monkeypatch):
    # No registered row (never shared a contact) → guidance message, no prompt.
    fake_db = mock.Mock()
    fake_db.set_frequency.return_value = False
    monkeypatch.setattr(bot, "db", fake_db)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000_000)

    query = mock.Mock()
    query.data = "freq:60"
    query.from_user.id = 7
    query.answer = mock.AsyncMock()
    query.edit_message_text = mock.AsyncMock()
    update = mock.Mock(callback_query=query)
    context = mock.Mock()
    context.bot.send_message = mock.AsyncMock()

    asyncio.run(bot.handle_frequency_choice(update, context))

    context.bot.send_message.assert_not_awaited()
    fake_db.mark_sent.assert_not_called()


def test_cancel_command_stops_reminders(monkeypatch):
    fake_db = mock.Mock()
    fake_db.cancel_checks.return_value = True
    monkeypatch.setattr(bot, "db", fake_db)

    update = mock.Mock()
    update.effective_user.id = 42
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(bot.cancel_checks_command(update, None))

    fake_db.cancel_checks.assert_called_once_with(42)
    update.message.reply_text.assert_awaited_once()
    assert "скасовано" in update.message.reply_text.await_args.args[0].lower()


def test_cancel_command_when_nothing_scheduled(monkeypatch):
    fake_db = mock.Mock()
    fake_db.cancel_checks.return_value = False
    monkeypatch.setattr(bot, "db", fake_db)

    update = mock.Mock()
    update.effective_user.id = 42
    update.message.reply_text = mock.AsyncMock()

    asyncio.run(bot.cancel_checks_command(update, None))

    update.message.reply_text.assert_awaited_once()
    assert "немає" in update.message.reply_text.await_args.args[0].lower()


@pytest.fixture
def stub_bot(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(bot, "_ensure_initialized", _noop)
    fake_db = mock.Mock()
    monkeypatch.setattr(bot, "db", fake_db)
    # PTB's ExtBot forbids setting attributes, so swap the whole Application
    # (the tick only touches app.bot.send_message).
    send = mock.AsyncMock()
    fake_app = mock.Mock()
    fake_app.bot.send_message = send
    monkeypatch.setattr(bot, "app", fake_app)
    return fake_db, send


def test_tick_prompts_due_user_and_marks_sent(stub_bot):
    fake_db, send = stub_bot
    fake_db.get_due_users.return_value = [{"user_id": "1", "chat_id": 111, "frequency_seconds": 60}]
    asyncio.run(bot._tick())

    send.assert_awaited_once()
    assert send.await_args.kwargs["chat_id"] == 111
    datas = [
        b.callback_data
        for row in send.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert all(d.startswith("water:") for d in datas)
    fake_db.mark_sent.assert_called_once()
    fake_db.log_check.assert_not_called()  # nothing pending to close out


def test_tick_closes_unanswered_pending_as_ignored(stub_bot):
    fake_db, _ = stub_bot
    fake_db.get_due_users.return_value = [
        {"user_id": "1", "chat_id": 111, "frequency_seconds": 60, "pending_check": "999"}
    ]
    asyncio.run(bot._tick())
    fake_db.log_check.assert_called_once_with("1", "999", "ignored")
    fake_db.mark_sent.assert_called_once()


def test_tick_deactivates_blocked_user_and_does_not_mark_sent(stub_bot):
    fake_db, send = stub_bot
    send.side_effect = Forbidden("blocked")
    fake_db.get_due_users.return_value = [{"user_id": "1", "chat_id": 111, "frequency_seconds": 60}]
    asyncio.run(bot._tick())
    fake_db.deactivate_user.assert_called_once_with("1")
    fake_db.mark_sent.assert_not_called()


def test_tick_keeps_due_on_transient_send_error(stub_bot):
    fake_db, send = stub_bot
    send.side_effect = RuntimeError("telegram hiccup")
    fake_db.get_due_users.return_value = [{"user_id": "1", "chat_id": 111, "frequency_seconds": 60}]
    asyncio.run(bot._tick())
    # Not marked sent → next_check_at stays due → retried next tick.
    fake_db.mark_sent.assert_not_called()
    fake_db.deactivate_user.assert_not_called()


def test_tick_skips_malformed_row_without_crashing(stub_bot):
    fake_db, send = stub_bot
    # A row with no chat_id must not wedge the whole tick (poison-row guard).
    fake_db.get_due_users.return_value = [
        {"user_id": "bad", "frequency_seconds": 60},
        {"user_id": "2", "chat_id": 222, "frequency_seconds": 60},
    ]
    asyncio.run(bot._tick())  # must not raise
    # The healthy user after the bad row is still processed.
    send.assert_awaited_once()
    assert send.await_args.kwargs["chat_id"] == 222
