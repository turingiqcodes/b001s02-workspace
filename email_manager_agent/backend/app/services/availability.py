"""Free-slot search and working-hours checks (pure functions, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from ..config import Settings

GRID_MINUTES = 15


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def work_window(day: date, settings: Settings) -> Interval:
    tz = settings.tz
    return Interval(datetime.combine(day, _hm(settings.work_start), tz),
                    datetime.combine(day, _hm(settings.work_end), tz))


def round_up(dt: datetime, minutes: int = GRID_MINUTES) -> datetime:
    dt = dt.replace(second=0, microsecond=0) + (timedelta(minutes=1) if dt.second or dt.microsecond else timedelta())
    extra = (-dt.minute) % minutes
    return dt + timedelta(minutes=extra)


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: i.start):
        if iv.end <= iv.start:
            continue
        if merged and iv.start <= merged[-1].end:
            if iv.end > merged[-1].end:
                merged[-1] = Interval(merged[-1].start, iv.end)
        else:
            merged.append(iv)
    return merged


def blocks_time(ev: dict) -> bool:
    """Like Google's free/busy: all-day, cancelled, "show as free" and declined events don't block time."""
    return not (ev.get("all_day") or ev.get("status") == "cancelled"
                or ev.get("transparency") == "transparent" or ev.get("self_response") == "declined")


def busy_from_events(events: Iterable[dict], exclude_id: str | None = None) -> list[Interval]:
    out = []
    for ev in events:
        if not blocks_time(ev) or (exclude_id and ev.get("id") == exclude_id):
            continue
        try:
            start = datetime.fromisoformat(str(ev["start"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(ev["end"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start.tzinfo is None or end.tzinfo is None:
            continue
        out.append(Interval(start, end))
    return out


def find_free_slots(*, busy: list[Interval], duration_minutes: int, date_from: date, date_to: date,
                    settings: Settings, now: datetime, max_results: int = 5) -> list[Interval]:
    """Free slots inside working hours on working days, on a 15-minute grid, never in the past.

    Long free gaps yield several back-to-back options.
    """
    duration = timedelta(minutes=duration_minutes)
    busy = merge(busy)
    slots: list[Interval] = []
    day = date_from
    while day <= date_to and len(slots) < max_results:
        if day.weekday() in settings.work_days:
            window = work_window(day, settings)
            cursor = round_up(max(window.start, now))
            for b in busy + [Interval(window.end, window.end + timedelta(minutes=1))]:
                if b.end <= cursor:
                    continue
                gap_end = min(b.start, window.end)
                while cursor + duration <= gap_end and len(slots) < max_results:
                    slots.append(Interval(cursor, cursor + duration))
                    cursor = round_up(cursor + duration)
                cursor = round_up(max(cursor, b.end))
                if cursor >= window.end or len(slots) >= max_results:
                    break
        day += timedelta(days=1)
    return slots


def outside_working_hours(start: datetime, end: datetime, settings: Settings) -> str | None:
    """A warning if the event isn't fully inside working hours on a working day (user's timezone)."""
    local_start, local_end = start.astimezone(settings.tz), end.astimezone(settings.tz)
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if local_start.weekday() not in settings.work_days:
        return f"{names[local_start.weekday()]} is outside your working days."
    window = work_window(local_start.date(), settings)
    if local_start < window.start or local_end > window.end:
        return (f"This is outside your working hours ({settings.work_start}–{settings.work_end} "
                f"{settings.user_timezone}).")
    return None
