"""Base entity: one service-type device per baby, pushed by the store."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .store import BabyLogStore


def device_identifier(entry: ConfigEntry, baby_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry.entry_id}_{baby_id}")


@callback
def async_add_per_baby(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    make: Callable[[str], Iterable[Entity]],
) -> None:
    """Add entities for every known baby now, and for new babies as the app
    syncs them in."""
    store: BabyLogStore = entry.runtime_data
    added: set[str] = set()

    @callback
    def add(baby_id: str) -> None:
        if baby_id in added:
            return
        added.add(baby_id)
        async_add_entities(make(baby_id))

    for baby_id in list(store.babies):
        add(baby_id)
    entry.async_on_unload(store.async_add_baby_listener(add))


class BabyLogEntity(Entity):
    """Recomputes from the store when records change and writes state only
    when the result differs."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # History and statistics come from the stored records (Junior panel), so
    # keep the recorder out of it: no attributes recorded, and no sensor has a
    # state_class, so there are no long-term statistics either.
    _unrecorded_attributes = frozenset(
        {
            "since",
            "last_feed",
            "last_feed_started_at",
            "last_feed_minutes",
            "last_amount_ml",
            "last_sync",
            "last_poop",
            "last_sleep_minutes",
            "milk_today_ml",
            "milk_today_pct",
            "feeds_today",
            "sleep_today_min",
            "sleep_today_pct",
            "bedtime",
            "morning_wake",
            *(
                f"{prefix}_{suffix}"
                for prefix in ("next_feed", "next_sleep", "wake")
                for suffix in ("at", "earliest", "latest", "basis", "kind")
            ),
        }
    )

    def __init__(
        self,
        entry: ConfigEntry,
        baby_id: str,
        description: EntityDescription,
    ) -> None:
        self.entity_description = description
        self._store: BabyLogStore = entry.runtime_data
        self._baby_id = baby_id
        self._attr_unique_id = f"{entry.entry_id}_{baby_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier(entry, baby_id)},
            name=self._store.babies.get(baby_id),
            manufacturer="Junior",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._snapshot: Any = self._recompute()

    def _recompute(self) -> Any:
        """Update the `_attr_*` fields from the store and return a comparable
        snapshot of them."""
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._store.async_add_listener(self._store_changed))

    @callback
    def _store_changed(self) -> None:
        snapshot = self._recompute()
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self.async_write_ha_state()
