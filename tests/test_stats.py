"""Read-only stats endpoint tests. AWS is stubbed by conftest before import."""

import json
from decimal import Decimal

import pytest

from chatcheck_bot import bot

# The bearer token comes from the conftest-stubbed SSM param via the bot's own
# lazy getter (avoids importing the non-package tests/conftest module).
STATS_TOKEN = bot._get_stats_token()

# DynamoDB hands numbers back as Decimal; fixtures mirror that so the tests
# exercise the endpoint's Decimal-to-JSON handling.
USERS = [
    {
        "user_id": "1",
        "first_name": "Ann",
        "username": "ann_k",
        "active": True,
        "frequency_seconds": Decimal("10800"),
        "next_check_at": Decimal("1752788100"),
        "pending_check": "1752777300",
    },
    {
        "user_id": "2",
        "first_name": "Bob",
        "active": False,
        "frequency_seconds": Decimal("86400"),
    },
]
LOGS = [
    # checked_at 1752777300 (sent), answered 60s later.
    {
        "user_id": "1",
        "checked_at": "1752777300",
        "status": "yes",
        "updated_at": "2025-07-17T18:36:00+00:00",
    },
    {"user_id": "1", "checked_at": "1752770000", "status": "no", "updated_at": None},
    {"user_id": "2", "checked_at": "1752760000", "status": "ignored"},  # legacy: no updated_at
]


@pytest.fixture(autouse=True)
def stub_scans(monkeypatch):
    monkeypatch.setattr(bot.db, "scan_all_users", lambda: [dict(u) for u in USERS])
    monkeypatch.setattr(bot.db, "scan_all_logs", lambda: [dict(log) for log in LOGS])


def _event(view=None, token=STATS_TOKEN):
    headers = {"authorization": f"Bearer {token}"} if token is not None else {}
    params = {"view": view} if view else None
    return {"headers": headers, "queryStringParameters": params}


def _body(result):
    assert result["statusCode"] == 200
    assert result["headers"]["content-type"] == "application/json"
    return json.loads(result["body"])


def test_rejects_missing_or_wrong_token():
    assert bot.stats_handler(_event(token=None), None)["statusCode"] == 403
    assert bot.stats_handler(_event(token="nope"), None)["statusCode"] == 403


def test_summary_counts_and_response_rate():
    data = _body(bot.stats_handler(_event(), None))  # default view = summary
    assert len(data) == 1
    row = data[0]
    assert row["total_users"] == 2
    assert row["active_users"] == 1
    # user 2 is inactive with no next_check_at → counts as cancelled.
    assert row["cancelled_users"] == 1
    assert row["total_checks"] == 3
    assert (row["yes"], row["no"], row["ignored"]) == (1, 1, 1)
    assert row["answered"] == 2
    assert row["response_rate"] == round(2 / 3, 3)


def test_logs_view_enriches_names_and_computes_latency():
    data = _body(bot.stats_handler(_event("logs"), None))
    by_check = {row["checked_at"]: row for row in data}
    answered = by_check[1752777300]
    assert answered["first_name"] == "Ann"
    assert answered["username"] == "ann_k"
    assert answered["answered_at"] == 1752777360
    assert answered["latency_seconds"] == 60
    # A row with no updated_at (never answered / legacy) carries null latency.
    legacy = by_check[1752760000]
    assert legacy["answered_at"] is None
    assert legacy["latency_seconds"] is None


def test_users_view_serializes_decimals_as_ints():
    data = _body(bot.stats_handler(_event("users"), None))
    ann = next(u for u in data if u["user_id"] == "1")
    assert ann["frequency_seconds"] == 10800
    assert ann["next_check_at"] == 1752788100
    assert ann["active"] is True
    bob = next(u for u in data if u["user_id"] == "2")
    assert bob["active"] is False
    # Optional fields absent on the row come back null, not missing.
    assert bob["next_check_at"] is None
    assert bob["username"] is None
