import os
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Attr


class WaterBotDB:
    def __init__(self):
        dynamodb = boto3.resource("dynamodb")
        self.users_table = dynamodb.Table(os.getenv("USERS_TABLE", "WaterBotUsers"))
        self.logs_table = dynamodb.Table(os.getenv("LOGS_TABLE", "WaterBotLogs"))

    def register_user(self, user_id, chat_id, phone, first_name):
        self.users_table.put_item(
            Item={
                "user_id": str(user_id),
                "chat_id": int(chat_id),
                "phone_number": phone,
                "first_name": first_name,
                "registered_at": datetime.now(UTC).isoformat(),
                "active": True,
            }
        )

    def get_active_users(self):
        users = []
        scan_kwargs = {"FilterExpression": Attr("active").eq(True)}
        while True:
            response = self.users_table.scan(**scan_kwargs)
            users.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return users
            scan_kwargs["ExclusiveStartKey"] = last_key

    def deactivate_user(self, user_id):
        self.users_table.update_item(
            Key={"user_id": str(user_id)},
            UpdateExpression="SET active = :inactive",
            ExpressionAttributeValues={":inactive": False},
        )

    def log_water(self, user_id, date_str, status):
        self.logs_table.put_item(
            Item={
                "user_id": str(user_id),
                "date": date_str,
                "status": status,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )

    def check_log_exists(self, user_id, date_str):
        response = self.logs_table.get_item(Key={"user_id": str(user_id), "date": date_str})
        return "Item" in response
