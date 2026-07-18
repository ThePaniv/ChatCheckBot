"""Shared test setup.

`chatcheck_bot.bot` fetches SSM parameters and builds a DynamoDB client at
import time (deliberately: that's the Lambda init phase), so AWS must be stubbed
before any test module imports it. Patching here in conftest guarantees the stub
is active during collection, whichever test module is imported first.
"""

from unittest import mock

WEBHOOK_SECRET = "test-webhook-secret"
STATS_TOKEN = "test-stats-token"
_PARAMS = {
    "/telegram/bot_token": "123456:TEST-TOKEN",
    "/telegram/webhook_secret": WEBHOOK_SECRET,
    "/telegram/stats_token": STATS_TOKEN,
}


def _fake_get_parameter(Name, WithDecryption=True):  # boto3 API casing
    return {"Parameter": {"Value": _PARAMS[Name]}}


# Started for the whole session; never stopped (the process exits at the end).
_client = mock.patch("boto3.client").start()
mock.patch("boto3.resource").start()
_client.return_value.get_parameter.side_effect = _fake_get_parameter
