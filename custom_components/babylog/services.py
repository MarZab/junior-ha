"""Actions called by the iOS app over REST with a long-lived access token, e.g.
`POST /api/services/babylog/upsert?return_response`.

- `upsert` / `delete` / `list`: per-record sync keyed by the app's UUIDs.
- `export` / `import`: whole-baby transfer in the app's backup file format.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .backup import export_backup, parse_backup
from .const import (
    DOMAIN,
    KINDS,
    SERVICE_DELETE,
    SERVICE_EXPORT,
    SERVICE_IMPORT,
    SERVICE_LIST,
    SERVICE_STATS,
    SERVICE_UPSERT,
)
from .predict import backtest, forecast
from .stats import compute_stats
from .store import BabyLogStore

_ID = vol.All(cv.string, vol.Length(min=1, max=64))

BABY_SCHEMA = vol.Schema(
    {vol.Required("id"): _ID, vol.Optional("name"): cv.string},
    extra=vol.REMOVE_EXTRA,
)

RECORD_SCHEMA = vol.Schema(
    {
        vol.Required("id"): _ID,
        vol.Required("baby_id"): _ID,
        vol.Required("kind"): vol.In(KINDS),
        vol.Required("started_at"): cv.datetime,
        vol.Optional("ended_at"): vol.Any(None, cv.datetime),
        vol.Optional("wet", default=False): cv.boolean,
        vol.Optional("pooped", default=False): cv.boolean,
        vol.Optional("left_breast", default=False): cv.boolean,
        vol.Optional("right_breast", default=False): cv.boolean,
        vol.Optional("amount_ml"): vol.Any(
            None, vol.All(vol.Coerce(int), vol.Range(min=0))
        ),
        vol.Optional("amount_confirmed", default=False): cv.boolean,
        vol.Optional("comment"): vol.Any(None, cv.string),
        vol.Required("modified_at"): cv.datetime,
        vol.Optional("deleted_at"): vol.Any(None, cv.datetime),
    },
    extra=vol.REMOVE_EXTRA,
)

UPSERT_SCHEMA = vol.Schema(
    {
        vol.Optional("records", default=list): vol.All(cv.ensure_list, [RECORD_SCHEMA]),
        vol.Optional("babies", default=list): vol.All(cv.ensure_list, [BABY_SCHEMA]),
    }
)
DELETE_SCHEMA = vol.Schema(
    {
        vol.Required("ids"): vol.All(cv.ensure_list, [_ID]),
        vol.Optional("deleted_at"): cv.datetime,
    }
)
LIST_SCHEMA = vol.Schema(
    {
        vol.Optional("since"): cv.datetime,
        vol.Optional("summary", default=False): cv.boolean,
        vol.Optional("baby_id"): _ID,
        vol.Optional("include_history", default=False): cv.boolean,
    }
)
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("baby_id"): _ID,
        vol.Optional("include_deleted", default=False): cv.boolean,
    }
)
STATS_SCHEMA = vol.Schema(
    {
        vol.Optional("baby_id"): _ID,
        vol.Optional("start"): cv.date,
        vol.Optional("days", default=7): vol.All(vol.Coerce(int), vol.Range(min=1, max=92)),
    }
)
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("backup"): dict,
        vol.Optional("baby_id"): _ID,
        vol.Optional("mode", default="merge"): vol.In(["merge", "replace"]),
    }
)


def get_store(hass: HomeAssistant) -> BabyLogStore:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError("Junior Baby Log is not set up")


def resolve_baby(
    store: BabyLogStore, baby_id: str | None, backup: dict[str, Any] | None = None
) -> str:
    """Explicit id, else the backup's `baby.id`, else the only known baby."""
    if baby_id:
        return baby_id
    if backup and backup.get("baby"):
        return backup["baby"]["id"]
    if len(store.babies) == 1:
        return next(iter(store.babies))
    raise ServiceValidationError(
        "baby_id is required"
        + (f" (known: {', '.join(store.babies)})" if store.babies else "")
    )


def export_for(
    store: BabyLogStore, baby_id: str | None, include_deleted: bool = False
) -> dict[str, Any]:
    baby_id = resolve_baby(store, baby_id)
    if baby_id not in store.babies:
        raise ServiceValidationError(f"Unknown baby_id: {baby_id}")
    return export_backup(store, baby_id, include_deleted)


def import_into(
    store: BabyLogStore, data: Any, baby_id: str | None, mode: str
) -> dict[str, Any]:
    try:
        backup, records = parse_backup(data)
    except vol.Invalid as err:
        raise ServiceValidationError(f"Not a valid Junior backup: {err}") from err
    baby_id = resolve_baby(store, baby_id, backup)
    if (baby := backup.get("baby")) and baby.get("name"):
        store.async_set_baby(baby_id, baby["name"])
    result = store.async_import(baby_id, records, replace_all=mode == "replace")
    return {"baby_id": baby_id, **result}


@callback
def async_register_services(hass: HomeAssistant) -> None:
    async def upsert(call: ServiceCall) -> ServiceResponse:
        return get_store(hass).async_upsert(call.data["records"], call.data["babies"])

    async def delete(call: ServiceCall) -> ServiceResponse:
        return get_store(hass).async_delete(call.data["ids"], call.data.get("deleted_at"))

    async def list_records(call: ServiceCall) -> ServiceResponse:
        return get_store(hass).async_list(
            call.data.get("since"),
            call.data["summary"],
            call.data.get("baby_id"),
            call.data["include_history"],
        )

    async def export(call: ServiceCall) -> ServiceResponse:
        return export_for(
            get_store(hass), call.data.get("baby_id"), call.data["include_deleted"]
        )

    async def import_backup(call: ServiceCall) -> ServiceResponse:
        return import_into(
            get_store(hass),
            call.data["backup"],
            call.data.get("baby_id"),
            call.data["mode"],
        )

    async def stats(call: ServiceCall) -> ServiceResponse:
        store = get_store(hass)
        baby_id = call.data.get("baby_id") or next(iter(store.babies), None)
        if baby_id is None:
            raise ServiceValidationError("No babies yet: sync from the app first")
        days = call.data["days"]
        start = call.data.get("start") or dt_util.now().date() - timedelta(days=days - 1)
        now = dt_util.utcnow()
        result = compute_stats(store, baby_id, start, days, now)
        result["prediction"] = store.prediction(baby_id).as_dict()
        result["forecast"] = await hass.async_add_executor_job(
            forecast, store.for_baby(baby_id), now, dt_util.get_default_time_zone()
        )
        result["accuracy"] = await hass.async_add_executor_job(
            backtest, store.for_baby(baby_id), now, dt_util.get_default_time_zone()
        )
        return result

    register = hass.services.async_register
    register(DOMAIN, SERVICE_UPSERT, upsert, UPSERT_SCHEMA, SupportsResponse.OPTIONAL)
    register(DOMAIN, SERVICE_DELETE, delete, DELETE_SCHEMA, SupportsResponse.OPTIONAL)
    register(DOMAIN, SERVICE_LIST, list_records, LIST_SCHEMA, SupportsResponse.ONLY)
    register(DOMAIN, SERVICE_EXPORT, export, EXPORT_SCHEMA, SupportsResponse.ONLY)
    register(
        DOMAIN, SERVICE_IMPORT, import_backup, IMPORT_SCHEMA, SupportsResponse.OPTIONAL
    )
    register(DOMAIN, SERVICE_STATS, stats, STATS_SCHEMA, SupportsResponse.ONLY)
