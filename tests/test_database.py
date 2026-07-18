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


def test_register_user_upserts_profile_without_touching_schedule():
    db, users_table, _ = _make_db()
    db.register_user(
        user_id=42, chat_id="42", phone="+380000000000", first_name="Ann", username="ann_k"
    )
    # An upsert, not a put_item — put_item replaces the whole row and would wipe
    # frequency_seconds/next_check_at/pending_check when /start is re-run.
    users_table.put_item.assert_not_called()
    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "42"}
    expr = kwargs["UpdateExpression"]
    assert "chat_id = :c" in expr
    assert "username = :u" in expr
    # Scheduling fields are never mentioned, so they survive re-registration.
    assert "next_check_at" not in expr
    assert "frequency_seconds" not in expr
    assert "pending_check" not in expr
    # registered_at / active are seeded only on first write.
    assert "if_not_exists(registered_at" in expr
    assert "if_not_exists(active" in expr
    vals = kwargs["ExpressionAttributeValues"]
    assert vals[":c"] == 42
    assert vals[":u"] == "ann_k"
    assert vals[":t"] is True


def test_register_user_omits_username_when_absent():
    db, users_table, _ = _make_db()
    # Telegram users aren't required to have a username; don't write an empty one.
    db.register_user(user_id=42, chat_id="42", phone="+380000000000", first_name="Ann")
    kwargs = users_table.update_item.call_args.kwargs
    assert "username" not in kwargs["UpdateExpression"]
    assert ":u" not in kwargs["ExpressionAttributeValues"]


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


def test_cancel_checks_closes_open_prompt_as_ignored_then_cancels():
    db, users_table, logs_table = _make_db()
    # The user still had an unanswered prompt open when they cancelled.
    users_table.update_item.return_value = {"Attributes": {"pending_check": "999"}}
    assert db.cancel_checks(42) is True

    kwargs = users_table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"user_id": "42"}
    expr = kwargs["UpdateExpression"]
    assert "REMOVE next_check_at" in expr
    assert "pending_check" in expr
    assert "active = :inactive" in expr
    assert kwargs["ExpressionAttributeValues"] == {":inactive": False}
    # Only cancels when a schedule exists (conditional), so a no-op returns False.
    assert kwargs["ConditionExpression"] is not None
    assert kwargs["ReturnValues"] == "ALL_OLD"
    # The still-open prompt is closed out as ignored, keyed by its old id.
    item = logs_table.put_item.call_args.kwargs["Item"]
    assert item["checked_at"] == "999"
    assert item["status"] == "ignored"


def test_cancel_checks_without_open_prompt_logs_nothing():
    db, users_table, logs_table = _make_db()
    # Latest check already answered (or none) → no pending_check to close.
    users_table.update_item.return_value = {"Attributes": {}}
    assert db.cancel_checks(42) is True
    logs_table.put_item.assert_not_called()


def test_cancel_checks_returns_false_when_nothing_scheduled():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    assert db.cancel_checks(999) is False


def test_cancel_checks_reraises_other_client_errors():
    db, users_table, _ = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
    )
    with pytest.raises(ClientError):
        db.cancel_checks(42)


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


def test_log_check_records_checked_at_and_answer_time():
    db, _, logs_table = _make_db()
    db.log_check(7, "1752777300", "yes")
    item = logs_table.put_item.call_args.kwargs["Item"]
    assert item["user_id"] == "7"
    assert item["checked_at"] == "1752777300"
    assert item["status"] == "yes"
    # updated_at is when the row was written (the answer/close moment) — a
    # distinct timestamp from checked_at (when the prompt was sent).
    assert item["updated_at"]
    assert item["updated_at"] != item["checked_at"]


def test_scan_all_users_and_logs_paginate():
    db, users_table, logs_table = _make_db()
    users_table.scan.side_effect = [
        {"Items": [{"user_id": "1"}], "LastEvaluatedKey": {"user_id": "1"}},
        {"Items": [{"user_id": "2"}]},
    ]
    logs_table.scan.side_effect = [{"Items": [{"user_id": "1", "checked_at": "10"}]}]
    assert [u["user_id"] for u in db.scan_all_users()] == ["1", "2"]
    assert db.scan_all_logs() == [{"user_id": "1", "checked_at": "10"}]
    # Full scans carry no FilterExpression (unlike get_due_users).
    assert "FilterExpression" not in users_table.scan.call_args_list[0].kwargs


def test_answer_check_records_only_the_live_prompt():
    db, users_table, logs_table = _make_db()
    result = db.answer_check(7, "1752777300", "no")
    assert result is True
    # Gate on the pending marker first (must still point at this check)...
    upd = users_table.update_item.call_args.kwargs
    assert "REMOVE pending_check" in upd["UpdateExpression"]
    assert upd["ConditionExpression"] is not None
    # ...then log the answer.
    item = logs_table.put_item.call_args.kwargs["Item"]
    assert item["checked_at"] == "1752777300"
    assert item["status"] == "no"


def test_answer_check_rejects_stale_or_ignored_check_without_logging():
    db, users_table, logs_table = _make_db()
    users_table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )
    # A tap on an already-ignored/answered check: rejected, not logged, no crash.
    assert db.answer_check(7, "old-check", "yes") is False
    logs_table.put_item.assert_not_called()


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
