"""`binary_sensor.<baby>_sleeping`: asleep right now.

`since` is when the current sleep started (on) or when the last one ended
(off), so the same entity serves sleep automations and wake windows.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BabyLogConfigEntry
from .const import BINARY_SENSOR_KEYS, KIND_SLEEP
from .entity import BabyLogEntity, async_add_per_baby
from .totals import today_totals

SLEEPING = BinarySensorEntityDescription(key="sleeping", name="Sleeping", icon="mdi:sleep")
assert {SLEEPING.key} == BINARY_SENSOR_KEYS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BabyLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_per_baby(
        entry, async_add_entities, lambda baby_id: [SleepingSensor(entry, baby_id, SLEEPING)]
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
