#!/usr/bin/env python3
"""Fill the docker HA with a few weeks of realistic-looking data for one baby,
to see the Junior panel and card with something in them.

    python3 scripts/e2e.py --setup-only   # onboard + token
    python3 scripts/seed.py [--days 35]

Uses a separate baby ("Demo") so it doesn't touch e2e data. Deterministic
(fixed random seed); rerunning upserts the same ids.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import random
import sys
import urllib.request
import uuid

BASE = "http://localhost:8123"
ROOT = Path(__file__).resolve().parent.parent
BABY = "DE000000-0000-4000-8000-00000000DE00"


def post(path: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def rid(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "junior-seed/" + "/".join(map(str, parts)))).upper()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def generate(days: int) -> list[dict]:
    rnd = random.Random(7)
    local = datetime.now().astimezone()
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    now = local
    out: list[dict] = []

    def add(kind: str, start: datetime, end: datetime | None = None, **fields) -> None:
        if start > now:
            return
        if end is not None and end > now:
            end = None  # still running
        out.append(
            {
                "id": rid(kind, start.isoformat()),
                "baby_id": BABY,
                "kind": kind,
                "started_at": iso(start),
                "ended_at": iso(end) if end else None,
                "modified_at": iso(end or start),
                **fields,
            }
        )

    for d in range(days, -1, -1):
        day = today - timedelta(days=d)
        age = days - d  # the baby gets a little older through the range
        # Night: bedtime ~19:30 until ~06:45 with 2-3 wake-ups for a feed.
        bed = day - timedelta(days=1) + timedelta(hours=19, minutes=rnd.randint(10, 60))
        wake = day + timedelta(hours=6, minutes=rnd.randint(15, 70))
        cuts = sorted(bed + (wake - bed) * f for f in rnd.sample([0.3, 0.45, 0.6, 0.75], 3 if age < 14 else 2))
        t = bed
        for cut in cuts:
            add("sleep", t, cut)
            ml = rnd.choice([90, 100, 110, 120])
            feed_end = cut + timedelta(minutes=rnd.randint(12, 22))
            add("bottle", cut, feed_end, amount_ml=ml, amount_confirmed=True)
            add("diaper", feed_end, wet=True, pooped=rnd.random() < 0.15)
            t = feed_end + timedelta(minutes=rnd.randint(5, 15))
        add("sleep", t, wake)

        # Day: feed every ~3h, naps in between, diapers after most feeds.
        t = wake + timedelta(minutes=rnd.randint(5, 20))
        naps = 0
        while t < day + timedelta(hours=18, minutes=45):
            if rnd.random() < 0.35:
                side = rnd.choice(["left", "right"])
                end = t + timedelta(minutes=rnd.randint(10, 25))
                add("feeding", t, end, left_breast=side == "left", right_breast=side == "right")
            else:
                end = t + timedelta(minutes=rnd.randint(10, 20))
                add("bottle", t, end, amount_ml=rnd.choice([100, 120, 130, 150, 160]), amount_confirmed=True)
            if rnd.random() < 0.8:
                add("diaper", end + timedelta(minutes=rnd.randint(2, 10)), wet=True, pooped=rnd.random() < 0.3)
            nap_start = end + timedelta(minutes=rnd.randint(50, 100))
            nap_len = timedelta(minutes=rnd.randint(35, 110) if naps < 3 else rnd.randint(20, 40))
            if nap_start + nap_len < day + timedelta(hours=18, minutes=30):
                add("sleep", nap_start, nap_start + nap_len)
                naps += 1
                t = nap_start + nap_len + timedelta(minutes=rnd.randint(5, 25))
            else:
                t = nap_start
        if rnd.random() < 0.15:
            add("comment", day + timedelta(hours=rnd.randint(8, 17)), comment=rnd.choice(["Hiccups", "Vitamin D", "Tummy time 10 min", "Bath"]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=35)
    args = parser.parse_args()
    token = (ROOT / ".ha-token").read_text().strip()
    records = generate(args.days)
    for i in range(0, len(records), 500):
        post(
            "/api/services/babylog/upsert",
            {"babies": [{"id": BABY, "name": "Demo"}], "records": records[i : i + 500]},
            token,
        )
    print(f"seeded {len(records)} records for baby Demo ({BABY})", file=sys.stderr)


if __name__ == "__main__":
    main()
