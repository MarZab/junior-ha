#!/usr/bin/env python3
"""Backtest the predictor on an app backup file, inside the HA container:

    docker cp junior-backup.json babylog-ha:/tmp/backup.json
    docker exec -e TZ=Europe/Paris babylog-ha python3 - < scripts/backtest.py   # your time zone

Replays predict.py at every wake-up and feed across the whole file (and per
month), reporting how often the real time fell inside the predicted window.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/config")
from custom_components.babylog.backup import parse_backup  # noqa: E402
from custom_components.babylog.predict import backtest  # noqa: E402
from custom_components.babylog.store import Record  # noqa: E402

TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/backup.json"
_, raw = parse_backup(json.load(open(path)))
records = [Record.from_dict({**r, "baby_id": "b"}) for r in raw if not r.get("deleted_at")]
end = max(r.started_at for r in records)
start = min(r.started_at for r in records)

print(f"{len(records)} records, {start:%Y-%m-%d} → {end:%Y-%m-%d}")
print(f"{'period':<22}{'sleep n':>8}{'in window':>11}{'median err':>12}{'feed n':>8}{'in window':>11}{'median err':>12}")
t = start + timedelta(days=14)
while t < end:
    stop = min(t + timedelta(days=30), end + timedelta(minutes=1))
    r = backtest(records, stop, TZ, days=(stop - t).days or 1)
    s, f = r["sleep"], r["feed"]
    fmt = lambda x: f"{x['hit_rate']:.0%}" if x["hit_rate"] is not None else "-"
    print(f"{t:%m-%d} → {stop:%m-%d}{'':>8}{s['n']:>8}{fmt(s):>11}{str(s['median_error_min'])+'m':>12}{f['n']:>8}{fmt(f):>11}{str(f['median_error_min'])+'m':>12}")
    t = stop
r = backtest(records, end + timedelta(minutes=1), TZ, days=14)
print(f"last 14 days: sleep {r['sleep']}\n              feed {r['feed']}")
r = backtest(records, end + timedelta(minutes=1), TZ, days=110)
print("whole period by kind:", json.dumps(r["sleep"].get("by_kind"), indent=1))
