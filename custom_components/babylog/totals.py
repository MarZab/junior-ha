"""Today's milk and sleep, and how they compare with a typical day last week.

`*_pct` is today so far as a percentage of the average full day over the
previous 7 days (only days with any entries count, so a gap in logging doesn't
drag the average down). Recomputed on every change and at midnight.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import KIND_BOTTLE, KIND_FEEDING, KIND_SLEEP
from .store import BabyLogStore

BASELINE_DAYS = 7


def milk_between(store: BabyLogStore, baby_id: str, start: datetime, end: datetime) -> int:
    return sum(
        r.amount_ml or 0
        for r in store.for_baby(baby_id)
        if r.kind == KIND_BOTTLE and start <= r.started_at < end
    )


def sleep_between(store: BabyLogStore, baby_id: str, start: datetime, end: datetime) -> float:
    """Finished sleep overlapping [start, end)."""
    total = timedelta()
    for r in store.for_baby(baby_id):
        if r.kind != KIND_SLEEP or r.ended_at is None:
            continue
        overlap = min(r.ended_at, end) - max(r.started_at, start)
        if overlap > timedelta():
            total += overlap
    return total.total_seconds() / 60


def pct(today: float, baseline: list[float]) -> int | None:
    if not baseline or (avg := sum(baseline) / len(baseline)) <= 0:
        return None
    return round(100 * today / avg)


def baseline_days(store: BabyLogStore, baby_id: str, before: date) -> list[tuple[datetime, datetime]]:
    """The (up to) 7 local days before `before` that have any entries."""
    tz = dt_util.get_default_time_zone()
    records = store.for_baby(baby_id)
    days = []
    for back in range(1, BASELINE_DAYS + 1):
        d0 = datetime.combine(before - timedelta(days=back), time.min, tz)
        d1 = d0 + timedelta(days=1)
        if any(d0 <= r.started_at < d1 for r in records):
            days.append((d0, d1))
    return days


def today_totals(store: BabyLogStore, baby_id: str) -> dict[str, dict[str, Any]]:
    """{"feeding": {...attrs}, "sleeping": {...attrs}}"""
    tz = dt_util.get_default_time_zone()
    today = dt_util.now().date()
    start = datetime.combine(today, time.min, tz)
    end = start + timedelta(days=1)

    records = store.for_baby(baby_id)
    past_days = baseline_days(store, baby_id, today)

    milk_today = milk_between(store, baby_id, start, end)
    sleep_today = sleep_between(store, baby_id, start, end)
    feeds_today = sum(
        1 for r in records if r.kind in (KIND_FEEDING, KIND_BOTTLE) and start <= r.started_at < end
    )
    return {
        "feeding": {
            "milk_today_ml": milk_today,
            "milk_today_pct": pct(milk_today, [milk_between(store, baby_id, a, b) for a, b in past_days]),
            "feeds_today": feeds_today,
        },
        "sleeping": {
            "sleep_today_min": round(sleep_today),
            "sleep_today_pct": pct(
                sleep_today, [sleep_between(store, baby_id, a, b) for a, b in past_days]
            ),
        },
    }
