"""Google Calendar: read tools, free slots, approval previews, conflict checks."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..errors import BridgeError
from . import availability
from .base import Context, Proposal, ProposalRejected, fmt_time, join, short

CANCEL_WARNING = "This cancels the event for everyone invited."
BRIDGE_PAGE = 250  # the bridge returns at most 250 events per list_events call


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_events(ctx: Context, start: datetime, end: datetime, depth: int = 0) -> list[dict[str, Any]]:
    """All events in [start, end). Splits the range when a page comes back full."""
    page = BRIDGE_PAGE
    data = ctx.bridge.call("list_events", {"start": start.isoformat(), "end": end.isoformat(), "max_results": page})
    events = list((data or {}).get("events") or [])
    if len(events) < page or depth >= 6 or end - start <= timedelta(hours=1):
        return events
    mid = start + (end - start) / 2
    seen: dict[str, dict[str, Any]] = {}
    for ev in fetch_events(ctx, start, mid, depth + 1) + fetch_events(ctx, mid, end, depth + 1):
        seen.setdefault(str(ev.get("id")), ev)
    return sorted(seen.values(), key=lambda e: str(e.get("start")))


# ------------------------------------------------------------------- reads
def list_events(ctx: Context, a: dict[str, Any]) -> Any:
    params = {"start": a["start"], "end": a["end"], "max_results": a.get("max_results", 25)}
    if a.get("query"):
        params["query"] = a["query"]
    return ctx.bridge.call("list_events", params)


def find_free_slots(ctx: Context, a: dict[str, Any]) -> Any:
    s = ctx.settings
    d1, d2 = date.fromisoformat(a["date_from"]), date.fromisoformat(a["date_to"])
    start = availability.work_window(d1, s).start.replace(hour=0, minute=0)
    end = availability.work_window(d2, s).start.replace(hour=0, minute=0) + timedelta(days=1)
    busy = availability.busy_from_events(fetch_events(ctx, start, end))
    slots = availability.find_free_slots(busy=busy, duration_minutes=a["duration_minutes"], date_from=d1, date_to=d2,
                                         settings=s, now=ctx.now(), max_results=a.get("max_results", 5))
    return {
        "timezone": s.user_timezone,
        "working_hours": f"{s.work_start}-{s.work_end}",
        "slots": [{"start": sl.start.isoformat(), "end": sl.end.isoformat()} for sl in slots],
        "note": None if slots else "No free slot of that length in working hours in this range.",
    }


READS = {"list_events": list_events, "find_free_slots": find_free_slots}


# ---------------------------------------------------------------- warnings
def conflict_warnings(ctx: Context, start: str, end: str, exclude_id: str | None = None) -> list[str]:
    s, e = _dt(start), _dt(end)
    try:
        events = fetch_events(ctx, s, e)
    except BridgeError:
        return ["Couldn't check your calendar for conflicts."]
    out = []
    for ev in events:
        if not availability.blocks_time(ev) or ev.get("id") == exclude_id:
            continue
        try:
            es, ee = _dt(str(ev["start"])), _dt(str(ev["end"]))
        except (KeyError, ValueError):
            continue
        if es < e and ee > s:
            out.append(f'Conflicts with "{short(ev.get("title") or "(no title)", 60)}" ({fmt_time(str(ev["start"]), ctx.settings)}).')
    return out


def _time_warnings(ctx: Context, start: str, end: str) -> list[str]:
    warnings = []
    w = availability.outside_working_hours(_dt(start), _dt(end), ctx.settings)
    if w:
        warnings.append(w)
    if _dt(start) < ctx.now():
        warnings.append("This time is in the past.")
    return warnings


# ---------------------------------------------------------------- previews
def p_create_event(ctx: Context, a: dict[str, Any]) -> Proposal:
    details = [{"label": "Title", "value": a["title"]},
               {"label": "Start", "value": fmt_time(a["start"], ctx.settings)},
               {"label": "End", "value": fmt_time(a["end"], ctx.settings)},
               {"label": "Guests", "value": join(a.get("attendees"))}]
    if a.get("location"):
        details.append({"label": "Location", "value": a["location"]})
    warnings = conflict_warnings(ctx, a["start"], a["end"]) + _time_warnings(ctx, a["start"], a["end"])
    return Proposal("Create event", f'"{short(a["title"], 60)}" on {fmt_time(a["start"], ctx.settings)}', a,
                    details, a.get("description"), warnings)


def p_move_event(ctx: Context, a: dict[str, Any]) -> Proposal:
    try:
        event = ctx.bridge.call("get_event", {"event_id": a["event_id"]})
    except BridgeError as exc:
        raise ProposalRejected(f"Couldn't load that event: {exc.message}") from exc
    if not isinstance(event, dict) or not event.get("start") or not event.get("end"):
        raise ProposalRejected("Couldn't load that event's current time.")
    if event.get("all_day"):
        raise ProposalRejected("All-day events can't be moved with this tool.")
    params = dict(a)
    if "new_end" not in params:
        duration = _dt(event["end"]) - _dt(event["start"])
        params["new_end"] = (_dt(params["new_start"]) + duration).isoformat()
    s = ctx.settings
    details = [{"label": "Event", "value": event.get("title") or "(no title)"},
               {"label": "From", "value": f'{fmt_time(event["start"], s)} – {fmt_time(event["end"], s)}'},
               {"label": "To", "value": f'{fmt_time(params["new_start"], s)} – {fmt_time(params["new_end"], s)}'}]
    if event.get("attendees"):
        details.append({"label": "Guests", "value": join(event["attendees"])})
    warnings = (conflict_warnings(ctx, params["new_start"], params["new_end"], exclude_id=a["event_id"])
                + _time_warnings(ctx, params["new_start"], params["new_end"]))
    return Proposal("Move event", f'Move "{short(event.get("title") or "(no title)", 60)}" to '
                    f'{fmt_time(params["new_start"], s)}', params, details, None, warnings)


def p_cancel_event(ctx: Context, a: dict[str, Any]) -> Proposal:
    params = {"event_id": a["event_id"], "notify_attendees": a.get("notify_attendees", True)}
    try:
        event = ctx.bridge.call("get_event", {"event_id": a["event_id"]})
    except BridgeError as exc:
        raise ProposalRejected(f"Couldn't load that event: {exc.message}") from exc
    title = (event or {}).get("title") or "(no title)"
    details = [{"label": "Event", "value": title},
               {"label": "When", "value": fmt_time(str((event or {}).get("start", "")), ctx.settings)},
               {"label": "Notify guests", "value": "Yes" if params["notify_attendees"] else "No"}]
    if (event or {}).get("attendees"):
        details.append({"label": "Guests", "value": join(event["attendees"])})
    return Proposal("Cancel event", f'Cancel "{short(title, 60)}"', params, details, None, [CANCEL_WARNING])


PREVIEWS = {"create_event": p_create_event, "move_event": p_move_event, "cancel_event": p_cancel_event}

DONE = {"create_event": "Event created.", "move_event": "Event moved.", "cancel_event": "Event cancelled."}


def recheck_conflicts(ctx: Context, tool: str, params: dict[str, Any]) -> list[str]:
    """Conflict warnings right before running a create/move (calendars change while the card is open)."""
    if tool == "create_event":
        return [w for w in conflict_warnings(ctx, params["start"], params["end"]) if w.startswith("Conflicts")]
    if tool == "move_event":
        return [w for w in conflict_warnings(ctx, params["new_start"], params["new_end"], exclude_id=params["event_id"])
                if w.startswith("Conflicts")]
    return []
