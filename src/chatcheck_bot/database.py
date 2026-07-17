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

    def register_user(self, user_id, chat_id, phone, first_name):
        # Registered but not yet scheduled: next_check_at is written only once
        # the user picks a frequency, so an un-onboarded user is never prompted.
        self.users_table.put_item(
            Item={
                "user_id": str(user_id),
                "chat_id": int(chat_id),
                "phone_number": phone,
                "first_name": first_name,
                "registered_at": _now_iso(),
                "active": True,
            }
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

    def get_due_users(self, now_epoch):
        # Active, onboarded (has next_check_at) users whose next prompt is due.
        users = []
        scan_kwargs = {
            "FilterExpression": (
                Attr("active").eq(True)
                & Attr("next_check_at").exists()
                & Attr("next_check_at").lte(int(now_epoch))
            )
        }
        while True:
            response = self.users_table.scan(**scan_kwargs)
            users.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return users
            scan_kwargs["ExclusiveStartKey"] = last_key

    def mark_sent(self, user_id, check_id, next_check_at):
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET pending_check = :c, next_check_at = :n",
            ExpressionAttributeValues={
                ":c": str(check_id),
                ":n": int(next_check_at),
            },
        )

    def deactivate_user(self, user_id):
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET active = :inactive",
            ExpressionAttributeValues={":inactive": False},
        )

    def log_check(self, user_id, check_id, status):
        self.logs_table.put_item(
            Item={
                "user_id": str(user_id),
                "check_id": str(check_id),
                "status": status,
                "updated_at": _now_iso(),
            }
        )

    def answer_check(self, user_id, check_id, status):
        self.log_check(user_id, check_id, status)
        # Clear the pending marker only if it still points at this check, so a
        # late answer to a superseded prompt doesn't wipe a newer pending one.
        try:
            self.users_table.update_item(
                Key={"user_id": str(user_id)},
                UpdateExpression="REMOVE pending_check",
                ConditionExpression=Attr("pending_check").eq(str(check_id)),
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
