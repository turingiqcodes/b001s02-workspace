"""Human-in-the-loop approval (FR-33..39, NFR-05, NFR-15; AT-07..14, 18, 21, 22, 24)."""

import json

import pytest

from app.errors import BridgeError
from tests.conftest import text, tool_use


def propose(client, llm, name, args, prompt="do it", session_id=None):
    llm.then(tool_use(name, args, id="w1")).then(text("Ready for your review."))
    payload = {"prompt": prompt}
    if session_id:
        payload["session_id"] = session_id
    body = client.post("/api/prompt", json=payload).json()
    return body


def confirm(client, action_id, approved=True):
    return client.post("/api/confirm", json={"action_id": action_id, "approved": approved})


WRITE_CASES = [
    ("create_draft", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}, "create_draft"),
    ("draft_reply", {"message_id": "m1", "body": "Thanks!"}, "create_draft"),
    ("update_draft", {"draft_id": "d1", "body": "New body"}, "update_draft"),
    ("delete_draft", {"draft_id": "d1"}, "delete_draft"),
    ("send_draft", {"draft_id": "d1"}, "send_draft"),
    ("send_email", {"to": ["priya@example.com"], "subject": "Re: Meeting", "body": "Thanks"}, "send_email"),
    ("update_email_labels", {"message_id": "m1", "add_labels": ["work"]}, "update_email_labels"),
    ("trash_email", {"message_id": "m1"}, "trash_email"),
    ("restore_email", {"message_id": "m3"}, "restore_email"),
    ("create_calendar_event", {"title": "Sync", "start": "2026-11-06T10:00:00+05:30", "end": "2026-11-06T10:30:00+05:30"}, "create_calendar_event"),
    ("update_calendar_event", {"event_id": "e1", "start": "2026-11-05T16:00:00+05:30"}, "update_calendar_event"),
    ("delete_calendar_event", {"event_id": "e1"}, "delete_calendar_event"),
    ("save_attachments_to_drive", {"message_id": "m2"}, "save_attachments_to_drive"),
    ("trash_drive_file", {"file_id": "f1"}, "trash_drive_file"),
]


@pytest.mark.parametrize("tool,args,bridge_op", WRITE_CASES, ids=[c[0] for c in WRITE_CASES])
def test_every_write_needs_approval_then_executes(client, llm, bridge, tool, args, bridge_op):
    body = propose(client, llm, tool, args)
    assert len(body["pending_actions"]) == 1, body
    action = body["pending_actions"][0]
    assert action["operation"] == tool
    assert action["status"] == "needs_confirmation"
    assert action["preview"]["title"] and action["preview"]["summary"]
    # Nothing written yet (AT-07, AT-10, AT-18, AT-21).
    assert bridge.write_calls() == []
    # The model was told the action is pending, not done.
    result = json.loads(llm.requests[1][-1]["content"][0]["content"])
    assert result["status"] == "pending_user_approval"

    resp = confirm(client, action["action_id"])
    assert resp.status_code == 200
    out = resp.json()
    assert out["status"] == "executed", out
    assert out["message"]
    writes = bridge.write_calls()
    assert len(writes) == 1 and writes[0][0] == bridge_op


def test_cancel_does_not_execute_and_agent_is_told(client, llm, bridge):
    body = propose(client, llm, "create_draft", {"to": ["a@example.com"], "subject": "Hi", "body": "x"})
    aid = body["pending_actions"][0]["action_id"]
    out = confirm(client, aid, approved=False).json()
    assert out == {"action_id": aid, "status": "declined", "message": "The operation was cancelled."}
    assert bridge.write_calls() == []  # AT-09

    llm.then(text("ok"))
    client.post("/api/prompt", json={"prompt": "what happened?", "session_id": body["session_id"]})
    first_block = llm.requests[-1][-1]["content"]
    notes = [b for b in first_block if b.get("type") == "text" and "Updates since" in b["text"]]
    assert notes and "DECLINED" in notes[0]["text"] and aid in notes[0]["text"]


def test_approved_args_are_exactly_the_previewed_ones(client, llm, bridge):
    body = propose(client, llm, "send_email", {"to": ["priya@example.com"], "subject": "Re: Meeting", "body": "Thank you"})
    aid = body["pending_actions"][0]["action_id"]
    preview = body["pending_actions"][0]["preview"]
    assert preview["proposed"]["to"] == "priya@example.com"
    assert preview["irreversible"] is True

    # Even if the conversation were tampered with afterwards, the stored action is fixed.
    session = client.app.state.store.get(body["session_id"])
    session.conversation[-3]["content"][0]["input"]["to"] = ["attacker@evil.com"]

    confirm(client, aid)
    assert bridge.write_calls() == [
        ("send_email", {"to": ["priya@example.com"], "cc": [], "bcc": [], "subject": "Re: Meeting", "body": "Thank you"})
    ]


def test_double_approve_executes_once(client, llm, bridge):
    aid = propose(client, llm, "trash_email", {"message_id": "m1"})["pending_actions"][0]["action_id"]
    assert confirm(client, aid).json()["status"] == "executed"
    second = confirm(client, aid)
    assert second.status_code == 409
    assert "already executed" in second.json()["detail"]
    assert len(bridge.write_calls()) == 1


def test_cannot_approve_after_cancel(client, llm, bridge):
    aid = propose(client, llm, "trash_email", {"message_id": "m1"})["pending_actions"][0]["action_id"]
    confirm(client, aid, approved=False)
    assert confirm(client, aid).status_code == 409
    assert bridge.write_calls() == []


def test_new_prompt_declines_pending_actions(client, llm, bridge):
    body = propose(client, llm, "trash_email", {"message_id": "m1"})
    aid = body["pending_actions"][0]["action_id"]
    llm.then(text("sure"))
    second = client.post("/api/prompt", json={"prompt": "never mind", "session_id": body["session_id"]}).json()
    assert second["declined_actions"] == [aid]
    assert confirm(client, aid).status_code == 409  # FR-37
    assert bridge.write_calls() == []


def test_multiple_writes_get_separate_approvals(client, llm, bridge):
    llm.then(
        tool_use("trash_email", {"message_id": "m1"}, id="w1"),
        tool_use("trash_email", {"message_id": "m2"}, id="w2"),
    ).then(text("Two actions ready."))
    body = client.post("/api/prompt", json={"prompt": "trash both"}).json()
    a1, a2 = [a["action_id"] for a in body["pending_actions"]]
    confirm(client, a1)
    confirm(client, a2, approved=False)
    assert bridge.write_calls() == [("trash_email", {"message_id": "m1"})]  # FR-39


def test_failed_execution_is_reported(client, llm, bridge):
    aid = propose(client, llm, "send_draft", {"draft_id": "d1"})["pending_actions"][0]["action_id"]
    bridge.fail["send_draft"] = BridgeError("Gmail quota exceeded.", code="google_error")
    out = confirm(client, aid).json()
    assert out["status"] == "failed"
    assert "Gmail quota exceeded" in out["message"]


def test_executed_result_reported_to_agent_next_turn(client, llm):
    body = propose(client, llm, "trash_email", {"message_id": "m1"})
    confirm(client, body["pending_actions"][0]["action_id"])
    llm.then(text("ok"))
    client.post("/api/prompt", json={"prompt": "done?", "session_id": body["session_id"]})
    joined = json.dumps(llm.requests[-1][-1])
    assert "APPROVED and EXECUTED" in joined


def test_save_attachments_reports_links_and_skips(client, llm, bridge):
    body = propose(client, llm, "save_attachments_to_drive", {"message_id": "m2"})
    preview = body["pending_actions"][0]["preview"]
    assert "report.pdf" in preview["proposed"]["files"]
    assert "video.mp4" not in preview["proposed"]["files"]
    assert any("video.mp4" in w and "25 MB" in w for w in preview["warnings"])  # AT-23
    out = confirm(client, body["pending_actions"][0]["action_id"]).json()
    assert "https://drive/nf" in out["message"]  # FR-30
    op, args = bridge.write_calls()[0]
    assert args == {"message_id": "m2", "attachment_names": ["report.pdf"], "folder_name": "Email Attachments"}


def test_invalid_write_args_return_error_to_agent_not_action(client, llm, bridge):
    body = propose(client, llm, "send_email", {"to": ["not-an-email"], "subject": "x", "body": "y"})
    assert body["pending_actions"] == []
    result = llm.requests[1][-1]["content"][0]
    assert result["is_error"] is True
    assert "not a valid email address" in result["content"]


def test_unknown_tool_returns_error(client, llm, bridge):
    body = propose(client, llm, "permanently_delete_email", {"message_id": "m1"})
    assert body["pending_actions"] == []
    result = llm.requests[1][-1]["content"][0]
    assert result["is_error"] and "Unknown tool" in result["content"]
    assert bridge.write_calls() == []
