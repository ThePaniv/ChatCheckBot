import os
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


def _now_iso():
    return datetime.now(UTC).isoformat()


class WaterBotDB:
    def __init__(self):
        dynamodb = boto3.resource("dynamodb")
        self.users_table = dynamodb.Table(os.getenv("USERS_TABLE", "WaterBotUsers"))
        self.logs_table = dynamodb.Table(os.getenv("LOGS_TABLE", "WaterBotLogs"))

    def _conditional_update(self, **kwargs):
        # Run a conditional update_item on the users table, returning the
        # response — or None if the ConditionExpression failed. Every other
        # ClientError propagates. Centralizes the load-bearing exact-code check
        # the callers rely on to tell "condition not met" from a real AWS error.
        try:
            return self.users_table.update_item(**kwargs)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise

    def register_user(self, user_id, chat_id, phone, first_name, username=None):
        # Upsert PROFILE fields only, never scheduling ones. put_item would
        # replace the whole row, so re-running /start on an already-scheduled
        # user would wipe frequency_seconds/next_check_at/pending_check and
        # silently stop their reminders. registered_at/active are seeded only on
        # first write (if_not_exists) so re-registration can't reset them; the
        # user isn't scheduled until they pick a frequency, so that's enough.
        expr = (
            "SET chat_id = :c, phone_number = :p, first_name = :f, "
            "registered_at = if_not_exists(registered_at, :r), "
            "active = if_not_exists(active, :t)"
        )
        values = {
            ":c": int(chat_id),
            ":p": phone,
            ":f": first_name,
            ":r": _now_iso(),
            ":t": True,
        }
        if username:
            expr += ", username = :u"
            values[":u"] = username
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression=expr,
            ExpressionAttributeValues=values,
        )

    def set_frequency(self, user_id, frequency_seconds, next_check_at):
        # Records the cadence, schedules the next prompt, reactivates the user
        # (in case they'd been deactivated), and drops any stale pending prompt
        # so adjusting settings doesn't retroactively log the old one as ignored.
        #
        # update_item is an upsert; the chat_id condition ensures we only touch
        # an already-registered user. Without it, a /frequency tap from someone
        # who never shared a contact would CREATE a chat_id-less row that later
        # crashes the tick. Returns False when there's no registered user to set.
        response = self._conditional_update(
            Key={"user_id": str(user_id)},
            UpdateExpression=(
                "SET frequency_seconds = :f, next_check_at = :n, active = :a REMOVE pending_check"
            ),
            ConditionExpression=Attr("chat_id").exists(),
            ExpressionAttributeValues={
                ":f": int(frequency_seconds),
                ":n": int(next_check_at),
                ":a": True,
            },
        )
        return response is not None

    def cancel_checks(self, user_id):
        # Stop reminders AND mark the user inactive without deleting them: drop
        # the schedule so the tick skips them, clear any open prompt, and flip
        # active to False so dashboards don't count them as active. Conditioned
        # on an existing schedule, so a no-op (unregistered, or already
        # cancelled) returns False. Resume by picking a frequency — set_frequency
        # re-activates and reschedules.
        response = self._conditional_update(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET active = :inactive REMOVE next_check_at, pending_check",
            ExpressionAttributeValues={":inactive": False},
            ConditionExpression=Attr("next_check_at").exists(),
            ReturnValues="ALL_OLD",
        )
        if response is None:
            return False
        # If the latest prompt was still open (unanswered), close it as ignored.
        # The atomic update above already cleared pending_check and returned its
        # old value, so a late answer to it is rejected and can't double-log; if
        # it was already answered, pending_check is absent here.
        self.close_pending(user_id, response.get("Attributes", {}).get("pending_check"))
        return True

    @staticmethod
    def _scan_all(table, **scan_kwargs):
        # Page through an entire table (or a filtered subset), following
        # LastEvaluatedKey until DynamoDB stops handing one back.
        items = []
        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return items
            scan_kwargs["ExclusiveStartKey"] = last_key

    def get_due_users(self, now_epoch):
        # Active, onboarded (has next_check_at) users whose next prompt is due.
        return self._scan_all(
            self.users_table,
            FilterExpression=(
                Attr("active").eq(True)
                & Attr("next_check_at").exists()
                & Attr("next_check_at").lte(int(now_epoch))
            ),
        )

    def scan_all_users(self):
        # Every user row — for the read-only stats/dashboard endpoint.
        return self._scan_all(self.users_table)

    def scan_all_logs(self):
        # Every check-log row — for the read-only stats/dashboard endpoint.
        return self._scan_all(self.logs_table)

    def mark_sent(self, user_id, checked_at, next_check_at):
        # Open the new prompt and advance the schedule — but only if the user is
        # still active (a cancel during the tick's send window sets active=False
        # and must NOT be resurrected). ReturnValues=ALL_OLD hands back the
        # previously-open prompt so the caller can close it as ignored atomically
        # with this pending_check swap: a concurrent answer that already cleared
        # pending_check yields nothing to close (its answer is preserved), and
        # once pending_check has advanced the old id can no longer be answered.
        # Returns the prior pending_check to close, or None (none open, or the
        # user was cancelled concurrently).
        response = self._conditional_update(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET pending_check = :c, next_check_at = :n",
            ConditionExpression=Attr("active").eq(True),
            ExpressionAttributeValues={":c": str(checked_at), ":n": int(next_check_at)},
            ReturnValues="ALL_OLD",
        )
        if response is None:
            return None
        return response.get("Attributes", {}).get("pending_check")

    def deactivate_user(self, user_id):
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET active = :inactive",
            ExpressionAttributeValues={":inactive": False},
        )

    def log_check(self, user_id, checked_at, status):
        # checked_at (epoch seconds) is the sort key and the answer callback's
        # token — it's WHEN THE PROMPT WAS SENT. updated_at is WHEN THE ROW WAS
        # WRITTEN: the moment the user tapped Так/Ні (yes/no), or the moment the
        # tick closed an unanswered prompt as `ignored`. The gap between the two
        # is the user's response latency.
        self.logs_table.put_item(
            Item={
                "user_id": str(user_id),
                "checked_at": str(checked_at),
                "status": status,
                "updated_at": _now_iso(),
            }
        )

    def close_pending(self, user_id, pending):
        # Close a superseded/cancelled open prompt as ignored (no-op if none).
        # Shared by the tick and cancel_checks so the "ignored" close-out and the
        # str() coercion live in one place.
        if pending:
            self.log_check(user_id, str(pending), "ignored")

    def answer_check(self, user_id, checked_at, status):
        # Accept an answer only for the user's CURRENT open prompt. Gate on the
        # pending marker first: if it no longer points at this check, the prompt
        # was already superseded (logged `ignored`) or already answered, so the
        # tap is a stale one — reject it rather than rewrite a closed check.
        # Returns True if the answer was recorded, False if the check is closed.
        response = self._conditional_update(
            Key={"user_id": str(user_id)},
            UpdateExpression="REMOVE pending_check",
            ConditionExpression=Attr("pending_check").eq(str(checked_at)),
        )
        if response is None:
            return False
        self.log_check(user_id, checked_at, status)
        return True
