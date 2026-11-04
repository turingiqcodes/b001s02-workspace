from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.services import availability as av
from app.services.availability import Interval
from tests.conftest import make_settings

UTC = timezone.utc


def dt(s: str, tz: object = UTC) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=tz)  # type: ignore[arg-type]


def slots(busy: list[Interval], *, minutes: int = 30, d1: str = "2026-11-03", d2: str = "2026-11-03",
          now: str = "2026-11-01T00:00:00", settings: object = None, n: int = 5) -> list[tuple[str, str]]:
    s = settings or make_settings()
    out = av.find_free_slots(busy=busy, duration_minutes=minutes, date_from=date.fromisoformat(d1),
                             date_to=date.fromisoformat(d2), settings=s, now=dt(now), max_results=n)  # type: ignore[arg-type]
    return [(x.start.strftime("%H:%M"), x.end.strftime("%H:%M")) for x in out]


def test_empty_day_gives_back_to_back_slots() -> None:
    assert slots([], minutes=60, n=3) == [("09:00", "10:00"), ("10:00", "11:00"), ("11:00", "12:00")]


def test_busy_time_is_skipped() -> None:
    busy = [Interval(dt("2026-11-03T09:00"), dt("2026-11-03T10:10"))]
    assert slots(busy, minutes=30, n=2) == [("10:15", "10:45"), ("10:45", "11:15")]


def test_gap_too_small_is_skipped() -> None:
    busy = [Interval(dt("2026-11-03T09:00"), dt("2026-11-03T10:00")),
            Interval(dt("2026-11-03T10:30"), dt("2026-11-03T17:30"))]
    assert slots(busy, minutes=45) == []


def test_slot_must_fit_before_work_end() -> None:
    busy = [Interval(dt("2026-11-03T09:00"), dt("2026-11-03T17:30"))]
    assert slots(busy, minutes=30) == [("17:30", "18:00")]
    assert slots(busy, minutes=45) == []


def test_overlapping_busy_merged() -> None:
    merged = av.merge([Interval(dt("2026-11-03T10:00"), dt("2026-11-03T11:00")),
                       Interval(dt("2026-11-03T10:30"), dt("2026-11-03T12:00")),
                       Interval(dt("2026-11-03T13:00"), dt("2026-11-03T13:00"))])
    assert merged == [Interval(dt("2026-11-03T10:00"), dt("2026-11-03T12:00"))]


def test_past_times_skipped_and_rounded_to_grid() -> None:
    assert slots([], now="2026-11-03T14:07:30", n=1) == [("14:15", "14:45")]


def test_weekend_skipped() -> None:
    # 2026-11-07 is Saturday, 2026-11-09 Monday
    s = make_settings()
    out = av.find_free_slots(busy=[], duration_minutes=60, date_from=date(2026, 11, 7), date_to=date(2026, 11, 9),
                             settings=s, now=dt("2026-11-01T00:00"), max_results=1)
    assert out[0].start.date() == date(2026, 11, 9)


def test_custom_work_days_and_hours() -> None:
    s = make_settings(work_days=(5,), work_start="10:00", work_end="12:00")
    out = av.find_free_slots(busy=[], duration_minutes=120, date_from=date(2026, 11, 2), date_to=date(2026, 11, 8),
                             settings=s, now=dt("2026-11-01T00:00"), max_results=5)
    assert [(o.start.isoformat()) for o in out] == ["2026-11-07T10:00:00+00:00"]


def test_timezone_working_hours() -> None:
    s = make_settings(user_timezone="Asia/Kolkata")
    out = av.find_free_slots(busy=[], duration_minutes=30, date_from=date(2026, 11, 3), date_to=date(2026, 11, 3),
                             settings=s, now=dt("2026-11-01T00:00"), max_results=1)
    assert out[0].start.isoformat() == "2026-11-03T09:00:00+05:30"
    # Busy given in UTC (03:30Z == 09:00 IST) blocks the local morning.
    busy = [Interval(dt("2026-11-03T03:30"), dt("2026-11-03T04:30"))]
    out = av.find_free_slots(busy=busy, duration_minutes=30, date_from=date(2026, 11, 3), date_to=date(2026, 11, 3),
                             settings=s, now=dt("2026-11-01T00:00"), max_results=1)
    assert out[0].start.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M") == "10:00"


def test_busy_from_events_filters_non_blocking() -> None:
    events = [
        {"id": "a", "start": "2026-11-03T10:00:00+00:00", "end": "2026-11-03T11:00:00+00:00"},
        {"id": "b", "start": "2026-11-03", "end": "2026-11-04", "all_day": True},
        {"id": "c", "start": "2026-11-03T12:00:00+00:00", "end": "2026-11-03T13:00:00+00:00", "status": "cancelled"},
        {"id": "d", "start": "2026-11-03T12:00:00+00:00", "end": "2026-11-03T13:00:00+00:00", "transparency": "transparent"},
        {"id": "e", "start": "2026-11-03T12:00:00+00:00", "end": "2026-11-03T13:00:00+00:00", "self_response": "declined"},
        {"id": "f", "start": "2026-11-03T14:00:00Z", "end": "2026-11-03T15:00:00Z"},
        {"id": "g", "start": "garbage", "end": "x"},
    ]
    busy = av.busy_from_events(events, exclude_id="f")
    assert [b.start.hour for b in busy] == [10]


def test_outside_working_hours() -> None:
    s = make_settings(user_timezone="Asia/Kolkata")
    assert av.outside_working_hours(dt("2026-11-03T04:00"), dt("2026-11-03T05:00"), s) is None  # 09:30 IST
    assert "working hours" in (av.outside_working_hours(dt("2026-11-03T13:00"), dt("2026-11-03T14:00"), s) or "")
    assert "Saturday" in (av.outside_working_hours(dt("2026-11-07T05:00"), dt("2026-11-07T06:00"), s) or "")


def test_round_up() -> None:
    assert av.round_up(dt("2026-11-03T10:00")).strftime("%H:%M") == "10:00"
    assert av.round_up(dt("2026-11-03T10:00:01")).strftime("%H:%M") == "10:15"
    assert av.round_up(dt("2026-11-03T10:46")).strftime("%H:%M") == "11:00"
