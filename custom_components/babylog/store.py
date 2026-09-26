"""Persistent record store for the Junior baby log.

Home Assistant is the system of record for synced entries: each record is
keyed by the app's stable UUID and keeps its real `started_at` / `ended_at`,
which is what makes backfilling old entries possible. Every record belongs to
a baby (`baby_id`); each baby gets its own device and sensors.

Nothing is ever removed. A delete is a new version with `deleted_at` set, and
every superseded or out-of-date version is kept in the record's history.
Conflicts resolve last-write-wins on `modified_at`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DEFAULT_BABY_NAME, DOMAIN, SESSION_KINDS, STORAGE_VERSION

SAVE_DELAY = 1


@dataclass(slots=True, frozen=True)
class Record:
    """One version of a log entry, mirroring `LogEntry` in the iOS app."""

    id: str
    baby_id: str
    kind: str
    started_at: datetime
    modified_at: datetime
    ended_at: datetime | None = None
    wet: bool = False
    pooped: bool = False
    left_breast: bool = False
    right_breast: bool = False
    amount_ml: int | None = None
    amount_confirmed: bool = False
    comment: str | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_session(self) -> bool:
        return self.kind in SESSION_KINDS

    @property
    def side(self) -> str | None:
        if self.left_breast and self.right_breast:
            return "both"
        if self.left_breast:
            return "left"
        if self.right_breast:
            return "right"
        return None

    @property
    def duration(self) -> timedelta | None:
        """Length of an ended session; None while active or for point events."""
        if not self.is_session or self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def same_content(self, other: Record) -> bool:
        """Equal apart from `modified_at` (i.e. writing it would change nothing)."""
        return all(
            getattr(self, f.name) == getattr(other, f.name)
            for f in fields(self)
            if f.name != "modified_at"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "baby_id": self.baby_id,
            "kind": self.kind,
            "started_at": self.started_at.isoformat(),
            "ended_at": _iso(self.ended_at),
            "wet": self.wet,
            "pooped": self.pooped,
            "left_breast": self.left_breast,
            "right_breast": self.right_breast,
            "amount_ml": self.amount_ml,
            "amount_confirmed": self.amount_confirmed,
            "comment": self.comment,
            "modified_at": self.modified_at.isoformat(),
            "deleted_at": _iso(self.deleted_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Record:
        """Build from validated service data or from stored JSON."""
        return cls(
            id=data["id"],
            baby_id=data["baby_id"],
            kind=data["kind"],
            started_at=as_utc(data["started_at"]),
            ended_at=as_utc(data["ended_at"]) if data.get("ended_at") else None,
            wet=bool(data.get("wet", False)),
            pooped=bool(data.get("pooped", False)),
            left_breast=bool(data.get("left_breast", False)),
            right_breast=bool(data.get("right_breast", False)),
            amount_ml=data.get("amount_ml"),
            amount_confirmed=bool(data.get("amount_confirmed", False)),
            comment=data.get("comment"),
            modified_at=as_utc(data["modified_at"]),
            deleted_at=as_utc(data["deleted_at"]) if data.get("deleted_at") else None,
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def as_utc(value: datetime | str) -> datetime:
    """Parse/normalize to an aware UTC datetime. Naive values are taken as UTC."""
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            raise ValueError(f"Invalid datetime: {value}")
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.UTC)
    return dt_util.as_utc(value)


class BabyLogStore:
    """In-memory records backed by `.storage/babylog.<entry_id>`."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        # record id -> current version (soft-deleted ones included)
        self.records: dict[str, Record] = {}
        # record id -> older / stale versions, oldest first
        self.history: dict[str, list[Record]] = {}
        # baby id -> display name
        self.babies: dict[str, str] = {}
        self.last_sync: datetime | None = None
        # baby id -> live records newest first; dropped on every change
        self._timelines: dict[str, list[Record]] = {}
        # baby id -> Prediction; dropped on every change and at midnight
        self._predictions: dict[str, Any] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._baby_listeners: list[Callable[[str], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.records = {
            raw["id"]: Record.from_dict(raw) for raw in data.get("records", [])
        }
        self.history = {
            record_id: [Record.from_dict(raw) for raw in versions]
            for record_id, versions in data.get("history", {}).items()
        }
        self.babies = dict(data.get("babies", {}))
        if last_sync := data.get("last_sync"):
            self.last_sync = as_utc(last_sync)

    async def async_flush(self) -> None:
        """Write now. Delayed saves only flush on HA shutdown, so an entry
        reload (e.g. after resetting pairing) would otherwise drop them."""
        await self._store.async_save(self._data_to_save())

    async def async_remove(self) -> None:
        await self._store.async_remove()

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "babies": self.babies,
            "records": [record.as_dict() for record in self.records.values()],
            "history": {
                record_id: [version.as_dict() for version in versions]
                for record_id, versions in self.history.items()
            },
            "last_sync": _iso(self.last_sync),
        }

    @callback
    def _changed(self) -> None:
        self._timelines.clear()
        self._predictions.clear()
        self.last_sync = dt_util.utcnow()
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        self.async_notify()

    # MARK: listeners

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add_baby_listener(
        self, listener: Callable[[str], None]
    ) -> CALLBACK_TYPE:
        """Called with the baby id when a baby is added or renamed."""
        self._baby_listeners.append(listener)
        return lambda: self._baby_listeners.remove(listener)

    @callback
    def async_notify(self, *_: Any) -> None:
        """Tell entities to recompute. Driven by record changes, plus once at
        local midnight so "today" totals reset."""
        self._predictions.clear()
        for listener in list(self._listeners):
            listener()

    # MARK: mutations

    @callback
    def async_set_baby(self, baby_id: str, name: str | None = None) -> bool:
        """Register a baby, or rename one when `name` is given. Returns True
        when something changed."""
        current = self.babies.get(baby_id)
        if current is not None and (name is None or name == current):
            return False
        self.babies[baby_id] = name or current or DEFAULT_BABY_NAME
        for listener in list(self._baby_listeners):
            listener(baby_id)
        return True

    def _archive(self, version: Record) -> None:
        """Keep a version in history, ordered by `modified_at`, skipping exact
        duplicates (retries)."""
        versions = self.history.setdefault(version.id, [])
        if any(v == version for v in versions):
            return
        versions.append(version)
        versions.sort(key=lambda v: v.modified_at)

    def _write(self, record: Record) -> str:
        """Last-write-wins on `modified_at`. Returns "accepted", "unchanged"
        (identical to the current version, e.g. a retry) or "stale" (older
        than the current version; kept in history only)."""
        current = self.records.get(record.id)
        if current is not None:
            if record == current:
                return "unchanged"
            if record.modified_at < current.modified_at:
                self._archive(record)
                return "stale"
            self._archive(current)
        self.async_set_baby(record.baby_id)
        self.records[record.id] = record
        return "accepted"

    @callback
    def async_upsert(
        self,
        incoming: Iterable[dict[str, Any]],
        babies: Iterable[dict[str, Any]] = (),
    ) -> dict[str, list[str]]:
        """Write versions sent by a client. Retries of the current version are
        reported as accepted so the client can mark them synced."""
        changed = False
        for baby in babies:
            changed |= self.async_set_baby(baby["id"], baby.get("name"))

        accepted: list[str] = []
        stale: list[str] = []
        for raw in incoming:
            record = Record.from_dict(raw)
            outcome = self._write(record)
            if outcome == "stale":
                stale.append(record.id)
            else:
                accepted.append(record.id)
            changed |= outcome != "unchanged"
        if changed:
            self._changed()
        return {"accepted": accepted, "stale": stale}

    @callback
    def async_delete(
        self, ids: Iterable[str], deleted_at: datetime | None = None
    ) -> dict[str, list[str]]:
        """Soft delete: a new version with `deleted_at` (and `modified_at`) set
        to `deleted_at`, defaulting to now. Already-deleted and unknown ids are
        reported but not an error, so the client can always mark them synced."""
        when = as_utc(deleted_at) if deleted_at else dt_util.utcnow()
        deleted: list[str] = []
        already: list[str] = []
        missing: list[str] = []
        stale: list[str] = []
        for record_id in ids:
            current = self.records.get(record_id)
            if current is None:
                missing.append(record_id)
            elif current.is_deleted:
                already.append(record_id)
            elif self._write(replace(current, deleted_at=when, modified_at=when)) == "stale":
                stale.append(record_id)
            else:
                deleted.append(record_id)
        if deleted or stale:
            self._changed()
        return {
            "deleted": deleted,
            "already_deleted": already,
            "missing": missing,
            "stale": stale,
        }

    @callback
    def async_import(
        self, baby_id: str, records: list[dict[str, Any]], replace_all: bool
    ) -> dict[str, int]:
        """Bulk import for one baby, same semantics as the iOS backup import.

        merge:   ids HA doesn't know yet are added; everything else is skipped.
        replace: the file is authoritative. Entries whose content differs from
                 HA's current version (including soft-deleted ones) get a new
                 version stamped now; the baby's live records that aren't in
                 the file are soft-deleted. Nothing is removed."""
        now = dt_util.utcnow()
        imported = updated = unchanged = skipped = 0
        for raw in records:
            record = Record.from_dict({**raw, "baby_id": baby_id})
            current = self.records.get(record.id)
            if current is None:
                self._write(record)
                imported += 1
            elif not replace_all:
                skipped += 1
            elif current.same_content(record):
                unchanged += 1
            else:
                self._write(replace(record, modified_at=max(now, current.modified_at)))
                updated += 1

        deleted = 0
        if replace_all:
            in_file = {raw["id"] for raw in records}
            for current in list(self.records.values()):
                if (
                    current.baby_id == baby_id
                    and not current.is_deleted
                    and current.id not in in_file
                ):
                    self._write(replace(current, deleted_at=now, modified_at=now))
                    deleted += 1

        self.async_set_baby(baby_id)
        self._changed()
        return {
            "imported": imported,
            "updated": updated,
            "unchanged": unchanged,
            "skipped": skipped,
            "deleted": deleted,
        }

    # MARK: queries

    def for_baby(
        self, baby_id: str | None, include_deleted: bool = False
    ) -> list[Record]:
        return [
            r
            for r in self.records.values()
            if (baby_id is None or r.baby_id == baby_id)
            and (include_deleted or not r.is_deleted)
        ]

    @callback
    def async_list(
        self,
        since: datetime | None = None,
        summary: bool = False,
        baby_id: str | None = None,
        include_history: bool = False,
    ) -> dict[str, Any]:
        """Current versions (soft-deleted ones included, with `deleted_at`)
        modified after `since`, or all when None. `summary` returns only id,
        modified_at and deleted_at, which is enough for a client to diff
        against its own store."""
        records = sorted(
            self.for_baby(baby_id, include_deleted=True), key=lambda r: r.started_at
        )
        if since is not None:
            since = as_utc(since)
            records = [r for r in records if r.modified_at > since]

        def render(r: Record) -> dict[str, Any]:
            if summary:
                out = {
                    "id": r.id,
                    "modified_at": r.modified_at.isoformat(),
                    "deleted_at": _iso(r.deleted_at),
                }
            else:
                out = r.as_dict()
            if include_history:
                out["history"] = [v.as_dict() for v in self.history.get(r.id, [])]
            return out

        return {
            "babies": [{"id": i, "name": n} for i, n in self.babies.items()],
            "records": [render(r) for r in records],
            "count": len(self.for_baby(baby_id)),
        }

    def prediction(self, baby_id: str) -> Any:
        """Next sleep / feed windows (see predict.py), cached until the next change."""
        if baby_id not in self._predictions:
            from .predict import predict  # noqa: PLC0415 - predict imports Record from here

            self._predictions[baby_id] = predict(
                self.for_baby(baby_id), dt_util.utcnow(), dt_util.get_default_time_zone()
            )
        return self._predictions[baby_id]

    def timeline(self, baby_id: str) -> list[Record]:
        """The baby's live records, newest first. Cached until the next change
        so every sensor can walk back from the latest entry cheaply."""
        if (cached := self._timelines.get(baby_id)) is None:
            cached = sorted(
                self.for_baby(baby_id), key=lambda r: r.started_at, reverse=True
            )
            self._timelines[baby_id] = cached
        return cached

    def latest(
        self,
        baby_id: str,
        kinds: Iterable[str],
        where: Callable[[Record], bool] | None = None,
    ) -> Record | None:
        """Walk back from the newest entry to the first matching one."""
        kinds = set(kinds)
        for r in self.timeline(baby_id):
            if r.kind in kinds and (where is None or where(r)):
                return r
        return None

    def active_session(self, baby_id: str) -> Record | None:
        return self.latest(baby_id, SESSION_KINDS, lambda r: r.ended_at is None)

    def started_since(
        self, baby_id: str, kinds: Iterable[str], start: datetime
    ) -> list[Record]:
        kinds = set(kinds)
        out: list[Record] = []
        for r in self.timeline(baby_id):
            if r.started_at < start:
                break
            if r.kind in kinds:
                out.append(r)
        return out
