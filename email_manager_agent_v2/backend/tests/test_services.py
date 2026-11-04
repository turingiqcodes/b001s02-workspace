"""Unit tests: parsing, validation, previews, scheduling (section 10.1)."""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.errors import PreviewRejected
from app.services import calendar, drive, gmail
from app.services.availability import find_free_slots
from tests.conftest import MB, make_email


def run(coro):
    return asyncio.run(coro)


# ---------- validation ----------

@pytest.mark.parametrize(
    "model,data,msg",
    [
        (gmail.SendEmailArgs, {"to": ["bad"], "subject": "s", "body": "b"}, "valid email"),
        (gmail.SendEmailArgs, {"to": [], "subject": "s", "body": "b"}, "at least one recipient"),
        (gmail.SendEmailArgs, {"to": ["a@b.co"], "subject": "x\r\nBcc: evil@x.com", "body": "b"}, "line breaks"),
        (gmail.SearchEmailsArgs, {}, "at least one search filter"),
        (gmail.UpdateDraftArgs, {"draft_id": "d1"}, "at least one field"),
        (gmail.UpdateEmailLabelsArgs, {"message_id": "m1"}, "labels to add or remove"),
        (gmail.MessageIdArgs, {"message_id": "m1", "extra": 1}, "Extra inputs"),
        (calendar.CreateCalendarEventArgs, {"title": "t", "start": "2026-11-05T10:00:00", "end": "2026-11-05T11:00:00+05:30"}, "UTC offset"),
        (calendar.CreateCalendarEventArgs, {"title": "t", "start": "2026-11-05T11:00:00+05:30", "end": "2026-11-05T10:00:00+05:30"}, "end must be after start"),
        (calendar.FindFreeSlotsArgs, {"max_results": 6}, "less than or equal to 5"),
        (calendar.UpdateCalendarEventArgs, {"event_id": "e1"}, "at least one field"),
    ],
)
def test_validation_errors(model, data, msg):
    with pytest.raises(ValidationError) as exc:
        model.model_validate(data)
    assert msg in str(exc.value)


def test_search_query_builder():
    args = gmail.SearchEmailsArgs(sender="Priya Shah", subject="Q3 (draft)", after=date(2026, 11, 1), is_unread=True)
    assert args.gmail_query() == 'from:"Priya Shah" subject:(Q3 draft) after:2026/11/01 is:unread'


# ---------- parsing / limits (NFR-10) ----------

def test_parse_email_truncates_body():
    raw = make_email(1, body="x" * 7000)
    parsed = gmail.parse_email(raw, 6000)
    assert parsed["body"].startswith("x" * 6000)
    assert "truncated, 1000 more characters" in parsed["body"]
    assert "message_id_header" not in parsed


def test_list_results_use_list_limit(ctx, bridge):
    bridge.emails["m1"]["body"] = "y" * 5000
    out = run(gmail.get_latest_emails(ctx, gmail.GetLatestEmailsArgs()))
    assert len(out["emails"][0]["body"]) < 3100


def test_tool_result_truncated_at_60000(client, llm, bridge):
    from tests.conftest import text, tool_use

    for i in range(4, 40):
        bridge.emails[f"m{i}"] = make_email(i, body="z" * 6000)
    llm.then(tool_use("get_latest_emails", {"max_results": 50}, id="t")).then(text("ok"))
    client.post("/api/prompt", json={"prompt": "all"})
    content = llm.requests[1][-1]["content"][0]["content"]
    assert len(content) < 60200 and "result truncated" in content


def test_parse_event_and_file():
    ev = calendar.parse_event({"id": "1", "title": None, "html_link": "L"})
    assert ev["title"] == "(no title)" and ev["link"] == "L" and ev["all_day"] is False
    f = drive.parse_file({"id": "f", "name": "n", "mime_type": "m", "size": 3, "url": "u", "created": "c", "updated": "u2", "extra": 1})
    assert set(f) == {"id", "name", "mime_type", "size", "url", "created", "updated"}


# ---------- Gmail previews ----------

def test_draft_reply_preserves_thread(ctx, bridge):
    bridge.emails["m1"]["references"] = "<old@x>"
    prepared = run(gmail.prepare_draft_reply(ctx, gmail.DraftReplyArgs(message_id="m1", body="Thanks", reply_all=True)))
    a = prepared.bridge_args
    assert prepared.bridge_operation == "create_draft"
    assert a["to"] == ["priya@example.com"]
    assert a["cc"] == ["team@example.com", "raj@example.com"]  # me@example.com excluded
    assert a["subject"] == "Re: Meeting"
    assert a["in_reply_to"] == "<msg1@mail.example.com>"
    assert a["references"] == "<old@x> <msg1@mail.example.com>"
    assert a["thread_id"] == "t1"
    assert a["reply_to_message_id"] == "m1"  # bridge uses createDraftReply on this message


def test_reply_subject_not_doubled():
    assert gmail.reply_subject("RE: hello") == "RE: hello"
    assert gmail.reply_subject("hello") == "Re: hello"


def test_update_draft_preview_shows_old_and_new(ctx):
    p = run(gmail.prepare_update_draft(ctx, gmail.UpdateDraftArgs(draft_id="d1", subject="New subject")))
    assert p.preview.existing == {"subject": "Draft subject"}
    assert p.preview.proposed == {"subject": "New subject"}
    assert p.bridge_args["body"] == "Draft body" and p.bridge_args["thread_id"] == "t9"


def test_send_draft_without_recipients_rejected(ctx):
    with pytest.raises(PreviewRejected, match="no recipients"):
        run(gmail.prepare_send_draft(ctx, gmail.DraftIdArgs(draft_id="d2")))


def test_labels_case_insensitive(ctx):
    p = run(gmail.prepare_update_email_labels(ctx, gmail.UpdateEmailLabelsArgs(message_id="m1", add_labels=["WORK", "Travel"], remove_labels=["inbox"])))
    assert p.bridge_args == {"message_id": "m1", "add_labels": ["Work", "Travel"], "remove_labels": ["INBOX"]}
    assert any("Travel" in w and "created" in w for w in p.preview.warnings)
    assert p.preview.proposed == {"labels": "Work, Travel"}


def test_remove_unknown_label_rejected(ctx):
    with pytest.raises(PreviewRejected, match="no label called"):
        run(gmail.prepare_update_email_labels(ctx, gmail.UpdateEmailLabelsArgs(message_id="m1", remove_labels=["Nope"])))


def test_trash_and_restore_state_checks(ctx):
    with pytest.raises(PreviewRejected, match="already in Trash"):
        run(gmail.prepare_trash_email(ctx, gmail.MessageIdArgs(message_id="m3")))
    with pytest.raises(PreviewRejected, match="isn't in Trash"):
        run(gmail.prepare_restore_email(ctx, gmail.MessageIdArgs(message_id="m1")))


def test_irreversible_flags():
    assert run_preview("delete_draft").irreversible
    assert run_preview("delete_calendar_event").irreversible
    assert not run_preview("trash_email").irreversible


def run_preview(kind):
    from tests.conftest import FakeBridge
    from app.config import Settings
    from app.services.base import ToolContext

    b = FakeBridge()

    async def gp():
        return b.profile

    c = ToolContext(b, Settings(), gp)
    match kind:
        case "delete_draft":
            return run(gmail.prepare_delete_draft(c, gmail.DraftIdArgs(draft_id="d1"))).preview
        case "delete_calendar_event":
            return run(calendar.prepare_delete_event(c, calendar.DeleteCalendarEventArgs(event_id="e1"))).preview
        case "trash_email":
            return run(gmail.prepare_trash_email(c, gmail.MessageIdArgs(message_id="m1"))).preview


# ---------- Calendar ----------

def test_create_event_conflict_rejected(ctx):
    args = calendar.CreateCalendarEventArgs(title="X", start="2026-11-05T14:30:00+05:30", end="2026-11-05T15:30:00+05:30")
    with pytest.raises(PreviewRejected, match="Design review"):
        run(calendar.prepare_create_event(ctx, args))  # AT-19


def test_create_event_conflict_allowed_when_user_agrees(ctx):
    args = calendar.CreateCalendarEventArgs(
        title="X", start="2026-11-05T14:30:00+05:30", end="2026-11-05T15:30:00+05:30", allow_conflict=True
    )
    p = run(calendar.prepare_create_event(ctx, args))
    assert p.preview.availability["free"] is False
    assert "Design review" in p.preview.availability["conflicts"][0]
    assert p.preview.warnings


def test_all_day_events_do_not_conflict(ctx):
    args = calendar.CreateCalendarEventArgs(
        title="X", start="2026-11-05T10:00:00+05:30", end="2026-11-05T10:30:00+05:30",
        attendees=["a@example.com"],
    )
    p = run(calendar.prepare_create_event(ctx, args))
    assert p.preview.availability == {"free": True, "conflicts": []}
    assert p.preview.notify_attendees is True
    assert p.bridge_args["send_updates"] == "all"


def test_no_attendees_means_no_notification(ctx):
    args = calendar.CreateCalendarEventArgs(title="Focus", start="2026-11-06T10:00:00+05:30", end="2026-11-06T11:00:00+05:30")
    p = run(calendar.prepare_create_event(ctx, args))
    assert p.preview.notify_attendees is None
    assert p.bridge_args["send_updates"] == "none"


def test_move_event_preserves_duration(ctx):
    p = run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", start="2026-11-05T16:00:00+05:30")))
    assert p.bridge_args["start"] == "2026-11-05T16:00:00+05:30"
    assert p.bridge_args["end"] == "2026-11-05T17:00:00+05:30"  # AT-20: 1 hour kept
    assert p.preview.existing["when"].startswith("Thu 05 Nov 2026, 14:00")
    assert p.preview.proposed["duration"] == "60 minutes"
    assert p.preview.notify_attendees is True
    assert p.preview.availability["free"] is True


def test_move_event_ignores_itself_but_detects_others(ctx):
    # Moving 30 min later overlaps only itself -> fine.
    run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", start="2026-11-05T14:30:00+05:30")))
    with pytest.raises(PreviewRejected, match="Lunch"):
        run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", start="2026-11-05T13:00:00+05:30")))


def test_update_event_no_change_rejected(ctx):
    with pytest.raises(PreviewRejected, match="same"):
        run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", title="Design review")))


def test_delete_event_notification_flag(ctx):
    p = run(calendar.prepare_delete_event(ctx, calendar.DeleteCalendarEventArgs(event_id="e1", notify_attendees=False)))
    assert p.preview.notify_attendees is False
    assert p.bridge_args == {"event_id": "e1", "send_updates": "none"}


IST = ZoneInfo("Asia/Kolkata")


def test_free_slots_rules():
    now = datetime(2026, 11, 6, 8, 0, tzinfo=IST)  # Friday morning
    busy = [(datetime(2026, 11, 6, 9, 0, tzinfo=IST), datetime(2026, 11, 6, 10, 15, tzinfo=IST))]
    slots = find_free_slots(busy, date(2026, 11, 6), date(2026, 11, 10), timedelta(minutes=60), IST, now, 10)
    assert len(slots) == 5  # capped at five
    assert slots[0][0] == datetime(2026, 11, 6, 10, 30, tzinfo=IST)  # after busy, on 30-min boundary
    for s, e in slots:
        assert s.weekday() < 5
        assert s.minute in (0, 30)
        assert s.hour >= 9 and (e.hour, e.minute) <= (18, 0)


def test_free_slots_skip_weekend_and_past():
    now = datetime(2026, 11, 6, 17, 10, tzinfo=IST)  # Friday after last 60-min slot
    slots = find_free_slots([], date(2026, 11, 6), date(2026, 11, 9), timedelta(minutes=60), IST, now)
    assert slots[0][0] == datetime(2026, 11, 9, 9, 0, tzinfo=IST)  # Monday


def test_free_slots_tool_uses_busy(ctx, bridge):
    bridge.busy = [{"start": "2026-11-09T09:00:00+05:30", "end": "2026-11-09T12:00:00+05:30"}]
    out = run(calendar.find_free_slots_tool(ctx, calendar.FindFreeSlotsArgs(duration_minutes=30, start_date=date(2026, 11, 9), end_date=date(2026, 11, 9))))
    assert out["slots"][0]["start"] == "2026-11-09T12:00:00+05:30"
    assert len(out["slots"]) == 5


# ---------- Drive ----------

def test_save_named_attachment_and_missing_name(ctx):
    p = run(drive.prepare_save_attachments(ctx, drive.SaveAttachmentsArgs(message_id="m2", attachment_names=["REPORT.pdf"])))
    assert p.bridge_args["attachment_names"] == ["report.pdf"]
    with pytest.raises(PreviewRejected, match="No attachment named"):
        run(drive.prepare_save_attachments(ctx, drive.SaveAttachmentsArgs(message_id="m2", attachment_names=["x.doc"])))


def test_only_oversized_attachments_rejected(ctx, bridge):
    bridge.emails["m2"]["attachments"] = [{"name": "big.zip", "size": 26 * MB}]
    with pytest.raises(PreviewRejected, match="larger than 25 MB"):
        run(drive.prepare_save_attachments(ctx, drive.SaveAttachmentsArgs(message_id="m2")))


def test_trash_drive_file_already_trashed(ctx, bridge):
    bridge.files["f1"]["trashed"] = True
    with pytest.raises(PreviewRejected, match="already in Drive Trash"):
        run(drive.prepare_trash_drive_file(ctx, drive.FileIdArgs(file_id="f1")))


def test_labels_preview_names_the_conversation(ctx, bridge):
    bridge.emails["m1"]["thread_message_count"] = 4
    p = run(gmail.prepare_update_email_labels(ctx, gmail.UpdateEmailLabelsArgs(message_id="m1", add_labels=["starred"])))
    assert p.bridge_args["add_labels"] == ["STARRED"]
    assert p.preview.target["conversation"] == "4 messages"
    assert any("whole conversation (4 messages)" in w for w in p.preview.warnings)


def test_restore_preview_names_the_conversation(ctx):
    p = run(gmail.prepare_restore_email(ctx, gmail.MessageIdArgs(message_id="m3")))
    assert "(3 messages)" in p.preview.summary
    assert p.preview.proposed == {"location": "Inbox"}
    assert p.preview.warnings


def test_move_and_cancel_previews_say_bridge_sends_email(ctx):
    moved = run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", start="2026-11-05T16:00:00+05:30")))
    assert any("sent from your Gmail" in w for w in moved.preview.warnings)
    quiet = run(calendar.prepare_update_event(ctx, calendar.UpdateCalendarEventArgs(event_id="e1", start="2026-11-05T16:00:00+05:30", notify_attendees=False)))
    assert not any("Gmail" in w for w in quiet.preview.warnings)
    cancelled = run(calendar.prepare_delete_event(ctx, calendar.DeleteCalendarEventArgs(event_id="e1")))
    assert any("cancellation email sent from your Gmail" in w for w in cancelled.preview.warnings)


@pytest.mark.parametrize("value", ["2026-11-04", "2026-11-04T00:00:00+00:00", "2026-11-04T18:30:00Z", "2026-11-04 09:00"])
def test_date_filters_accept_date_or_datetime(value):
    args = gmail.SearchEmailsArgs(after=value, before=value)
    assert args.after == date(2026, 11, 4)
    assert "after:2026/11/04" in args.gmail_query()
    slots = calendar.FindFreeSlotsArgs(start_date=value, end_date=value)
    assert slots.start_date == date(2026, 11, 4)


def test_bad_date_still_rejected():
    with pytest.raises(ValidationError):
        gmail.SearchEmailsArgs(after="next tuesday")
