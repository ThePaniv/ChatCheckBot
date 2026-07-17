from unittest import mock

from chatcheck_bot.database import WaterBotDB


def _make_db():
    with mock.patch("chatcheck_bot.database.boto3") as boto3_mock:
        users_table = mock.Mock(name="users_table")
        logs_table = mock.Mock(name="logs_table")
        boto3_mock.resource.return_value.Table.side_effect = [users_table, logs_table]
        db = WaterBotDB()
    return db, users_table, logs_table


def test_register_user_stores_stringified_id_and_active_flag():
    db, users_table, _ = _make_db()
    db.register_user(user_id=42, chat_id="42", phone="+380000000000", first_name="Ann")
    item = users_table.put_item.call_args.kwargs["Item"]
    assert item["user_id"] == "42"
    assert item["chat_id"] == 42
    assert item["active"] is True
    assert item["registered_at"]  # ISO timestamp present


def test_get_active_users_paginates_until_no_last_key():
    db, users_table, _ = _make_db()
    users_table.scan.side_effect = [
        {"Items": [{"user_id": "1"}], "LastEvaluatedKey": {"user_id": "1"}},
        {"Items": [{"user_id": "2"}]},
    ]
    users = db.get_active_users()
    assert [u["user_id"] for u in users] == ["1", "2"]
    assert users_table.scan.call_count == 2
    second_call = users_table.scan.call_args_list[1].kwargs
    assert second_call["ExclusiveStartKey"] == {"user_id": "1"}


def test_deactivate_user_sets_inactive():
    db, users_table, _ = _make_db()
    db.deactivate_user(7)
    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "7"}
    assert kwargs["ExpressionAttributeValues"] == {":inactive": False}


def test_check_log_exists():
    db, _, logs_table = _make_db()
    logs_table.get_item.return_value = {"Item": {"status": "yes"}}
    assert db.check_log_exists(1, "2026-07-17") is True
    logs_table.get_item.return_value = {}
    assert db.check_log_exists(1, "2026-07-17") is False
