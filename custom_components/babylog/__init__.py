"""Junior baby log: records synced from the iOS app, plus latest-state sensors."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import BINARY_SENSOR_KEYS, DOMAIN, PANEL_URL_PATH, SENSOR_KEYS, STATIC_URL
from .entity import device_identifier
from .pairing import async_setup_pairing
from .services import async_register_services
from .store import BabyLogStore
from .views import BabyLogBackupView

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type BabyLogConfigEntry = ConfigEntry[BabyLogStore]


FRONTEND_DIR = Path(__file__).parent / "frontend"


def _versioned(name: str) -> str:
    """URL with a content hash so browsers pick up new builds."""
    digest = sha1((FRONTEND_DIR / name).read_bytes()).hexdigest()[:8]
    return f"{STATIC_URL}/{name}?v={digest}"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    hass.http.register_view(BabyLogBackupView())
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
    )
    # Makes `custom:junior-card` available on every dashboard without adding a
    # Lovelace resource by hand.
    card_url = await hass.async_add_executor_job(_versioned, "junior-card.js")
    frontend.add_extra_js_url(hass, card_url)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BabyLogConfigEntry) -> bool:
    store = BabyLogStore(hass, entry.entry_id)
    await store.async_load()
    entry.runtime_data = store

    # Sensors recompute on record changes; only the "today" totals depend on
    # the clock, and just at local midnight.
    entry.async_on_unload(
        async_track_time_change(hass, store.async_notify, hour=0, minute=0, second=0)
    )

    @callback
    def rename_device(baby_id: str) -> None:
        registry = dr.async_get(hass)
        if device := registry.async_get_device_by_identifier(
            device_identifier(entry, baby_id), entry.entry_id
        ):
            registry.async_update_device(device.id, name=store.babies[baby_id])

    entry.async_on_unload(store.async_add_baby_listener(rename_device))

    _remove_retired_entities(hass, entry)
    await async_setup_pairing(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    panel_url = await hass.async_add_executor_job(_versioned, "junior-panel.js")
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="junior-panel",
        module_url=panel_url,
        sidebar_title="Junior",
        sidebar_icon="mdi:baby-face-outline",
        require_admin=False,
    )
    return True


def _remove_retired_entities(hass: HomeAssistant, entry: BabyLogConfigEntry) -> None:
    """Drop entities from earlier versions so they don't linger as unavailable."""
    keys = {"sensor": SENSOR_KEYS, "binary_sensor": BINARY_SENSOR_KEYS}
    wanted = {
        (domain, f"{entry.entry_id}_{baby_id}_{key}")
        for domain, domain_keys in keys.items()
        for key in domain_keys
        for baby_id in entry.runtime_data.babies
    }
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (reg.domain, reg.unique_id) not in wanted:
            registry.async_remove(reg.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: BabyLogConfigEntry) -> bool:
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BabyLogConfigEntry) -> None:
    await BabyLogStore(hass, entry.entry_id).async_remove()
