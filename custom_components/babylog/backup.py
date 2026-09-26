"""Conversion to/from the iOS app's backup file (`BackupFile` in LogEntry.swift).

The app decodes with `JSONDecoder.dateDecodingStrategy = .iso8601`, which
rejects fractional seconds, so dates are written as `YYYY-MM-DDTHH:MM:SSZ`.
Keys the app doesn't know yet (`babyId`, `modifiedAt`, `syncedAt`,
`deletedAt`, `baby`)
are ignored by older app versions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import KINDS
from .store import BabyLogStore, as_utc

BACKUP_VERSION = 1


def _ios_date(value: datetime) -> str:
    return dt_util.as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional(validator: Any) -> Any:
    return vol.Any(None, validator)


BACKUP_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Optional("id"): _optional(cv.string),
        vol.Required("startedAt"): cv.datetime,
        vol.Optional("endedAt"): _optional(cv.datetime),
        vol.Required("kindRaw"): vol.In(KINDS),
        vol.Optional("wet", default=False): cv.boolean,
        vol.Optional("pooped", default=False): cv.boolean,
        vol.Optional("leftBoob", default=False): cv.boolean,
        vol.Optional("rightBoob", default=False): cv.boolean,
        vol.Optional("amountMl"): _optional(vol.All(vol.Coerce(int), vol.Range(min=0))),
        vol.Optional("amountConfirmed", default=False): cv.boolean,
        vol.Optional("comment"): _optional(cv.string),
        vol.Optional("modifiedAt"): _optional(cv.datetime),
        vol.Optional("deletedAt"): _optional(cv.datetime),
    },
    extra=vol.ALLOW_EXTRA,
)

BACKUP_FILE_SCHEMA = vol.Schema(
    {
        vol.Required("version"): vol.Coerce(int),
        vol.Optional("exportedAt"): _optional(cv.datetime),
        vol.Optional("baby"): _optional(
            vol.Schema(
                {vol.Required("id"): cv.string, vol.Optional("name"): cv.string},
                extra=vol.ALLOW_EXTRA,
            )
        ),
        vol.Required("entries"): [BACKUP_ENTRY_SCHEMA],
    },
    extra=vol.ALLOW_EXTRA,
)


def export_backup(
    store: BabyLogStore, baby_id: str, include_deleted: bool = False
) -> dict[str, Any]:
    """One baby's current records as an app-importable backup file.

    Soft-deleted entries are left out unless `include_deleted` is set; an app
    that doesn't know `deletedAt` would import them as live, so only use that
    for archiving or for an app version that understands it. `syncedAt` equals
    `modifiedAt` because these entries are exactly what HA holds, so importing
    this file on the phone doesn't trigger a re-upload."""
    records = sorted(
        store.for_baby(baby_id, include_deleted=include_deleted),
        key=lambda r: r.started_at,
    )
    entries = []
    for r in records:
        modified = _ios_date(r.modified_at)
        entries.append(
            {
                "id": r.id,
                "babyId": r.baby_id,
                "startedAt": _ios_date(r.started_at),
                "endedAt": _ios_date(r.ended_at) if r.ended_at else None,
                "kindRaw": r.kind,
                "wet": r.wet,
                "pooped": r.pooped,
                "leftBoob": r.left_breast,
                "rightBoob": r.right_breast,
                "amountMl": r.amount_ml,
                "amountConfirmed": r.amount_confirmed,
                "comment": r.comment,
                "modifiedAt": modified,
                "syncedAt": modified,
                **({"deletedAt": _ios_date(r.deleted_at)} if r.deleted_at else {}),
            }
        )
    return {
        "version": BACKUP_VERSION,
        "exportedAt": _ios_date(dt_util.utcnow()),
        "baby": {"id": baby_id, "name": store.babies.get(baby_id)},
        "entries": entries,
    }


def parse_backup(data: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate an app backup file and convert its entries to record dicts
    (without `baby_id`). Returns (validated file, records).

    Entries without `modifiedAt` (backups from before sync existed) take the
    file's `exportedAt`, i.e. "this is how it looked at export time"."""
    backup = BACKUP_FILE_SCHEMA(data)
    fallback = as_utc(backup.get("exportedAt") or dt_util.utcnow())
    records = [
        {
            # Swift's `UUID().uuidString` is upper-case; match it for new ids.
            "id": entry.get("id") or str(uuid.uuid4()).upper(),
            "kind": entry["kindRaw"],
            "started_at": entry["startedAt"],
            "ended_at": entry.get("endedAt"),
            "wet": entry["wet"],
            "pooped": entry["pooped"],
            "left_breast": entry["leftBoob"],
            "right_breast": entry["rightBoob"],
            "amount_ml": entry.get("amountMl"),
            "amount_confirmed": entry["amountConfirmed"],
            "comment": entry.get("comment"),
            "modified_at": entry.get("modifiedAt") or fallback,
            "deleted_at": entry.get("deletedAt"),
        }
        for entry in backup["entries"]
    ]
    return backup, records
