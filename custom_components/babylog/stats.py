"""Statistics for the Junior panel, computed from the stored records (not from
sensors or the recorder), so history reaches back as far as the app has data.

Days are local calendar days. Sessions crossing midnight are split across the
days they cover; an active session runs to "now".
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import KIND_BOTTLE, KIND_COMMENT, KIND_DIAPER, KIND_FEEDING, KIND_SLEEP
from .store import BabyLogStore, Record
from .totals import baseline_days, milk_between, pct, sleep_between

# Sleep inside [NIGHT_START, 24:00) or [00:00, NIGHT_END) counts as night sleep.
NIGHT_START = time(19, 0)
NIGHT_END = time(7, 0)
FEED_KINDS = (KIND_FEEDING, KIND_BOTTLE)
SESSION_KINDS = (KIND_FEEDING, KIND_BOTTLE, KIND_SLEEP)


def _minutes(delta: timedelta) -> float:
    return delta.total_seconds() / 60


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> timedelta:
    return max(timedelta(), min(a1, b1) - max(a0, b0))


def _feed_type(r: Record) -> str:
    if r.kind == KIND_BOTTLE:
        return "bottle"
    return r.side if r.side in ("left", "right") else "breast"


def compute_stats(
    store: BabyLogStore, baby_id: str, start: date, days: int, now: datetime
) -> dict[str, Any]:
    tz = dt_util.get_default_time_zone()
    bounds = [
        datetime.combine(start + timedelta(days=i), time.min, tzinfo=tz)
        for i in range(days + 1)
    ]
    range_start, range_end = bounds[0], bounds[-1]

    def end_of(r: Record) -> datetime:
        if r.kind in SESSION_KINDS:
            return r.ended_at or max(now, r.started_at)
        return r.started_at

    records = sorted(
        (
            r
            for r in store.for_baby(baby_id)
            if r.started_at < range_end and end_of(r) >= range_start
        ),
        key=lambda r: r.started_at,
    )

    out_days: list[dict[str, Any]] = []
    for i in range(days):
        d0, d1 = bounds[i], bounds[i + 1]
        out_days.append(
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "elapsed": d0 <= now,
                "milk_ml": 0,
                "bottles": 0,
                "feeds": 0,
                "breast_feeds": 0,
                "feed_types": {"bottle": 0, "left": 0, "right": 0, "breast": 0},
                "feed_min": 0.0,
                "sleep_min": 0.0,
                "night_sleep_min": 0.0,
                "day_sleep_min": 0.0,
                "longest_sleep_min": 0.0,
                "sleeps": 0,
                "diapers": 0,
                "wet": 0,
                "poopy": 0,
            }
        )

    segments: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for r in records:
        r_end = end_of(r)
        for i in range(days):
            d0, d1 = bounds[i], bounds[i + 1]
            day = out_days[i]
            started_today = d0 <= r.started_at < d1

            if r.kind in SESSION_KINDS:
                seg_start, seg_end = max(r.started_at, d0), min(r_end, d1)
                if seg_end < seg_start or (seg_end == seg_start and not started_today):
                    continue
                length = _minutes(seg_end - seg_start)
                segments.append(
                    {
                        "day": i,
                        "kind": r.kind,
                        "type": _feed_type(r) if r.kind in FEED_KINDS else "sleep",
                        "start": round(_minutes(seg_start - d0), 1),
                        "end": round(_minutes(seg_end - d0), 1),
                        "active": r.ended_at is None,
                        "amount_ml": r.amount_ml if started_today else None,
                    }
                )
                if r.kind == KIND_SLEEP:
                    night = _overlap(
                        seg_start, seg_end, d0, datetime.combine(d0.date(), NIGHT_END, tzinfo=tz)
                    ) + _overlap(
                        seg_start, seg_end, datetime.combine(d0.date(), NIGHT_START, tzinfo=tz), d1
                    )
                    day["sleep_min"] += length
                    day["night_sleep_min"] += _minutes(night)
                    if started_today:
                        day["sleeps"] += 1
                        day["longest_sleep_min"] = max(
                            day["longest_sleep_min"], _minutes(r_end - r.started_at)
                        )
                else:
                    day["feed_min"] += length
                    if started_today:
                        day["feeds"] += 1
                        day["feed_types"][_feed_type(r)] += 1
                        if r.kind == KIND_BOTTLE:
                            day["bottles"] += 1
                            day["milk_ml"] += r.amount_ml or 0
                        else:
                            day["breast_feeds"] += 1
            elif started_today:
                event = {"day": i, "kind": r.kind, "minute": round(_minutes(r.started_at - d0), 1)}
                if r.kind == KIND_DIAPER:
                    event.update(wet=r.wet, pooped=r.pooped)
                    day["diapers"] += 1
                    day["wet"] += int(r.wet)
                    day["poopy"] += int(r.pooped)
                elif r.kind == KIND_COMMENT:
                    event["comment"] = r.comment
                events.append(event)

    for day in out_days:
        day["day_sleep_min"] = day["sleep_min"] - day["night_sleep_min"]
        for key in ("feed_min", "sleep_min", "night_sleep_min", "day_sleep_min", "longest_sleep_min"):
            day[key] = round(day[key])

    feed_starts = [r.started_at for r in records if r.kind in FEED_KINDS and r.started_at >= range_start]
    gaps = [_minutes(b - a) for a, b in zip(feed_starts, feed_starts[1:])]
    elapsed = [d for d in out_days if d["elapsed"]] or out_days
    n = len(elapsed)

    def total(key: str) -> int:
        return sum(d[key] for d in out_days)

    # Per-day average of the range vs the week before it (for a single day:
    # that day, so far if it's today).
    baseline = baseline_days(store, baby_id, start)
    summary = {
        "milk_pct": pct(
            total("milk_ml") / n, [milk_between(store, baby_id, a, b) for a, b in baseline]
        ),
        "sleep_pct": pct(
            total("sleep_min") / n, [sleep_between(store, baby_id, a, b) for a, b in baseline]
        ),
        "milk_ml": total("milk_ml"),
        "milk_ml_per_day": round(total("milk_ml") / n),
        "bottles": total("bottles"),
        "feeds": total("feeds"),
        "feeds_per_day": round(total("feeds") / n, 1),
        "avg_feed_interval_min": round(sum(gaps) / len(gaps)) if gaps else None,
        "sleep_min_per_day": round(total("sleep_min") / n),
        "night_sleep_min_per_day": round(total("night_sleep_min") / n),
        "longest_sleep_min": max((d["longest_sleep_min"] for d in out_days), default=0),
        "diapers": total("diapers"),
        "diapers_per_day": round(total("diapers") / n, 1),
        "wet": total("wet"),
        "poopy": total("poopy"),
        "feed_types": {
            key: sum(d["feed_types"][key] for d in out_days)
            for key in ("bottle", "left", "right", "breast")
        },
        "night_sleep_min": total("night_sleep_min"),
        "day_sleep_min": total("day_sleep_min"),
    }

    return {
        "baby": {"id": baby_id, "name": store.babies.get(baby_id)},
        "babies": [{"id": i, "name": name} for i, name in store.babies.items()],
        "start": start.isoformat(),
        "days": out_days,
        "segments": segments,
        "events": events,
        "summary": summary,
        "night": {"start": NIGHT_START.strftime("%H:%M"), "end": NIGHT_END.strftime("%H:%M")},
        "now": now.isoformat(),
    }
