"""Google Calendar tools: argument models, parsing, availability and previews (FR-20..FR-27)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.errors import PreviewRejected
from app.services.availability import find_free_slots, format_range, get_zone, overlaps
from app.services.base import (
    coerce_date,
    Preview,
    PreparedWrite,
    ToolContext,
    parse_aware_datetime,
    validate_email,
)

DT_HELP = "ISO 8601 with UTC offset, e.g. 2026-11-05T16:00:00+05:30"
BRIDGE_EMAIL_NOTE = (
    "Attendees will get a short email sent from your Gmail account "
    "(Apps Script can't send Google Calendar's own update emails)."
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_dt(v: str | None) -> str | None:
    return None if v is None else parse_aware_datetime(v).isoformat()


class _Range(_Args):
    start: str = Field(..., description=DT_HELP)
    end: str = Field(..., description=DT_HELP)

    @field_validator("start", "end")
    @classmethod
    def _dt(cls, v: str) -> str:
        return _check_dt(v)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _order(self) -> "_Range":
        s, e = parse_aware_datetime(self.start), parse_aware_datetime(self.end)
        if e <= s:
            raise ValueError("end must be after start")
        if e - s > timedelta(days=92):
            raise ValueError("the range can be at most 92 days")
        return self


class ListCalendarEventsArgs(_Range):
    query: str | None = Field(None, max_length=200, description="Text to match in event titles/descriptions.")
    max_results: int = Field(25, ge=1, le=100)


class CheckAvailabilityArgs(_Range):
    pass


class FindFreeSlotsArgs(_Args):
    duration_minutes: int = Field(30, ge=15, le=540, description="Meeting length in minutes.")
    start_date: date | None = Field(None, description="First day to search (YYYY-MM-DD). Defaults to today.")
    end_date: date | None = Field(None, description="Last day to search (YYYY-MM-DD). Defaults to 7 days after start.")
    max_results: int = Field(5, ge=1, le=5)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _dates(cls, v: object) -> object:
        return coerce_date(v)

    @model_validator(mode="after")
    def _order(self) -> "FindFreeSlotsArgs":
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date must be on or after start_date")
            if (self.end_date - self.start_date).days > 31:
                raise ValueError("search at most 31 days at a time")
        return self


def _emails(v: list[str] | None) -> list[str] | None:
    return None if v is None else [validate_email(x) for x in v]


class CreateCalendarEventArgs(_Range):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=8000)
    location: str | None = Field(None, max_length=500)
    attendees: list[str] = Field(default_factory=list, max_length=100)
    notify_attendees: bool = Field(True, description="Email invitations to attendees.")
    allow_conflict: bool = Field(
        False, description="Only set true after the user explicitly agreed to double-book."
    )

    @field_validator("attendees")
    @classmethod
    def _att(cls, v: list[str]) -> list[str]:
        return _emails(v) or []


class UpdateCalendarEventArgs(_Args):
    event_id: str = Field(..., min_length=1, max_length=300)
    title: str | None = Field(None, min_length=1, max_length=300)
    start: str | None = Field(None, description=DT_HELP + ". If only start is given, the duration is kept.")
    end: str | None = Field(None, description=DT_HELP)
    description: str | None = Field(None, max_length=8000)
    location: str | None = Field(None, max_length=500)
    attendees: list[str] | None = Field(None, max_length=100, description="Full new attendee list.")
    notify_attendees: bool = True
    allow_conflict: bool = False

    @field_validator("start", "end")
    @classmethod
    def _dt(cls, v: str | None) -> str | None:
        return _check_dt(v)

    @field_validator("attendees")
    @classmethod
    def _att(cls, v: list[str] | None) -> list[str] | None:
        return _emails(v)

    @model_validator(mode="after")
    def _has_change(self) -> "UpdateCalendarEventArgs":
        if all(getattr(self, f) is None for f in ("title", "start", "end", "description", "location", "attendees")):
            raise ValueError("give at least one field to change")
        return self


class DeleteCalendarEventArgs(_Args):
    event_id: str = Field(..., min_length=1, max_length=300)
    notify_attendees: bool = True


# ---------- parsing ----------

def parse_event(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "(no title)",
        "start": raw.get("start"),
        "end": raw.get("end"),
        "all_day": bool(raw.get("all_day")),
        "location": raw.get("location") or "",
        "description": (raw.get("description") or "")[:2000],
        "attendees": raw.get("attendees") or [],
        "organizer": raw.get("organizer"),
        "status": raw.get("status"),
        "transparency": raw.get("transparency"),
        "link": raw.get("html_link"),
    }


def _attendee_emails(event: dict[str, Any]) -> list[str]:
    return [a.get("email") for a in event.get("attendees") or [] if a.get("email")]


# ---------- read handlers ----------

async def list_calendar_events(ctx: ToolContext, args: ListCalendarEventsArgs) -> Any:
    raw = await ctx.bridge.call("list_calendar_events", args.model_dump(exclude_none=True))
    return {"time_zone": raw.get("time_zone"), "events": [parse_event(e) for e in raw.get("events") or []]}


async def check_availability(ctx: ToolContext, args: CheckAvailabilityArgs) -> Any:
    raw = await ctx.bridge.call("check_availability", {"start": args.start, "end": args.end})
    busy = raw.get("busy") or []
    return {"time_zone": raw.get("time_zone"), "busy": busy, "is_free": not busy}


async def find_free_slots_tool(ctx: ToolContext, args: FindFreeSlotsArgs) -> Any:
    profile = await ctx.get_profile()
    tz = get_zone(profile.get("time_zone"))
    now = datetime.now(tz)
    first = args.start_date or now.date()
    last = args.end_date or first + timedelta(days=7)
    window_start = datetime.combine(first, datetime.min.time(), tzinfo=tz)
    window_end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    raw = await ctx.bridge.call(
        "check_availability", {"start": window_start.isoformat(), "end": window_end.isoformat()}
    )
    busy = [(parse_aware_datetime(b["start"]), parse_aware_datetime(b["end"])) for b in raw.get("busy") or []]
    slots = find_free_slots(busy, first, last, timedelta(minutes=args.duration_minutes), tz, now, args.max_results)
    return {
        "time_zone": tz.key,
        "rules": "Weekdays, 09:00-18:00, 30-minute steps, at most 5 suggestions",
        "slots": [
            {"start": s.isoformat(), "end": e.isoformat(), "label": format_range(s, e, tz)} for s, e in slots
        ],
    }


# ---------- conflicts ----------

async def find_conflicts(
    ctx: ToolContext, start: datetime, end: datetime, exclude_id: str | None = None
) -> list[dict[str, Any]]:
    raw = await ctx.bridge.call(
        "list_calendar_events", {"start": start.isoformat(), "end": end.isoformat(), "max_results": 50}
    )
    conflicts = []
    for ev in raw.get("events") or []:
        if ev.get("id") == exclude_id or ev.get("all_day") or ev.get("transparency") == "transparent":
            continue
        if ev.get("status") == "cancelled":
            continue
        try:
            es, ee = parse_aware_datetime(ev["start"]), parse_aware_datetime(ev["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if overlaps(start, end, es, ee):
            conflicts.append({"id": ev.get("id"), "title": ev.get("title") or "(no title)", "start": ev["start"], "end": ev["end"]})
    return conflicts


def _availability_block(conflicts: list[dict[str, Any]], tz) -> dict[str, Any]:
    if not conflicts:
        return {"free": True, "conflicts": []}
    return {
        "free": False,
        "conflicts": [
            f"{c['title']} ({format_range(parse_aware_datetime(c['start']), parse_aware_datetime(c['end']), tz)})"
            for c in conflicts
        ],
    }


def _reject_conflicts(conflicts: list[dict[str, Any]], tz) -> None:
    listed = "; ".join(_availability_block(conflicts, tz)["conflicts"])
    raise PreviewRejected(
        f"That time conflicts with: {listed}. Tell the user about the conflict and suggest another time "
        "(find_free_slots can help). Only if the user explicitly agrees to double-book, call again with "
        "allow_conflict=true."
    )


# ---------- write preparation ----------

async def prepare_create_event(ctx: ToolContext, args: CreateCalendarEventArgs) -> PreparedWrite:
    profile = await ctx.get_profile()
    tz = get_zone(profile.get("time_zone"))
    start, end = parse_aware_datetime(args.start), parse_aware_datetime(args.end)
    conflicts = await find_conflicts(ctx, start, end)
    warnings: list[str] = []
    if conflicts:
        if not args.allow_conflict:
            _reject_conflicts(conflicts, tz)
        warnings.append("This overlaps other events. You asked to book it anyway.")
    notify = bool(args.attendees) and args.notify_attendees
    proposed = {"title": args.title, "when": format_range(start, end, tz)}
    if args.location:
        proposed["location"] = args.location
    if args.description:
        proposed["description"] = args.description
    proposed["attendees"] = ", ".join(args.attendees) or "(none)"
    return PreparedWrite(
        preview=Preview(
            title="Create calendar event",
            summary=f"Add '{args.title}' on {format_range(start, end, tz)}.",
            proposed=proposed,
            recipients=args.attendees,
            availability=_availability_block(conflicts, tz),
            notify_attendees=notify if args.attendees else None,
            warnings=warnings,
        ),
        bridge_operation="create_calendar_event",
        bridge_args={
            "title": args.title,
            "start": args.start,
            "end": args.end,
            "description": args.description or "",
            "location": args.location or "",
            "attendees": args.attendees,
            "send_updates": "all" if notify else "none",
        },
    )


async def prepare_update_event(ctx: ToolContext, args: UpdateCalendarEventArgs) -> PreparedWrite:
    profile = await ctx.get_profile()
    tz = get_zone(profile.get("time_zone"))
    existing = parse_event(await ctx.bridge.call("get_calendar_event", {"event_id": args.event_id}))

    changes: dict[str, Any] = {}
    old_view: dict[str, Any] = {}
    new_view: dict[str, Any] = {}
    availability = None
    warnings: list[str] = []

    if args.start is not None or args.end is not None:
        if existing["all_day"]:
            raise PreviewRejected("This is an all-day event; changing its time isn't supported here.")
        old_start, old_end = parse_aware_datetime(existing["start"]), parse_aware_datetime(existing["end"])
        new_start = parse_aware_datetime(args.start) if args.start else old_start
        if args.end:
            new_end = parse_aware_datetime(args.end)
        else:
            new_end = new_start + (old_end - old_start)  # FR-24: keep the duration
        if new_end <= new_start:
            raise PreviewRejected("The new end time must be after the new start time.")
        if (new_start, new_end) != (old_start, old_end):
            conflicts = await find_conflicts(ctx, new_start, new_end, exclude_id=args.event_id)
            if conflicts and not args.allow_conflict:
                _reject_conflicts(conflicts, tz)
            if conflicts:
                warnings.append("The new time overlaps other events. You asked to move it anyway.")
            availability = _availability_block(conflicts, tz)
            changes["start"], changes["end"] = new_start.isoformat(), new_end.isoformat()
            old_view["when"] = format_range(old_start, old_end, tz)
            new_view["when"] = format_range(new_start, new_end, tz)
            old_dur, new_dur = old_end - old_start, new_end - new_start
            new_view["duration"] = f"{int(new_dur.total_seconds() // 60)} minutes"
            if old_dur != new_dur:
                old_view["duration"] = f"{int(old_dur.total_seconds() // 60)} minutes"

    for f in ("title", "description", "location"):
        val = getattr(args, f)
        if val is not None and val != existing[f]:
            changes[f] = val
            old_view[f], new_view[f] = existing[f] or "(empty)", val or "(empty)"

    old_attendees = _attendee_emails(existing)
    if args.attendees is not None and sorted(a.lower() for a in args.attendees) != sorted(a.lower() for a in old_attendees):
        changes["attendees"] = args.attendees
        old_view["attendees"] = ", ".join(old_attendees) or "(none)"
        new_view["attendees"] = ", ".join(args.attendees) or "(none)"

    if not changes:
        raise PreviewRejected("Those values are the same as the current event; nothing to update.")

    all_attendees = args.attendees if args.attendees is not None else old_attendees
    notify = bool(all_attendees) and args.notify_attendees
    if notify:
        warnings.append(BRIDGE_EMAIL_NOTE)
    other = [label for key, label in (("title", "title"), ("description", "description"),
                                      ("location", "location"), ("attendees", "attendees")) if key in new_view]
    if "when" in new_view:
        summary = f"Move '{existing['title']}' to {new_view['when']}"
        summary += f" and change its {', '.join(other)}." if other else "."
    else:
        summary = f"Change the {', '.join(other)} of '{existing['title']}'."
    return PreparedWrite(
        preview=Preview(
            title="Update calendar event",
            summary=summary,
            target={
                "event": existing["title"],
                "when": format_range(parse_aware_datetime(existing["start"]), parse_aware_datetime(existing["end"]), tz)
                if not existing["all_day"] else "All day",
            },
            existing=old_view,
            proposed=new_view,
            recipients=all_attendees,
            availability=availability,
            notify_attendees=notify if all_attendees else None,
            warnings=warnings,
        ),
        bridge_operation="update_calendar_event",
        bridge_args={"event_id": args.event_id, **changes, "send_updates": "all" if notify else "none"},
    )


async def prepare_delete_event(ctx: ToolContext, args: DeleteCalendarEventArgs) -> PreparedWrite:
    profile = await ctx.get_profile()
    tz = get_zone(profile.get("time_zone"))
    existing = parse_event(await ctx.bridge.call("get_calendar_event", {"event_id": args.event_id}))
    attendees = _attendee_emails(existing)
    notify = bool(attendees) and args.notify_attendees
    when = (
        "All day"
        if existing["all_day"]
        else format_range(parse_aware_datetime(existing["start"]), parse_aware_datetime(existing["end"]), tz)
    )
    warnings = ["Cancelled events can't be restored from this app."]
    if notify:
        warnings.append(BRIDGE_EMAIL_NOTE.replace("a short email", "a cancellation email"))
    return PreparedWrite(
        preview=Preview(
            title="Cancel calendar event",
            summary=f"Cancel '{existing['title']}' ({when}).",
            target={"event": existing["title"], "when": when},
            existing={"title": existing["title"], "when": when, "attendees": ", ".join(attendees) or "(none)"},
            recipients=attendees,
            notify_attendees=notify if attendees else None,
            warnings=warnings,
            irreversible=True,
        ),
        bridge_operation="delete_calendar_event",
        bridge_args={"event_id": args.event_id, "send_updates": "all" if notify else "none"},
    )
