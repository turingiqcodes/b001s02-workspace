from __future__ import annotations

from app.bridge import BridgeError, MockBridge
from app.tools import ALL_TOOLS, READ_TOOL_NAMES, WRITE_TOOL_NAMES, build_preview, validate_input

import pytest


def test_every_tool_is_classified_once():
    names = [t["name"] for t in ALL_TOOLS]
    assert len(names) == len(set(names))
    assert READ_TOOL_NAMES.isdisjoint(WRITE_TOOL_NAMES)
    assert READ_TOOL_NAMES | WRITE_TOOL_NAMES == set(names)


def test_validate_input():
    assert validate_input("search_emails", {"query": "x"}) is None
    assert "Missing" in validate_input("search_emails", {})
    assert "Unknown field" in validate_input("search_emails", {"query": "x", "foo": 1})
    assert "integer" in validate_input("search_emails", {"query": "x", "max_results": True})
    assert "list of strings" in validate_input("create_draft", {"to": [1], "subject": "s", "body": "b"})
    assert "Unknown tool" in validate_input("delete_everything", {})


def test_preview_create_event_warns_on_overlap():
    b = MockBridge()
    e = b.events["e1"]
    p = build_preview("create_event", {"summary": "Sync", "start": e["start"], "end": e["end"]}, b)
    assert p["title"] == "Schedule meeting"
    assert "Team standup" in p["warning"]


def test_preview_update_draft_shows_before_and_after():
    b = MockBridge()
    d = b.call("create_draft", {"to": ["a@x.com"], "subject": "Old", "body": "hi"})
    p = build_preview("update_draft", {"draft_id": d["id"], "subject": "New"}, b)
    assert {"label": "Subject", "value": "Old → New"} in p["fields"]


def test_preview_survives_missing_targets():
    b = MockBridge()
    p = build_preview("cancel_event", {"event_id": "missing"}, b)
    assert {"label": "Event ID", "value": "missing"} in p["fields"]


def test_mock_label_rules():
    b = MockBridge()
    with pytest.raises(BridgeError) as exc:
        b.call("modify_labels", {"message_ids": ["m1"], "remove_labels": ["INBOX"]})
    assert exc.value.code == "FORBIDDEN_LABEL"
    b.call("modify_labels", {"message_ids": ["m1"], "remove_labels": ["Projects/Apollo"]})
    assert "Label_2" not in b.messages["m1"]["labelIds"]
    b.call("modify_labels", {"message_ids": ["m1"], "add_labels": ["Projects/Apollo"]})  # restore
    assert "Label_2" in b.messages["m1"]["labelIds"]
