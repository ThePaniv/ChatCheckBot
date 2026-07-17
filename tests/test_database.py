from unittest import mock

import pytest
from botocore.exceptions import ClientError

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
    # Not scheduled until the user picks a frequency, so they aren't prompted yet.
    assert "next_check_at" not in item


def test_set_frequency_records_schedule_and_clears_pending():
    db, users_table, _ = _make_db()
    result = db.set_frequency(42, 10800, 1752777300)
    assert result is True
    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "42"}
    assert kwargs["ExpressionAttributeValues"] == {
        ":f": 10800,
        ":n": 1752777300,
        ":a": True,
    }
    assert "REMOVE pending_check" in kwargs["UpdateExpression"]
    # Only touch an already-registered user (has chat_id) — never upsert a bare row.
    assert kwargs["ConditionExpression"] is not None


def test_set_frequency_returns_false_for_unregistered_user():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    assert db.set_frequency(999, 60, 1752777300) is False


def test_set_frequency_reraises_other_client_errors():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
    )
    with pytest.raises(ClientError):
        db.set_frequency(42, 60, 1752777300)


def test_get_due_users_paginates_until_no_last_key():
    db, users_table, _ = _make_db()
    users_table.scan.side_effect = [
        {"Items": [{"user_id": "1"}], "LastEvaluatedKey": {"user_id": "1"}},
        {"Items": [{"user_id": "2"}]},
    ]
    users = db.get_due_users(1000)
    assert [u["user_id"] for u in users] == ["1", "2"]
    assert users_table.scan.call_count == 2
    assert users_table.scan.call_args_list[1].kwargs["ExclusiveStartKey"] == {"user_id": "1"}


def test_mark_sent_sets_pending_and_next_check():
    db, users_table, _ = _make_db()
    db.mark_sent(7, "1752777300", 1752788100)
    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "7"}
    assert kwargs["ExpressionAttributeValues"] == {":c": "1752777300", ":n": 1752788100}


def test_log_check_uses_check_id_sort_key():
    db, _, logs_table = _make_db()
    db.log_check(7, "1752777300", "yes")
    item = logs_table.put_item.call_args.kwargs["Item"]
    assert item["user_id"] == "7"
    assert item["check_id"] == "1752777300"
    assert item["status"] == "yes"


def test_answer_check_logs_then_clears_matching_pending():
    db, users_table, logs_table = _make_db()
    db.answer_check(7, "1752777300", "no")
    logs_table.put_item.assert_called_once()
    upd = users_table.update_item.call_args.kwargs
    assert "REMOVE pending_check" in upd["UpdateExpression"]
    assert upd["ConditionExpression"] is not None  # only clear if it's still this check


def test_answer_check_swallows_conditional_failure():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    # A late answer to a superseded prompt must not raise.
    db.answer_check(7, "old-check", "yes")


def test_answer_check_reraises_other_client_errors():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
    )
    with pytest.raises(ClientError):
        db.answer_check(7, "x", "yes")


def test_deactivate_user_sets_inactive():
    db, users_table, _ = _make_db()
    db.deactivate_user(7)
    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "7"}
    assert kwargs["ExpressionAttributeValues"] == {":inactive": False}
