"""Webhook handler auth tests. AWS is stubbed by conftest before import."""

import base64
import json

import pytest

from chatcheck_bot import bot


@pytest.fixture
def processed(monkeypatch):
    """Replace update processing with a recorder so no network I/O happens."""
    payloads = []

    async def _record(payload):
        payloads.append(payload)

    monkeypatch.setattr(bot, "_process_update", _record)
    return payloads


def test_rejects_missing_or_wrong_secret(processed):
    for headers in ({}, {"x-telegram-bot-api-secret-token": "wrong"}):
        result = bot.webhook_handler({"headers": headers, "body": "{}"}, None)
        assert result["statusCode"] == 403
    assert processed == []


def test_accepts_valid_secret_and_processes_update(processed):
    update = {"update_id": 1}
    event = {
        "headers": {"x-telegram-bot-api-secret-token": bot.WEBHOOK_SECRET},
        "body": json.dumps(update),
    }
    result = bot.webhook_handler(event, None)
    assert result["statusCode"] == 200
    assert processed == [update]


def test_decodes_base64_body(processed):
    update = {"update_id": 2}
    event = {
        "headers": {"x-telegram-bot-api-secret-token": bot.WEBHOOK_SECRET},
        "body": base64.b64encode(json.dumps(update).encode()).decode(),
        "isBase64Encoded": True,
    }
    result = bot.webhook_handler(event, None)
    assert result["statusCode"] == 200
    assert processed == [update]


def test_returns_200_on_poison_update(monkeypatch):
    async def _boom(payload):
        raise RuntimeError("poison")

    monkeypatch.setattr(bot, "_process_update", _boom)
    event = {
        "headers": {"x-telegram-bot-api-secret-token": bot.WEBHOOK_SECRET},
        "body": "{}",
    }
    # A non-200 would make Telegram retry the same update forever.
    assert bot.webhook_handler(event, None)["statusCode"] == 200
