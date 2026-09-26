"""Phone pairing: a webhook that only speaks babylog sync.

The app talks to `POST <webhook url>` with `{"action": "upsert" | "delete" |
"list", ...}` (same fields as the actions) instead of using a long-lived
access token, so a leaked pairing link exposes the baby log, not the house.
With Nabu Casa the link is a cloudhook, reachable from anywhere without
opening HA to the internet.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from aiohttp import web
import voluptuous as vol

from homeassistant.components import cloud, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.network import NoURLAvailableError

from .const import DOMAIN
from .services import DELETE_SCHEMA, LIST_SCHEMA, UPSERT_SCHEMA, get_store

_LOGGER = logging.getLogger(__name__)

CONF_WEBHOOK_ID = "webhook_id"
CONF_CLOUDHOOK_URL = "cloudhook_url"
APP_SCHEME = "junior"


async def async_setup_pairing(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if CONF_WEBHOOK_ID not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()}
        )
    webhook_id = entry.data[CONF_WEBHOOK_ID]
    webhook.async_register(
        hass,
        DOMAIN,
        "Junior Baby Log",
        webhook_id,
        _handle,
        local_only=False,
        allowed_methods=["POST"],
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))


def _cloud_available(hass: HomeAssistant) -> bool:
    return "cloud" in hass.config.components and cloud.async_active_subscription(hass)


async def async_pairing_url(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """The URL the phone should use: a cloudhook with Nabu Casa, otherwise
    HA's external (or internal) URL. Raises HomeAssistantError if HA doesn't
    know any URL for itself."""
    if _cloud_available(hass):
        if CONF_CLOUDHOOK_URL not in entry.data:
            url = await cloud.async_create_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_CLOUDHOOK_URL: url}
            )
        return entry.data[CONF_CLOUDHOOK_URL]
    try:
        return webhook.async_generate_url(hass, entry.data[CONF_WEBHOOK_ID])
    except NoURLAvailableError as err:
        raise HomeAssistantError(
            "Home Assistant doesn't know its own URL. Set one under Settings → System → Network."
        ) from err


def app_link(url: str) -> str:
    """What the QR code encodes: opens Junior (via the iPhone camera) and pairs."""
    return f"{APP_SCHEME}://pair?url={quote(url, safe='')}"


async def async_reset_pairing(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """New secret: every paired phone stops syncing until paired again."""
    if CONF_CLOUDHOOK_URL in entry.data and "cloud" in hass.config.components:
        try:
            await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Couldn't delete the old cloudhook: %s", err)
    data = {
        k: v for k, v in entry.data.items() if k not in (CONF_WEBHOOK_ID, CONF_CLOUDHOOK_URL)
    }
    data[CONF_WEBHOOK_ID] = webhook.async_generate_id()
    hass.config_entries.async_update_entry(entry, data=data)
    hass.config_entries.async_schedule_reload(entry.entry_id)


ACTIONS = {
    "upsert": (UPSERT_SCHEMA, lambda store, d: store.async_upsert(d["records"], d["babies"])),
    "delete": (DELETE_SCHEMA, lambda store, d: store.async_delete(d["ids"], d.get("deleted_at"))),
    "list": (
        LIST_SCHEMA,
        lambda store, d: store.async_list(
            d.get("since"), d["summary"], d.get("baby_id"), d["include_history"]
        ),
    ),
}


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
    try:
        body: Any = await request.json()
    except ValueError:
        return _error("Body must be JSON")
    if not isinstance(body, dict) or body.get("action") not in ACTIONS:
        return _error(f"action must be one of {', '.join(ACTIONS)}")
    schema, run = ACTIONS[body.pop("action")]
    try:
        data = schema(body)
        store = get_store(hass)
    except vol.Invalid as err:
        return _error(str(err))
    except ServiceValidationError as err:
        return _error(str(err), 503)
    return web.json_response(run(store, data))
