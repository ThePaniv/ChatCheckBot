"""Webhook handler tests.

`chatcheck_bot.bot` fetches SSM parameters and builds the DynamoDB client at
import time (deliberately: that work belongs in the Lambda init phase), so AWS
must be stubbed before the module is imported.
"""

import base64
import json
from unittest import mock

import pytest

_PARAMS = {
    "/telegram/bot_token": "123456:TEST-TOKEN",
    "/telegram/webhook_secret": "test-webhook-secret",
}


def _fake_get_parameter(Name, WithDecryption=True):  # boto3 API casing
    return {"Parameter": {"Value": _PARAMS[Name]}}


with mock.patch("boto3.client") as _client, mock.patch("boto3.resource"):
    _client.return_value.get_parameter.side_effect = _fake_get_parameter
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
        "headers": {"x-telegram-bot-api-secret-token": "test-webhook-secret"},
        "body": json.dumps(update),
    }
    result = bot.webhook_handler(event, None)
    assert result["statusCode"] == 200
    assert processed == [update]


def test_decodes_base64_body(processed):
    update = {"update_id": 2}
    event = {
        "headers": {"x-telegram-bot-api-secret-token": "test-webhook-secret"},
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
        "headers": {"x-telegram-bot-api-secret-token": "test-webhook-secret"},
        "body": "{}",
    }
    # A non-200 would make Telegram retry the same update forever.
    assert bot.webhook_handler(event, None)["statusCode"] == 200
