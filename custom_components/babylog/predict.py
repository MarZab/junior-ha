"""Predict the next sleep (nap / bedtime / wake-up) and the next feed.

Everything is a window from recent history, not a single time: each band is
the 15th/50th/85th percentile of the relevant interval, anchored on the last
event. Backtested on several months of real logs, the real time lands inside
it about two times in three, with windows ~1.5 h wide (20–80%: ~55%, ~1.25 h;
10–90%: ~70%, ~2 h). Baselines
are rolling (7 days for sleep, 3 days for feeds) because the pattern moves
quickly at this age, and night hours are learned from the baby's actual bedtime and
morning wake rather than fixed.

Observations from real logs that shaped this:
- A feed and a bottle top-up are one feed; sleeps split by < 10 min are one
  sleep. Daytime wake windows > 6 h are almost always unlogged naps.
- Most night feeds start within minutes of waking, so at night the next feed
  is predicted as "when the baby wakes".
- Daytime feeds and naps are largely independent of each other.

Pure functions over `Record`s so the same code backs the entity attributes,
the panel's accuracy line and the offline backtest (scripts/backtest.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .const import KIND_BOTTLE, KIND_FEEDING, KIND_SLEEP
from .store import Record

Q_LOW, Q_MID, Q_HIGH = 0.15, 0.5, 0.85

SLEEP_MERGE_MIN = 10
FEED_MERGE_MIN = 30
MAX_DAY_WAKE_MIN = 360
MAX_FEED_GAP_MIN = 600
SLEEP_DAYS = 7
FEED_DAYS = 3
MIN_SAMPLES = 5
MIN_NIGHTS = 3

# Night detection: an evening sleep starts the night if it is long or soon
# followed by more sleep; the night continues while gaps stay short.
EVENING_FROM = time(17, 0)
NIGHT_START_MIN_LEN = 90
NIGHT_CONTINUE_GAP = 150
DEFAULT_BEDTIME = 19 * 60 + 30
DEFAULT_WAKE = 7 * 60
# How early before the usual bedtime the evening counts as night.
BEDTIME_LEAD = 30


def _min(delta: timedelta) -> float:
    return delta.total_seconds() / 60


def _hm(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}m"


def _clock(minutes: float) -> str:
    m = round(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def quantiles(values: Iterable[float]) -> tuple[float, float, float] | None:
    xs = sorted(values)
    if len(xs) < MIN_SAMPLES:
        return None

    def q(p: float) -> float:
        pos = (len(xs) - 1) * p
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    return q(Q_LOW), q(Q_MID), q(Q_HIGH)


# MARK: episodes


@dataclass
class Episode:
    start: datetime
    end: datetime | None  # None while active
    records: list[Record] = field(default_factory=list)

    @property
    def minutes(self) -> float | None:
        return None if self.end is None else _min(self.end - self.start)


def episodes(records: Iterable[Record], kinds: Iterable[str], merge_min: float) -> list[Episode]:
    """Merge sessions that follow each other within `merge_min` (breast + top-up,
    a sleep interrupted for a moment) into one episode."""
    kinds = set(kinds)
    out: list[Episode] = []
    for r in sorted((r for r in records if r.kind in kinds), key=lambda r: r.started_at):
        if out and out[-1].end is not None and _min(r.started_at - out[-1].end) < merge_min:
            last = out[-1]
            last.end = None if r.ended_at is None else max(last.end, r.ended_at)
            last.records.append(r)
        elif out and out[-1].end is None and r.started_at >= out[-1].start:
            out[-1].records.append(r)  # overlapping entry while something is active
        else:
            out.append(Episode(r.started_at, r.ended_at, [r]))
    return out


# MARK: nights


@dataclass
class Night:
    bed: datetime
    wake: datetime
    stretches: list[float]  # sleep lengths, first one first
    gaps: list[float]  # awake time between them


@dataclass
class NightPattern:
    tz: tzinfo
    nights: list[Night]
    bedtime: tuple[float, float, float] | None  # minutes after midnight, may exceed 1440
    wake: tuple[float, float, float] | None

    @property
    def bed_mid(self) -> float:
        return self.bedtime[1] if self.bedtime else DEFAULT_BEDTIME

    @property
    def wake_mid(self) -> float:
        return self.wake[1] if self.wake else DEFAULT_WAKE

    def tod(self, t: datetime) -> float:
        local = t.astimezone(self.tz)
        return local.hour * 60 + local.minute + local.second / 60

    def is_night(self, t: datetime) -> bool:
        """Between (usual bedtime - lead) and usual morning wake."""
        m = self.tod(t)
        start = (self.bed_mid - BEDTIME_LEAD) % 1440
        end = self.wake_mid % 1440
        return (m >= start or m < end) if start > end else (start <= m < end)

    def at(self, day: date, minutes: float) -> datetime:
        return datetime.combine(day, time.min, self.tz) + timedelta(minutes=minutes)


def night_pattern(sleeps: list[Episode], now: datetime, tz: tzinfo) -> NightPattern:
    done = [s for s in sleeps if s.end is not None]
    today = now.astimezone(tz).date()
    nights: list[Night] = []
    for back in range(1, SLEEP_DAYS + 1):
        day = today - timedelta(days=back)
        d0 = datetime.combine(day, time.min, tz)
        evening, late = datetime.combine(day, EVENING_FROM, tz), d0 + timedelta(days=1, hours=2)
        bed = None
        for i, s in enumerate(done):
            if not evening <= s.start < late:
                continue
            nxt = done[i + 1] if i + 1 < len(done) else None
            if s.minutes >= NIGHT_START_MIN_LEN or (
                nxt and _min(nxt.start - s.end) < NIGHT_START_MIN_LEN
            ):
                bed = i
                break
        if bed is None:
            continue
        chain = [done[bed]]
        for s in done[bed + 1 :]:
            if _min(s.start - chain[-1].end) < NIGHT_CONTINUE_GAP and s.start < d0 + timedelta(days=1, hours=10):
                chain.append(s)
            else:
                break
        wake = chain[-1].end
        # Last night may still be going on (the baby could fall back asleep).
        if _min(now - wake) < NIGHT_CONTINUE_GAP and chain[-1] is done[-1]:
            continue
        if _min(wake - chain[0].start) < 240:
            continue  # not a night
        nights.append(
            Night(
                bed=chain[0].start,
                wake=wake,
                stretches=[s.minutes for s in chain],
                gaps=[_min(b.start - a.end) for a, b in zip(chain, chain[1:])],
            )
        )

    def tod(t: datetime) -> float:
        local = t.astimezone(tz)
        return local.hour * 60 + local.minute

    beds = [tod(n.bed) + (1440 if tod(n.bed) < 12 * 60 else 0) for n in nights]
    wakes = [tod(n.wake) for n in nights]
    enough = len(nights) >= MIN_NIGHTS
    q = lambda xs: quantiles(xs) if len(xs) >= MIN_SAMPLES else _small_quantiles(xs)  # noqa: E731
    return NightPattern(
        tz=tz,
        nights=nights,
        bedtime=q(beds) if enough else None,
        wake=q(wakes) if enough else None,
    )


def _small_quantiles(xs: list[float]) -> tuple[float, float, float] | None:
    """For 3-4 nights: min / median / max instead of interpolated percentiles."""
    if len(xs) < MIN_NIGHTS:
        return None
    s = sorted(xs)
    return s[0], s[len(s) // 2], s[-1]


# MARK: prediction


@dataclass
class Band:
    earliest: datetime
    likely: datetime
    latest: datetime
    basis: str

    @classmethod
    def after(cls, base: datetime, q: tuple[float, float, float], basis: str) -> Band:
        lo, mid, hi = q
        return cls(
            base + timedelta(minutes=lo),
            base + timedelta(minutes=mid),
            base + timedelta(minutes=hi),
            f"{basis}: {_hm(lo)}–{_hm(hi)}, usually {_hm(mid)}",
        )

    @classmethod
    def time_of_day(cls, pattern: NightPattern, day: date, q: tuple[float, float, float], basis: str) -> Band:
        lo, mid, hi = q
        return cls(
            pattern.at(day, lo),
            pattern.at(day, mid),
            pattern.at(day, hi),
            f"{basis}: {_clock(lo)}–{_clock(hi)}, usually {_clock(mid)}",
        )

    def contains(self, t: datetime) -> bool:
        return self.earliest <= t <= self.latest

    def as_attrs(self, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_at": self.likely.isoformat(),
            f"{prefix}_earliest": self.earliest.isoformat(),
            f"{prefix}_latest": self.latest.isoformat(),
            f"{prefix}_basis": self.basis,
        }


@dataclass
class Prediction:
    asleep: bool
    sleep: Band | None  # next sleep when awake, wake-up when asleep
    sleep_kind: str | None  # nap / bedtime / back_to_sleep | nap / night / morning
    feed: Band | None
    pattern: NightPattern

    def sleep_attrs(self) -> dict[str, Any]:
        prefix = "wake" if self.asleep else "next_sleep"
        attrs: dict[str, Any] = {
            "bedtime": _clock(self.pattern.bed_mid) if self.pattern.bedtime else None,
            "morning_wake": _clock(self.pattern.wake_mid) if self.pattern.wake else None,
            f"{prefix}_kind": self.sleep_kind,
        }
        if self.sleep:
            attrs.update(self.sleep.as_attrs(prefix))
        return attrs

    def feed_attrs(self) -> dict[str, Any]:
        return self.feed.as_attrs("next_feed") if self.feed else {}

    def as_dict(self) -> dict[str, Any]:
        def band(b: Band | None) -> dict[str, Any] | None:
            if b is None:
                return None
            return {
                "earliest": b.earliest.isoformat(),
                "likely": b.likely.isoformat(),
                "latest": b.latest.isoformat(),
                "basis": b.basis,
            }

        return {
            "asleep": self.asleep,
            "sleep_kind": self.sleep_kind,
            "sleep": band(self.sleep),
            "feed": band(self.feed),
            "bedtime": _clock(self.pattern.bed_mid) if self.pattern.bedtime else None,
            "morning_wake": _clock(self.pattern.wake_mid) if self.pattern.wake else None,
        }


def _since(eps: list[Episode], now: datetime, days: int) -> list[Episode]:
    cutoff = now - timedelta(days=days)
    return [e for e in eps if e.start >= cutoff]


def predict(records: list[Record], now: datetime, tz: tzinfo) -> Prediction:
    """`records`: one baby's live records as of `now` (active sessions have no end)."""
    horizon = now - timedelta(days=SLEEP_DAYS + 2)
    records = [r for r in records if r.started_at >= horizon or r.ended_at is None]
    sleeps = episodes(records, [KIND_SLEEP], SLEEP_MERGE_MIN)
    feeds = episodes(records, [KIND_FEEDING, KIND_BOTTLE], FEED_MERGE_MIN)
    pattern = night_pattern(sleeps, now, tz)
    recent_sleeps = [s for s in _since(sleeps, now, SLEEP_DAYS) if s.end is not None]

    day_naps = [s.minutes for s in recent_sleeps if not pattern.is_night(s.start)]
    wake_windows = [
        g
        for a, b in zip(recent_sleeps, recent_sleeps[1:])
        if not pattern.is_night(a.end) and 0 < (g := _min(b.start - a.end)) <= MAX_DAY_WAKE_MIN
    ]
    first_stretch = [n.stretches[0] for n in pattern.nights]
    later_stretch = [x for n in pattern.nights for x in n.stretches[1:]]
    night_gaps = [g for n in pattern.nights for g in n.gaps if g > 0]

    active = sleeps[-1] if sleeps and sleeps[-1].end is None else None
    last_done = next((s for s in reversed(sleeps) if s.end is not None), None)
    sleep_band: Band | None = None
    kind: str | None = None

    def morning_band(start: datetime) -> Band | None:
        if not pattern.wake:
            return None
        local = start.astimezone(tz)
        day = local.date() if local.hour < 12 else local.date() + timedelta(days=1)
        return Band.time_of_day(pattern, day, pattern.wake, f"morning wake, last {len(pattern.nights)} nights")

    if active is not None:
        if pattern.is_night(active.start):
            # First stretch of the night: the baby was up for the day (or long enough) before.
            first = (
                last_done is None
                or not pattern.is_night(last_done.end)
                or _min(active.start - last_done.end) >= NIGHT_CONTINUE_GAP
            )
            dist = quantiles(first_stretch if first else later_stretch) or quantiles(first_stretch + later_stretch)
            label = "first night stretch" if first else "night stretch"
            kind = "night"
            if dist:
                sleep_band = Band.after(active.start, dist, f"{label}, last {len(pattern.nights)} nights")
            morning = morning_band(active.start)
            if morning and (sleep_band is None or sleep_band.likely >= morning.earliest):
                sleep_band, kind = morning, "morning"
        else:
            kind = "nap"
            if q := quantiles(day_naps):
                sleep_band = Band.after(active.start, q, f"nap length, last {SLEEP_DAYS} days (n={len(day_naps)})")
    elif last_done is not None:
        woke = last_done.end
        nap = (
            Band.after(woke, q, f"wake window, last {SLEEP_DAYS} days (n={len(wake_windows)})")
            if (q := quantiles(wake_windows))
            else None
        )
        back_to_sleep = (
            Band.after(woke, q, f"awake at night, last {len(pattern.nights)} nights")
            if (q := quantiles(night_gaps))
            else None
        )
        if pattern.tod(now) < 12 * 60:
            # Morning: woke before the usual earliest morning wake -> probably
            # a night waking; otherwise the day has started.
            day_started = (
                pattern.wake is None
                or not pattern.is_night(woke)
                or pattern.tod(woke) >= pattern.wake[0]
            )
            sleep_band, kind = (nap, "nap") if day_started else (back_to_sleep, "back_to_sleep")
        else:
            today = now.astimezone(tz).date()
            bedtime = (
                Band.time_of_day(pattern, today, pattern.bedtime, f"bedtime, last {len(pattern.nights)} nights")
                if pattern.bedtime
                else None
            )
            lead = timedelta(minutes=BEDTIME_LEAD)
            if bedtime and woke >= bedtime.earliest - lead and pattern.is_night(woke):
                sleep_band, kind = back_to_sleep, "back_to_sleep"  # woke after going down
            elif bedtime and (nap is None or nap.likely >= bedtime.earliest - lead):
                sleep_band, kind = bedtime, "bedtime"
            else:
                sleep_band, kind = nap, "nap"

    feed_band: Band | None = None
    if feeds:
        last_feed = feeds[-1]
        if active is not None and kind in ("night", "morning") and sleep_band is not None:
            # At night the baby is fed on waking.
            feed_band = replace(sleep_band, basis=f"on waking ({sleep_band.basis})")
        else:
            recent = _since(feeds, now, FEED_DAYS)
            night = pattern.is_night(last_feed.start)
            intervals = [
                g
                for a, b in zip(recent, recent[1:])
                if pattern.is_night(a.start) == night and 0 < (g := _min(b.start - a.start)) <= MAX_FEED_GAP_MIN
            ]
            if q := quantiles(intervals):
                label = "night" if night else "day"
                feed_band = Band.after(
                    last_feed.start, q, f"{label} feed interval, last {FEED_DAYS} days (n={len(intervals)})"
                )

    return Prediction(asleep=active is not None, sleep=sleep_band, sleep_kind=kind, feed=feed_band, pattern=pattern)


# MARK: forecast

FORECAST_FEED_MIN = 15  # assumed length of a predicted feed
FORECAST_MAX_STEPS = 24


FORECAST_HOURS = 16
FORECAST_PAST_HOURS = 4  # logged history shown before now, for context


def _utc_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _past(records: list[Record], start: datetime, now: datetime) -> list[dict[str, Any]]:
    """Logged sleeps and feeds overlapping [start, now], clipped to it; `end`
    is None while still going on."""
    out = []
    for typ, kinds, merge in (
        ("sleep", [KIND_SLEEP], SLEEP_MERGE_MIN),
        ("feed", [KIND_FEEDING, KIND_BOTTLE], FEED_MERGE_MIN),
    ):
        for e in episodes(records, kinds, merge):
            if e.start > now or (e.end is not None and e.end < start):
                continue
            out.append(
                {
                    "type": typ,
                    "start": _utc_iso(max(e.start, start)),
                    "end": None if e.end is None else _utc_iso(min(e.end, now)),
                }
            )
    return sorted(out, key=lambda e: e["start"])


def forecast(
    records: list[Record],
    now: datetime,
    tz: tzinfo,
    hours: int = FORECAST_HOURS,
    past_hours: int = FORECAST_PAST_HOURS,
) -> dict[str, Any]:
    """The next `hours` as a chain of predictions: assume each predicted event
    (fall asleep, wake up, feed) happens at its likely time, then predict the
    next one from there. Uncertainty adds up along the chain (root sum of
    squares of the earlier steps' spread), so later events get wider windows.

    The night counts as sleep apart from feeds: night wakings are still
    simulated (night feeds are timed off them) but not returned; bedtime and
    the morning wake-up are.

    The range starts `past_hours` before now, with what was logged in that
    time under `past`.
    """
    start = now - timedelta(hours=past_hours)
    end = now + timedelta(hours=hours)
    horizon = now - timedelta(days=SLEEP_DAYS + 2)
    recs = [r for r in records if r.started_at >= horizon or r.ended_at is None]
    past = _past(recs, start, now)
    first = predict(recs, now, tz)
    events: list[dict[str, Any]] = []
    var_lo = var_hi = 0.0
    t = now
    p = first
    for step in range(1, FORECAST_MAX_STEPS + 1):
        if step > 1:
            p = predict(recs, t, tz)
        candidates: list[tuple[str, str | None, Band]] = []
        if p.sleep:
            candidates.append(("wake" if p.asleep else "sleep", p.sleep_kind, p.sleep))
        if p.feed:
            candidates.append(("feed", None, p.feed))
        if not candidates:
            break
        # Overdue events are "any moment now"; on a tie, waking comes first
        # (a night feed follows the wake-up).
        typ, kind, band = min(candidates, key=lambda c: (max(c[2].likely, t), c[0] != "wake"))
        at = max(band.likely, t)
        if at > end:
            break
        lo = (var_lo + _min(band.likely - band.earliest) ** 2) ** 0.5
        hi = (var_hi + _min(band.latest - band.likely) ** 2) ** 0.5
        var_lo, var_hi = lo**2, hi**2
        # Can't happen before now or before the event it follows.
        earliest = max(at - timedelta(minutes=lo), t)
        events.append(
            {
                "type": typ,
                "kind": kind,
                "likely": _utc_iso(at),
                "earliest": _utc_iso(earliest),
                "latest": _utc_iso(at + timedelta(minutes=hi)),
                "basis": band.basis if step == 1 else f"{band.basis} (after {step - 1} earlier predicted events)",
            }
        )
        synthetic = dict(baby_id="", modified_at=at)
        if typ == "wake":
            recs = [
                replace(r, ended_at=at) if r.kind == KIND_SLEEP and r.ended_at is None else r
                for r in recs
            ]
        elif typ == "sleep":
            recs.append(Record(id=f"forecast-{step}", kind=KIND_SLEEP, started_at=at, **synthetic))
        else:
            recs.append(
                Record(
                    id=f"forecast-{step}",
                    kind=KIND_BOTTLE,
                    started_at=at,
                    ended_at=at + timedelta(minutes=FORECAST_FEED_MIN),
                    **synthetic,
                )
            )
        t = at + timedelta(minutes=1)
    events = [
        e
        for e in events
        if not (e["type"] == "wake" and e["kind"] == "night")
        and not (e["type"] == "sleep" and e["kind"] == "back_to_sleep")
    ]
    return {
        "start": _utc_iso(start),
        "now": _utc_iso(now),
        "end": _utc_iso(end),
        "past": past,
        "asleep": first.asleep,
        "bedtime": first.as_dict()["bedtime"],
        "morning_wake": first.as_dict()["morning_wake"],
        "events": events,
    }


# MARK: backtest


def as_of(records: list[Record], t: datetime) -> list[Record]:
    """The records as they looked at `t`: later ones dropped, sessions that
    were still running at `t` made active again."""
    out = []
    for r in records:
        if r.started_at > t:
            continue
        out.append(replace(r, ended_at=None) if r.ended_at and r.ended_at > t else r)
    return out


def _next_sleep(p: Prediction) -> Band | None:
    return None if p.asleep else p.sleep


def _next_feed(p: Prediction) -> Band | None:
    return p.feed


def backtest(records: list[Record], now: datetime, tz: tzinfo, days: int = 7) -> dict[str, Any]:
    """Replay the predictor at every decision point in the last `days`:
    right after each wake-up (next sleep) and right after each feed starts
    (next feed), using only what was known then. Reports how often the real
    time landed inside the window and the typical error of the likely time."""
    start = now - timedelta(days=days)
    history = as_of(
        [r for r in records if r.started_at >= start - timedelta(days=SLEEP_DAYS + 2)], now
    )
    sleeps = episodes(history, [KIND_SLEEP], SLEEP_MERGE_MIN)
    feeds = episodes(history, [KIND_FEEDING, KIND_BOTTLE], FEED_MERGE_MIN)

    def summarize(results: list[tuple[bool, float, float]]) -> dict[str, Any]:
        if not results:
            return {"n": 0, "hit_rate": None, "median_error_min": None, "median_width_min": None}
        errors = sorted(e for _, e, _ in results)
        widths = sorted(w for _, _, w in results)
        return {
            "n": len(results),
            "hit_rate": round(sum(h for h, _, _ in results) / len(results), 2),
            "median_error_min": round(errors[len(errors) // 2]),
            "median_width_min": round(widths[len(widths) // 2]),
        }

    def score(pairs: list[tuple[datetime, datetime]], pick) -> dict[str, Any]:
        by_kind: dict[str, list[tuple[bool, float]]] = {}
        for at, actual in pairs:
            prediction = predict(as_of(history, at), at, tz)
            band = pick(prediction)
            if band is None:
                continue
            kind = prediction.sleep_kind if pick is _next_sleep else "feed"
            by_kind.setdefault(kind or "?", []).append(
                (band.contains(actual), abs(_min(actual - band.likely)), _min(band.latest - band.earliest))
            )
        out = summarize([x for xs in by_kind.values() for x in xs])
        if len(by_kind) > 1:
            out["by_kind"] = {k: summarize(v) for k, v in by_kind.items()}
        return out

    sleep_pairs = [
        (a.end + timedelta(seconds=1), b.start)
        for a, b in zip(sleeps, sleeps[1:])
        if a.end is not None and a.end >= start and _min(b.start - a.end) <= MAX_DAY_WAKE_MIN
    ]
    feed_pairs = [
        (a.start + timedelta(seconds=1), b.start)
        for a, b in zip(feeds, feeds[1:])
        if a.start >= start and _min(b.start - a.start) <= MAX_FEED_GAP_MIN
    ]
    return {
        "days": days,
        "sleep": score(sleep_pairs, _next_sleep),
        "feed": score(feed_pairs, _next_feed),
    }
