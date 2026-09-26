"""Binary sensors per baby.

- `sleeping`: asleep right now. `since` is when the current sleep started (on)
  or when the last one ended (off), so the same entity serves sleep
  automations and wake windows.
- `feed_window`: now is inside the predicted next-feed window (see
  predict.py). Flips on its own at the window's edges.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import BabyLogConfigEntry
from .const import BINARY_SENSOR_KEYS, KIND_SLEEP
from .entity import BabyLogEntity, async_add_per_baby
from .totals import today_totals

SLEEPING = BinarySensorEntityDescription(key="sleeping", name="Sleeping", icon="mdi:sleep")
FEED_WINDOW = BinarySensorEntityDescription(
    key="feed_window", name="Feed window", icon="mdi:baby-bottle-outline"
)
assert {SLEEPING.key, FEED_WINDOW.key} == BINARY_SENSOR_KEYS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BabyLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_per_baby(
        entry,
        async_add_entities,
        lambda baby_id: [
            SleepingSensor(entry, baby_id, SLEEPING),
            FeedWindowSensor(entry, baby_id, FEED_WINDOW),
        ],
    )


class SleepingSensor(BabyLogEntity, BinarySensorEntity):
    def _recompute(self) -> Any:
        active = self._store.active_session(self._baby_id)
        asleep = active if active and active.kind == KIND_SLEEP else None
        last = self._store.latest(
            self._baby_id, [KIND_SLEEP], lambda r: r.ended_at is not None
        )
        since = asleep.started_at if asleep else (last.ended_at if last else None)
        self._attr_is_on = asleep is not None
        self._attr_extra_state_attributes = {
            "since": since.isoformat() if since else None,
            "last_sleep_minutes": int(last.duration.total_seconds() // 60)
            if last and last.duration
            else None,
            **today_totals(self._store, self._baby_id)["sleeping"],
            **self._store.prediction(self._baby_id).sleep_attrs(),
        }
        return (self._attr_is_on, self._attr_extra_state_attributes)


class FeedWindowSensor(BabyLogEntity, BinarySensorEntity):
    """On between `next_feed_earliest` and `next_feed_latest`. The window only
    moves when records change; a timer at its next edge covers the clock."""

    _boundary: datetime | None = None
    _unsub_timer: CALLBACK_TYPE | None = None

    def _recompute(self) -> Any:
        band = self._store.prediction(self._baby_id).feed
        now = dt_util.utcnow()
        self._attr_is_on = band is not None and band.contains(now)
        self._attr_extra_state_attributes = band.as_attrs("next_feed") if band else {}
        self._boundary = (
            next((t for t in (band.earliest, band.latest) if t > now), None) if band else None
        )
        return (self._attr_is_on, self._attr_extra_state_attributes)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._schedule()
        self.async_on_remove(self._cancel_timer)

    @callback
    def _store_changed(self) -> None:
        super()._store_changed()
        self._schedule()

    @callback
    def _schedule(self) -> None:
        self._cancel_timer()
        if self._boundary is not None:
            # Just past the edge: the window includes both ends.
            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, self._tick, self._boundary + timedelta(seconds=1)
            )

    @callback
    def _tick(self, _now: datetime) -> None:
        self._unsub_timer = None
        self._store_changed()

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
