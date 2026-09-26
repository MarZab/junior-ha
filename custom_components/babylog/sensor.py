"""Sensors per baby (plus `binary_sensor.<baby>_sleeping`). The state is what
an automation triggers on; everything else is an attribute.

Same shape throughout: the state says what's happening (or the last event),
`since` says from when.

- feeding        none / bottle / left / right; `since` = this feed's start, or
                 the last feed's end ("3 h since feed")
- diaper         last change: wet / poopy / both; `since` = when (reminders)

Today's milk (and its % of a typical day last week) is an attribute of
feeding; today's sleep one of sleeping (see totals.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BabyLogConfigEntry
from .const import KIND_BOTTLE, KIND_DIAPER, KIND_FEEDING, SENSOR_KEYS
from .entity import BabyLogEntity, async_add_per_baby
from .store import BabyLogStore, Record
from .totals import today_totals

FEED_KINDS = (KIND_FEEDING, KIND_BOTTLE)

FEEDING_NONE = "none"
# `breast` covers a breastfeed logged without a single side (both / neither).
FEEDING_STATES = [FEEDING_NONE, "bottle", "left", "right", "breast"]
DIAPER_STATES = ["wet", "poopy", "both"]


@dataclass(frozen=True)
class Ctx:
    store: BabyLogStore
    baby_id: str

    def latest(self, *kinds: str, where: Callable[[Record], bool] | None = None):
        return self.store.latest(self.baby_id, kinds, where)



@dataclass(frozen=True, kw_only=True)
class BabyLogSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Ctx], Any]
    attrs_fn: Callable[[Ctx], dict[str, Any] | None] = lambda _: None


def _minutes(delta: timedelta | None) -> int | None:
    return None if delta is None else int(delta.total_seconds() // 60)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _active_feed(c: Ctx) -> Record | None:
    active = c.store.active_session(c.baby_id)
    return active if active and active.kind in FEED_KINDS else None


def _feed_type(record: Record) -> str:
    if record.kind == KIND_BOTTLE:
        return "bottle"
    return record.side if record.side in ("left", "right") else "breast"


def _feeding(c: Ctx) -> str:
    active = _active_feed(c)
    return _feed_type(active) if active else FEEDING_NONE


def _feeding_attrs(c: Ctx) -> dict[str, Any]:
    active = _active_feed(c)
    last = c.latest(*FEED_KINDS, where=lambda r: r.ended_at is not None)
    since = active.started_at if active else (last.ended_at if last else None)
    return {
        "since": _iso(since),
        "last_feed": _feed_type(last) if last else None,
        "last_feed_started_at": _iso(last.started_at) if last else None,
        "last_feed_minutes": _minutes(last.duration) if last else None,
        "last_amount_ml": last.amount_ml if last else None,
        # Sync health at a glance: when the app last pushed anything.
        "last_sync": _iso(c.store.last_sync),
        **today_totals(c.store, c.baby_id)["feeding"],
        **c.store.prediction(c.baby_id).feed_attrs(),
    }


def _diaper_type(record: Record) -> str:
    if record.wet and record.pooped:
        return "both"
    return "poopy" if record.pooped else "wet"


def _diaper_attrs(c: Ctx) -> dict[str, Any]:
    last = c.latest(KIND_DIAPER)
    poop = c.latest(KIND_DIAPER, where=lambda r: r.pooped)
    return {
        "since": _iso(last.started_at) if last else None,
        "last_poop": _iso(poop.started_at) if poop else None,
    }


DESCRIPTIONS: tuple[BabyLogSensorDescription, ...] = (
    BabyLogSensorDescription(
        key="feeding",
        name="Feeding",
        icon="mdi:baby-bottle-outline",
        device_class=SensorDeviceClass.ENUM,
        options=FEEDING_STATES,
        value_fn=_feeding,
        attrs_fn=_feeding_attrs,
    ),
    BabyLogSensorDescription(
        key="diaper",
        name="Diaper",
        icon="mdi:water",
        device_class=SensorDeviceClass.ENUM,
        options=DIAPER_STATES,
        value_fn=lambda c: _diaper_type(d) if (d := c.latest(KIND_DIAPER)) else None,
        attrs_fn=_diaper_attrs,
    ),
)

assert {d.key for d in DESCRIPTIONS} == SENSOR_KEYS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BabyLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_per_baby(
        entry,
        async_add_entities,
        lambda baby_id: (BabyLogSensor(entry, baby_id, d) for d in DESCRIPTIONS),
    )


class BabyLogSensor(BabyLogEntity, SensorEntity):
    entity_description: BabyLogSensorDescription

    def _recompute(self) -> Any:
        ctx = Ctx(self._store, self._baby_id)
        self._attr_native_value = self.entity_description.value_fn(ctx)
        self._attr_extra_state_attributes = self.entity_description.attrs_fn(ctx)
        return (self._attr_native_value, self._attr_extra_state_attributes)
