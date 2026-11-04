"""Pure scheduling logic: free-slot search and conflict detection (FR-22, FR-26)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WORK_START = time(9, 0)
WORK_END = time(18, 0)
SLOT_STEP = timedelta(minutes=30)
MAX_SUGGESTIONS = 5

Interval = tuple[datetime, datetime]


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def find_free_slots(
    busy: Iterable[Interval],
    first_day: date,
    last_day: date,
    duration: timedelta,
    tz: ZoneInfo,
    now: datetime,
    max_results: int = MAX_SUGGESTIONS,
) -> list[Interval]:
    """Weekday slots between 09:00 and 18:00 local time, on 30-minute boundaries,
    that don't overlap any busy interval and start after `now`."""
    max_results = max(1, min(max_results, MAX_SUGGESTIONS))
    busy_list = sorted(busy)
    slots: list[Interval] = []
    day = first_day
    while day <= last_day and len(slots) < max_results:
        if day.weekday() < 5:  # Monday-Friday
            cursor = datetime.combine(day, WORK_START, tzinfo=tz)
            day_end = datetime.combine(day, WORK_END, tzinfo=tz)
            while cursor + duration <= day_end and len(slots) < max_results:
                end = cursor + duration
                if cursor >= now and not any(overlaps(cursor, end, b0, b1) for b0, b1 in busy_list):
                    slots.append((cursor, end))
                cursor += SLOT_STEP
        day += timedelta(days=1)
    return slots


def format_range(start: datetime, end: datetime, tz: ZoneInfo) -> str:
    s, e = start.astimezone(tz), end.astimezone(tz)
    if s.date() == e.date():
        return f"{s:%a %d %b %Y, %H:%M}–{e:%H:%M} ({tz.key})"
    return f"{s:%a %d %b %Y, %H:%M} – {e:%a %d %b %Y, %H:%M} ({tz.key})"
