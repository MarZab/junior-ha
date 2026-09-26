# Junior Baby Log for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/marzab/junior-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/marzab/junior-ha/actions/workflows/validate.yml)

Brings the feeds, sleeps and diaper changes logged in the **Junior** iOS baby
log into Home Assistant: live sensors for automations, a sidebar panel with
statistics and a 16-hour prediction, and a dashboard card.

- **Sync from the app** over a pairing link (QR code) that only allows baby-log
  sync — no Home Assistant access token on the phone. Works remotely through
  Home Assistant Cloud (Nabu Casa) without exposing your instance.
- **Sensors** per baby: feeding now (bottle / left / right) and time since the
  last feed, asleep / awake since, last diaper, today's milk and sleep compared
  with a usual day.
- **Predictions** of the next nap, bedtime, wake-up and feed as time windows,
  learned from the last days of logs.
- **Junior panel** in the sidebar: day / week / month statistics, a 24-hour
  rhythm chart per day and a Gantt chart of the last 4 and next 16 hours.
- **Dashboard card** `custom:junior-card`.
- **Backups**: import and export in the app's backup format; nothing is ever
  deleted (soft deletes with full version history).
- Several babies, each with their own device.

All data stays in your Home Assistant (`.storage/babylog.*`).

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/marzab/junior-ha`
   with category **Integration**.
2. Install **Junior Baby Log** and restart Home Assistant.

### Manual

Copy `custom_components/babylog` into your `config/custom_components/` folder
and restart Home Assistant.

Requires Home Assistant 2026.9 or newer.

## Setup

1. **Settings → Devices & services → Add integration → Junior Baby Log.**
2. **Configure → Pair a phone** shows a QR code. Scan it with the iPhone camera
   (it opens Junior and pairs), or copy the link into Junior → Settings → Home
   Assistant.
   - With Home Assistant Cloud the link uses a cloud webhook and works anywhere.
     Otherwise it uses your instance's external (or internal) URL, so set one
     under Settings → System → Network.
   - Treat the link like a password: it can read and write the baby log (and
     nothing else). **Configure → Reset pairing** revokes it for all phones.
3. Set the baby's name in the app before the first sync: entity ids are built
   from it (`sensor.<name>_feeding`, …).

Babies, devices and entities appear as soon as the app syncs.

## Entities

Four per baby. The state says what's happening (or what the last event was)
and `since` says from when; everything else is an attribute.

| Entity | State | Attributes |
|---|---|---|
| `sensor.<baby>_feeding` | none / bottle / left / right (`breast` without a single side) | since, last_feed, last_feed_started_at, last_feed_minutes, last_amount_ml, milk_today_ml, milk_today_pct, feeds_today, next_feed_*, last_sync |
| `binary_sensor.<baby>_sleeping` | on / off | since, last_sleep_minutes, sleep_today_min, sleep_today_pct, next_sleep_* / wake_*, bedtime, morning_wake |
| `sensor.<baby>_diaper` | last change: wet / poopy / both | since, last_poop |
| `binary_sensor.<baby>_feed_window` | on while now is inside the predicted next-feed window | next_feed_* |

- `since`: when feeding / sleep started, or when the last one ended (so "time
  since the last feed" and wake windows come straight from it).
- `*_today_pct`: today so far as a percentage of the average day over the
  previous 7 days (days without entries are skipped). Milk counts bottles.
- `next_*` / `wake_*`: predictions, see below.

Sensors recompute when a record changes (and at local midnight); there is no
polling. `feed_window` also flips on its own at the window's edges.

### Example automation

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.baby_feed_window
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Next feed is due soon"
```

### Recorder

History comes from the integration's own records, so the sensors have no
long-term statistics and their attributes aren't recorded. To keep their
states out of the recorder entirely:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*_feeding
      - binary_sensor.*_sleeping
      - sensor.*_diaper
      - binary_sensor.*_feed_window
```

## Junior panel

A **Junior** entry in the sidebar, with statistics computed from the stored
records, so it reaches back as far as the app's logs do:

- Day / Week / Month with ‹ › and Today (opens on today).
- Tiles for milk, feeds, sleep and diapers. In Day view milk and sleep fill up
  to their percentage of a usual day.
- **Forecast** (today only): a Gantt chart from 4 hours ago to 16 hours ahead.
  The first row is what was logged so far; below it the predicted naps,
  bedtime, wake-up and feeds; bars are the windows, marks the most likely times.
- **Rhythm**: a 24-hour timeline per day with sleeps, feeds, diapers and notes.
- Milk and diaper bars (per hour in Day view, per day otherwise) and sleep per
  day (night vs naps) for longer ranges.

## Dashboard card

```yaml
type: custom:junior-card
baby: baby        # optional: entity id prefix; defaults to the first baby
title: Junior     # optional
```

Feeding now / time since the last feed, asleep or awake since, the last diaper,
today's milk and sleep as bars against a usual day, and the next nap and feed
windows. Tapping it opens the panel. The card is loaded by the integration; no
resource needs to be added.

## Predictions

The next sleep and feed are predicted as a **window** (15th–85th percentile of
recent intervals, anchored on the last event), because a single time is
usually 30–60 minutes off at this age:

- Breast + bottle top-up within 30 minutes is one feed; sleeps split by less
  than 10 minutes are one sleep; daytime wake windows over 6 hours are treated
  as unlogged naps.
- Night hours are learned from the last 7 nights (bedtime and morning wake).
- Awake by day → next nap from recent wake windows, switching to the usual
  bedtime in the evening. Asleep → wake-up from nap length or night stretch,
  capped by the usual morning wake.
- Feeds follow day / night intervals of the last 3 days; at night the next feed
  is expected when the baby wakes.
- The 16-hour forecast chains these: each event is assumed at its most likely
  time and the next one is predicted from there, so windows widen along the
  chain.

On several months of real logs the real time fell inside the window about two
times in three, with windows around 1.5 hours wide. `scripts/backtest.py`
replays the predictor over a backup file.

## Backups

- **Import**: Configure → **Import backup** takes a file saved by the app (or
  exported here). *Merge* adds entries Home Assistant doesn't have; *Replace*
  makes that baby match the file (missing entries are soft-deleted).
- **Export**: the `babylog.export` action (Developer tools → Actions), or
  `GET /api/babylog/backup?baby_id=…` with a Home Assistant token. The file can
  be imported by the app.

Nothing is ever deleted: a delete is a new version with `deleted_at`, and every
replaced or out-of-date version is kept in the record's history. Removing the
integration deletes its storage.

## Actions

| Action | Purpose |
|---|---|
| `babylog.upsert` | Write records (last write wins on `modified_at`) |
| `babylog.delete` | Soft-delete records by id |
| `babylog.list` | Records changed since a time, optionally with history |
| `babylog.export` / `babylog.import` | Backup files in the app's format |
| `babylog.stats` | Per-day totals, timeline, prediction and 16-hour forecast (powers the panel) |

<details>
<summary>Sync protocol (for app developers)</summary>

The pairing webhook accepts `POST {"action": "upsert" | "delete" | "list", ...}`
with the same fields as the actions and answers with the same JSON. Errors are
400 `{"error": …}`; Home Assistant answers an empty 200 for unknown webhook ids
(i.e. after a pairing reset).

Record:

```json
{
  "id": "6F1C…",
  "baby_id": "A1B2…",
  "kind": "feeding|sleep|bottle|diaper|comment",
  "started_at": "2026-09-26T03:10:00Z",
  "ended_at": "2026-09-26T03:35:00Z",
  "wet": false, "pooped": false,
  "left_breast": true, "right_breast": false,
  "amount_ml": null, "amount_confirmed": false,
  "comment": null,
  "modified_at": "2026-09-26T03:35:01Z",
  "deleted_at": null
}
```

- `upsert` → `{"accepted": [...], "stale": [...]}`. Re-sending the current
  version is accepted; an older one is `stale` (kept in history, not applied).
  Both count as synced. `babies: [{"id", "name"}]` registers or renames babies.
- `delete` → `{"deleted", "already_deleted", "missing", "stale"}`; never fails
  per id.
- `list` (`since`, `summary`, `baby_id`, `include_history`) → current versions
  including soft-deleted ones; `count` is live records only.
- Backup files: `GET/POST /api/babylog/backup` (token auth, POST needs admin).
  Dates have no fractional seconds; `syncedAt == modifiedAt` so importing on
  the phone doesn't re-upload.

</details>

## Development

```sh
docker compose up -d                 # Home Assistant on http://localhost:8123 (TZ from your shell)
python3 scripts/e2e.py [--restart]   # onboards a throwaway user dev/devdevdev, adds the integration, runs the checks
python3 scripts/seed.py              # a few weeks of demo data for a "Demo" baby
docker compose down -v               # wipe the dev instance
```

The integration folder is mounted read-only; run `docker compose restart`
after changes. CI runs hassfest, the HACS validation and the end-to-end checks.

Releases: bump `version` in `manifest.json`, tag `vX.Y.Z` and publish a GitHub
release; HACS offers the release to users.

## License

MIT — see [LICENSE](LICENSE).
