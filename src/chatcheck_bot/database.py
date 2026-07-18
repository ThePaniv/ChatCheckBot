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

    def register_user(self, user_id, chat_id, phone, first_name, username=None):
        # Registered but not yet scheduled: next_check_at is written only once
        # the user picks a frequency, so an un-onboarded user is never prompted.
        # username is optional — Telegram users aren't required to have one.
        item = {
            "user_id": str(user_id),
            "chat_id": int(chat_id),
            "phone_number": phone,
            "first_name": first_name,
            "registered_at": _now_iso(),
            "active": True,
        }
        if username:
            item["username"] = username
        self.users_table.put_item(Item=item)

    def set_frequency(self, user_id, frequency_seconds, next_check_at):
        # Records the cadence, schedules the next prompt, reactivates the user
        # (in case they'd been deactivated), and drops any stale pending prompt
        # so adjusting settings doesn't retroactively log the old one as ignored.
        #
        # update_item is an upsert; the chat_id condition ensures we only touch
        # an already-registered user. Without it, a /frequency tap from someone
        # who never shared a contact would CREATE a chat_id-less row that later
        # crashes the tick. Returns False when there's no registered user to set.
        try:
            self.users_table.update_item(
                Key={"user_id": str(user_id)},
                UpdateExpression=(
                    "SET frequency_seconds = :f, next_check_at = :n, active = :a "
                    "REMOVE pending_check"
                ),
                ConditionExpression=Attr("chat_id").exists(),
                ExpressionAttributeValues={
                    ":f": int(frequency_seconds),
                    ":n": int(next_check_at),
                    ":a": True,
                },
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

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
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET pending_check = :c, next_check_at = :n",
            ExpressionAttributeValues={
                ":c": str(checked_at),
                ":n": int(next_check_at),
            },
        )

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

    def answer_check(self, user_id, checked_at, status):
        # Accept an answer only for the user's CURRENT open prompt. Gate on the
        # pending marker first: if it no longer points at this check, the prompt
        # was already superseded (logged `ignored`) or already answered, so the
        # tap is a stale one — reject it rather than rewrite a closed check.
        # Returns True if the answer was recorded, False if the check is closed.
        try:
            self.users_table.update_item(
                Key={"user_id": str(user_id)},
                UpdateExpression="REMOVE pending_check",
                ConditionExpression=Attr("pending_check").eq(str(checked_at)),
            )
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        self.log_check(user_id, checked_at, status)
        return True
