from __future__ import annotations

from typing import Any

import pytest

from app.agent.tools import READ_TOOLS, TOOLS, WRITE_TOOLS, ToolValidationError, tool_definitions, validate_args
from tests.conftest import make_settings

S = make_settings(user_timezone="Asia/Kolkata", max_search_results=25, max_recipients=3)


def ok(name: str, **args: Any) -> dict[str, Any]:
    return validate_args(name, args, S)


def bad(tool: str, match: str, **args: Any) -> None:
    with pytest.raises(ToolValidationError, match=match):
        validate_args(tool, args, S)


def test_registry_has_all_tools() -> None:
    assert READ_TOOLS == {"search_emails", "read_email", "list_labels", "list_drafts", "list_events",
                          "find_free_slots", "search_drive_files"}
    assert WRITE_TOOLS == {"send_email", "reply_to_email", "forward_email", "create_draft", "update_draft",
                           "send_draft", "create_label", "modify_labels", "create_event", "move_event",
                           "cancel_event", "create_drive_folder", "move_drive_file", "save_attachments_to_drive"}
    assert not any("delete" in n or "trash" in n for n in TOOLS)


def test_definitions_are_strict_json_schema() -> None:
    for d in tool_definitions(make_settings(max_search_results=7)):
        p = d["parameters"]
        assert p["type"] == "object" and p["additionalProperties"] is False
        assert set(p["required"]) <= set(p["properties"])
        if "max_results" in p["properties"] and d["name"] != "find_free_slots":
            assert p["properties"]["max_results"]["maximum"] == 7


def test_unknown_tool() -> None:
    bad("delete_email", "Unknown tool")


def test_non_object_args() -> None:
    with pytest.raises(ToolValidationError, match="JSON object"):
        validate_args("search_emails", ["x"], S)


def test_unknown_field() -> None:
    bad("search_emails", "Unknown field", q="x")


def test_missing_required() -> None:
    bad("read_email", "Missing required field\\(s\\): message_id")


def test_nulls_are_dropped() -> None:
    assert ok("search_emails", query=None, max_results=None) == {}


def test_wrong_type() -> None:
    bad("read_email", "must be a string", message_id=5)
    bad("search_emails", "whole number", max_results="ten")
    bad("search_emails", "whole number", max_results=True)
    bad("reply_to_email", "true or false", message_id="m1", body="x", reply_all="yes")


def test_float_integers_accepted() -> None:
    assert ok("search_emails", max_results=5.0)["max_results"] == 5


def test_max_results_capped() -> None:
    bad("search_emails", "between 1 and 25", max_results=26)
    bad("search_emails", "between 1 and 25", max_results=0)
    with pytest.raises(ToolValidationError, match="between 1 and 4"):
        validate_args("list_drafts", {"max_results": 5}, make_settings(max_search_results=4))


def test_enum() -> None:
    assert ok("search_drive_files", mime_type="pdf")["mime_type"] == "pdf"
    bad("search_drive_files", "must be one of", mime_type="exe")


def test_emails_validated_and_deduped() -> None:
    assert ok("send_email", to=["a@x.com", "a@x.com"], subject="s", body="b")["to"] == ["a@x.com"]
    bad("send_email", "Not a valid email address: bob", to=["bob"], subject="s", body="b")
    bad("send_email", "at least 1", to=[], subject="s", body="b")


def test_single_string_becomes_list() -> None:
    assert ok("send_email", to="a@x.com", subject="s", body="b")["to"] == ["a@x.com"]


def test_recipient_limit() -> None:
    bad("send_email", "Too many recipients", to=["a@x.com", "b@x.com"], cc=["c@x.com", "d@x.com"], subject="s", body="b")


def test_string_length_limits() -> None:
    bad("create_label", "must not be empty", name="  ")
    bad("search_emails", "too long", query="x" * 501)


def test_datetime_normalized_with_user_timezone() -> None:
    a = ok("create_event", title="T", start="2026-11-03T10:00:00", end="2026-11-03T10:30:00Z")
    assert a["start"] == "2026-11-03T10:00:00+05:30"
    assert a["end"] == "2026-11-03T10:30:00+00:00"


def test_datetime_needs_time() -> None:
    bad("create_event", "needs a time", title="T", start="2026-11-03", end="2026-11-03T10:00:00")
    bad("create_event", "ISO 8601", title="T", start="tomorrow 3pm", end="2026-11-03T10:00:00")


def test_event_must_end_after_start() -> None:
    bad("create_event", "end after it starts", title="T", start="2026-11-03T10:00:00", end="2026-11-03T09:00:00")
    bad("create_event", "at most 14 days", title="T", start="2026-11-03T10:00:00", end="2026-12-03T09:00:00")


def test_move_event_new_end_after_start() -> None:
    assert "new_end" not in ok("move_event", event_id="e", new_start="2026-11-03T10:00:00")
    bad("move_event", "new_end must be after", event_id="e", new_start="2026-11-03T10:00:00", new_end="2026-11-03T09:00:00")


def test_list_events_range() -> None:
    bad("list_events", "end must be after", start="2026-11-03T10:00:00", end="2026-11-03T10:00:00")
    bad("list_events", "62 days", start="2026-01-01T00:00:00", end="2026-04-01T00:00:00")


def test_find_free_slots_rules() -> None:
    a = ok("find_free_slots", duration_minutes=45, date_from="2026-11-03", date_to="2026-11-04")
    assert a["date_from"] == "2026-11-03"
    bad("find_free_slots", "between 5 and 480", duration_minutes=2, date_from="2026-11-03", date_to="2026-11-03")
    bad("find_free_slots", "on or after", duration_minutes=30, date_from="2026-11-04", date_to="2026-11-03")
    bad("find_free_slots", "14 days", duration_minutes=30, date_from="2026-11-01", date_to="2026-11-20")
    bad("find_free_slots", "YYYY-MM-DD", duration_minutes=30, date_from="Tuesday", date_to="2026-11-03")


def test_modify_labels_rules() -> None:
    bad("modify_labels", "add_labels or remove_labels", message_ids=["m1"])
    bad("modify_labels", "both added and removed", message_ids=["m1"], add_labels=["A"], remove_labels=["A"])
    bad("modify_labels", "at most 100", message_ids=[f"m{i}" for i in range(101)], add_labels=["A"])


def test_update_draft_needs_a_change() -> None:
    bad("update_draft", "at least one field", draft_id="d1")
    assert ok("update_draft", draft_id="d1", subject="New")["subject"] == "New"


def test_save_attachments_folder_choice() -> None:
    bad("save_attachments_to_drive", "not both", message_id="m1", folder_id="f", folder_name="n")


def test_array_items_must_be_strings() -> None:
    bad("modify_labels", "list of strings", message_ids=[1, 2], add_labels=["A"])
