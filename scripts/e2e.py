#!/usr/bin/env python3
"""End-to-end checks for the babylog integration against the docker HA.

    docker compose up -d
    python3 scripts/e2e.py [--restart]

Onboards HA on first run (user dev / devdevdev), adds the integration, then
drives it over REST exactly like the iOS app will. Writes a short-lived
access token to .ha-token for ad-hoc curl use. Stdlib only.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://localhost:8123"
CLIENT_ID = f"{BASE}/"
USER, PASSWORD = "dev", "devdevdev"
ROOT = Path(__file__).resolve().parent.parent

BABY_A = "0A0A0A0A-0000-4000-8000-00000000000A"
BABY_B = "0B0B0B0B-0000-4000-8000-00000000000B"


def request(
    method: str,
    path: str,
    body: Any = None,
    token: str | None = None,
    form: dict[str, str] | None = None,
) -> tuple[int, Any]:
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as err:
        raw = err.read()
        status = err.code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode()


def upload_file(token: str, name: str, content: bytes) -> str:
    """POST /api/file_upload (multipart), as the frontend's file selector does."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/file_upload",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["file_id"]


def options_step(token: str, step: str) -> tuple[str, dict[str, Any]]:
    """Open the integration's Configure menu and pick `step`. Returns (flow_id, step)."""
    status, entries = request("GET", "/api/config/config_entries/entry?domain=babylog", token=token)
    status, menu = request(
        "POST", "/api/config/config_entries/options/flow", {"handler": entries[0]["entry_id"]}, token
    )
    assert status == 200 and menu["type"] == "menu", menu
    status, result = request(
        "POST", f"/api/config/config_entries/options/flow/{menu['flow_id']}", {"next_step_id": step}, token
    )
    assert status == 200, result
    return menu["flow_id"], result


def options_import(token: str, content: bytes, baby: str, mode: str) -> dict[str, Any]:
    """Run the "Import backup" options flow with an uploaded file."""
    flow_id, step = options_step(token, "import_backup")
    assert step["step_id"] == "import_backup", step
    flow = {"flow_id": flow_id}
    file_id = upload_file(token, "junior-backup.json", content)
    status, result = request(
        "POST",
        f"/api/config/config_entries/options/flow/{flow['flow_id']}",
        {"file": file_id, "baby": baby, "mode": mode},
        token,
    )
    assert status == 200, result
    return result


def wait_for_ha(timeout: float = 300) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = request("GET", "/manifest.json")
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    sys.exit("HA did not come up")


def token_from_code(code: str) -> str:
    status, body = request(
        "POST",
        "/auth/token",
        form={"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID},
    )
    assert status == 200, body
    return body["access_token"]


def login() -> str:
    status, steps = request("GET", "/api/onboarding")
    user_done = status != 200 or any(
        s["step"] == "user" and s["done"] for s in steps
    )
    if not user_done:
        status, body = request(
            "POST",
            "/api/onboarding/users",
            {
                "client_id": CLIENT_ID,
                "name": "Dev",
                "username": USER,
                "password": PASSWORD,
                "language": "en",
            },
        )
        assert status == 200, body
        token = token_from_code(body["auth_code"])
        request("POST", "/api/onboarding/core_config", {}, token)
        request("POST", "/api/onboarding/analytics", {}, token)
        request(
            "POST",
            "/api/onboarding/integration",
            {"client_id": CLIENT_ID, "redirect_uri": f"{BASE}/?auth_callback=1"},
            token,
        )
        print("onboarded HA (user dev / devdevdev)")
        return token

    status, flow = request(
        "POST",
        "/auth/login_flow",
        {
            "client_id": CLIENT_ID,
            "handler": ["homeassistant", None],
            "redirect_uri": f"{BASE}/?auth_callback=1",
        },
    )
    assert status == 200, flow
    status, result = request(
        "POST",
        f"/auth/login_flow/{flow['flow_id']}",
        {"client_id": CLIENT_ID, "username": USER, "password": PASSWORD},
    )
    assert result.get("type") == "create_entry", result
    return token_from_code(result["result"])


def ensure_integration(token: str) -> None:
    status, entries = request(
        "GET", "/api/config/config_entries/entry?domain=babylog", token=token
    )
    assert status == 200, entries
    if entries:
        return
    status, flow = request(
        "POST", "/api/config/config_entries/flow", {"handler": "babylog"}, token
    )
    assert status == 200, flow
    status, result = request(
        "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {}, token
    )
    assert result.get("type") == "create_entry", result
    print("added babylog config entry")


def reset_integration(token: str) -> None:
    status, entries = request(
        "GET", "/api/config/config_entries/entry?domain=babylog", token=token
    )
    for entry in entries:
        status, body = request(
            "DELETE", f"/api/config/config_entries/entry/{entry['entry_id']}", token=token
        )
        assert status == 200, body
    ensure_integration(token)
    time.sleep(1)


class Client:
    def __init__(self, token: str) -> None:
        self.token = token

    def call(self, service: str, data: dict[str, Any], response: bool = True) -> Any:
        query = "?return_response" if response else ""
        status, body = request(
            "POST", f"/api/services/babylog/{service}{query}", data, self.token
        )
        if status != 200:
            raise AssertionError(f"{service} -> {status}: {body}")
        return body["service_response"] if response else body

    def call_status(self, service: str, data: dict[str, Any]) -> tuple[int, Any]:
        return request(
            "POST", f"/api/services/babylog/{service}?return_response", data, self.token
        )

    def state(self, entity_id: str) -> dict[str, Any]:
        status, body = request("GET", f"/api/states/{entity_id}", token=self.token)
        assert status == 200, f"{entity_id}: {status} {body}"
        return body


def live(client: Client, baby_id: str) -> int:
    """Live (not soft-deleted) records HA holds for a baby."""
    return client.call("list", {"summary": True, "baby_id": baby_id})["count"]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def same_instant(a: str, b: str) -> bool:
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


passed = 0


def check(label: str, condition: bool, detail: Any = "") -> None:
    global passed
    if not condition:
        sys.exit(f"FAIL {label} {detail}")
    passed += 1
    print(f"  ok  {label}")


def run(client: Client, restart: bool) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    t = lambda minutes: now - timedelta(minutes=minutes)  # noqa: E731
    mod = iso(now)

    feed = {
        "id": "F0000000-0000-4000-8000-000000000001",
        "baby_id": BABY_A,
        "kind": "feeding",
        "started_at": iso(t(120)),
        "ended_at": iso(t(95)),
        "left_breast": True,
        "modified_at": mod,
    }
    old_feed = {  # backfill: two days ago
        **feed,
        "id": "F0000000-0000-4000-8000-000000000000",
        "started_at": iso(t(60 * 48)),
        "ended_at": iso(t(60 * 48 - 20)),
        "left_breast": False,
        "right_breast": True,
    }
    bottle = {
        "id": "B0000000-0000-4000-8000-000000000001",
        "baby_id": BABY_A,
        "kind": "bottle",
        "started_at": iso(t(80)),
        "ended_at": iso(t(70)),
        "amount_ml": 90,
        "amount_confirmed": True,
        "modified_at": mod,
    }
    diaper = {
        "id": "D0000000-0000-4000-8000-000000000001",
        "baby_id": BABY_A,
        "kind": "diaper",
        "started_at": iso(t(60)),
        "wet": True,
        "pooped": True,
        "modified_at": mod,
    }
    sleep = {
        "id": "S0000000-0000-4000-8000-000000000001",
        "baby_id": BABY_A,
        "kind": "sleep",
        "started_at": iso(t(30)),
        "ended_at": None,
        "modified_at": mod,
    }

    print("upsert")
    res = client.call(
        "upsert",
        {
            "babies": [{"id": BABY_A, "name": "Junior"}],
            "records": [old_feed, feed, bottle, diaper, sleep],
        },
    )
    check("all accepted", len(res["accepted"]) == 5 and not res["stale"], res)
    time.sleep(1)  # let new entities register

    print("latest-state sensors")
    s = client.state("sensor.junior_feeding")
    check("not feeding while asleep", s["state"] == "none", s)
    check("feeding since = end of newest feed, not backfilled", same_instant(s["attributes"]["since"], bottle["ended_at"]), s)
    check(
        "last feed attrs",
        s["attributes"]["last_feed"] == "bottle"
        and s["attributes"]["last_amount_ml"] == 90
        and same_instant(s["attributes"]["last_feed_started_at"], bottle["started_at"]),
        s["attributes"],
    )
    s = client.state("binary_sensor.junior_sleeping")
    check("sleeping on, since sleep start", s["state"] == "on" and same_instant(s["attributes"]["since"], sleep["started_at"]), s)
    s = client.state("sensor.junior_diaper")
    check("diaper = both, since change", s["state"] == "both" and same_instant(s["attributes"]["since"], diaper["started_at"]), s)
    check("last poop attr", same_instant(s["attributes"]["last_poop"], diaper["started_at"]), s)
    check("records = 5", live(client, BABY_A) == 5)
    for retired in ("sensor.junior_awake_since", "sensor.junior_milk_today", "sensor.junior_sleep_today"):
        status, _ = request("GET", f"/api/states/{retired}", token=client.token)
        check(f"no {retired}", status == 404, status)

    print("idempotency / staleness")
    res = client.call("upsert", {"records": [feed]})
    check("same version re-accepted", res["accepted"] == [feed["id"]], res)
    res = client.call("upsert", {"records": [{**feed, "modified_at": iso(t(500))}]})
    check("older version stale", res["stale"] == [feed["id"]], res)
    check("still 5 records", live(client, BABY_A) == 5)
    status, body = client.call_status("upsert", {"records": [{**feed, "kind": "nap"}]})
    check("invalid kind rejected", status == 400, (status, body))

    print("edit: end the sleep")
    woke = iso(t(5))
    res = client.call(
        "upsert", {"records": [{**sleep, "ended_at": woke, "modified_at": iso(now + timedelta(seconds=1))}]}
    )
    check("edit accepted", res["accepted"] == [sleep["id"]], res)

    print("feeding now")
    for side, fields in (("left", {"left_breast": True}), ("right", {"right_breast": True}), ("bottle", {})):
        live_feed = {
            "id": f"A0000000-0000-4000-8000-00000000000{len(side)}",
            "baby_id": BABY_A,
            "kind": "bottle" if side == "bottle" else "feeding",
            "started_at": iso(t(3)),
            "modified_at": iso(now),
            **fields,
        }
        client.call("upsert", {"records": [live_feed]}, response=False)
        s = client.state("sensor.junior_feeding")
        check(f"feeding = {side}", s["state"] == side and same_instant(s["attributes"]["since"], live_feed["started_at"]), s)
        client.call("delete", {"ids": [live_feed["id"]]}, response=False)
    check("feeding = none after", client.state("sensor.junior_feeding")["state"] == "none")
    s = client.state("binary_sensor.junior_sleeping")
    check("sleeping off, since = wake time", s["state"] == "off" and same_instant(s["attributes"]["since"], woke), s)

    print("today totals follow each change")
    milk_before = client.state("sensor.junior_feeding")["attributes"]["milk_today_ml"]
    fresh = {**bottle, "id": "B0000000-0000-4000-8000-000000000002", "started_at": iso(now), "ended_at": iso(now), "amount_ml": 40}
    client.call("upsert", {"records": [fresh]}, response=False)
    check("milk today +40 on upsert", client.state("sensor.junior_feeding")["attributes"]["milk_today_ml"] == milk_before + 40)
    client.call("delete", {"ids": [fresh["id"]]}, response=False)
    check("milk today back on delete", client.state("sensor.junior_feeding")["attributes"]["milk_today_ml"] == milk_before)

    print("soft delete")
    since = iso(datetime.now(UTC) - timedelta(seconds=1))
    res = client.call("delete", {"ids": [diaper["id"], "NOT-A-REAL-ID"]})
    check("deleted + missing", res["deleted"] == [diaper["id"]] and res["missing"] == ["NOT-A-REAL-ID"], res)
    res = client.call("delete", {"ids": [diaper["id"]]})
    check("second delete is a no-op", res["already_deleted"] == [diaper["id"]], res)
    check("diaper falls back to none", client.state("sensor.junior_diaper")["state"] == "unknown")
    check("deleted not counted", live(client, BABY_A) == 4)
    res = client.call("list", {"since": since, "include_history": True})
    gone = next((r for r in res["records"] if r["id"] == diaper["id"]), None)
    check("list returns soft-deleted record", gone is not None and gone["deleted_at"], res)
    check("pre-delete version kept in history", len(gone["history"]) == 1 and gone["history"][0]["deleted_at"] is None, gone)
    res = client.call("list", {"include_history": True, "baby_id": BABY_A})
    hist = next(r for r in res["records"] if r["id"] == feed["id"])["history"]
    check("stale version kept in history", any(same_instant(v["modified_at"], iso(t(500))) for v in hist), hist)

    print("second baby")
    res = client.call(
        "upsert",
        {
            "babies": [{"id": BABY_B, "name": "Twin"}],
            "records": [{**diaper, "id": "D0000000-0000-4000-8000-00000000000B", "baby_id": BABY_B}],
        },
    )
    time.sleep(1)
    check("twin entities created", client.state("sensor.twin_feeding")["state"] == "none" and live(client, BABY_B) == 1)
    check("junior unaffected", live(client, BABY_A) == 4)
    res = client.call("list", {"summary": True, "baby_id": BABY_B})
    check("list summary per baby", res["count"] == 1 and set(res["records"][0]) == {"id", "modified_at", "deleted_at"}, res)
    # HA's REST API turns ServiceValidationError into a 500, so check the
    # error path through our own endpoint, which returns a proper 400.
    status, body = request("GET", "/api/babylog/backup", token=client.token)
    check("export needs baby_id with 2 babies", status == 400 and "baby_id" in body["message"], (status, body))
    client.call("upsert", {"babies": [{"id": BABY_B, "name": "Twin B"}]}, response=False)
    name = client.state("sensor.twin_feeding")["attributes"]["friendly_name"]
    check("rename baby updates device name", name == "Twin B Feeding", name)

    print("export / import (iOS backup format)")
    backup = client.call("export", {"baby_id": BABY_A})
    check("export skips soft-deleted", backup["version"] == 1 and len(backup["entries"]) == 4, backup)
    e = next(x for x in backup["entries"] if x["id"] == bottle["id"])
    check(
        "entry uses app keys",
        e["kindRaw"] == "bottle" and e["amountMl"] == 90 and e["leftBoob"] is False,
        e,
    )
    check("dates have no fractional seconds", e["startedAt"].endswith("Z") and "." not in e["startedAt"], e)
    check("syncedAt == modifiedAt", e["syncedAt"] == e["modifiedAt"], e)
    full = client.call("export", {"baby_id": BABY_A, "include_deleted": True})
    check("include_deleted adds deletedAt", any(x["id"] == diaper["id"] and x.get("deletedAt") for x in full["entries"]), full)
    status, raw = request("GET", f"/api/babylog/backup?baby_id={BABY_A}", token=client.token)
    check("GET /api/babylog/backup", status == 200 and len(raw["entries"]) == 4, status)

    # A backup made by the current app: camelCase, no modifiedAt/babyId.
    comment = {
        "id": "C0000000-0000-4000-8000-000000000001",
        "startedAt": iso(t(10)),
        "kindRaw": "comment",
        "wet": False,
        "pooped": False,
        "leftBoob": False,
        "rightBoob": False,
        "amountConfirmed": False,
        "comment": "hiccups",
    }
    legacy = {
        "version": 1,
        "exportedAt": iso(now),
        "entries": [comment, {k: v for k, v in e.items() if k not in ("modifiedAt", "syncedAt", "babyId")}],
    }
    res = client.call("import", {"backup": legacy, "baby_id": BABY_A, "mode": "merge"})
    check("merge imports new, skips existing", res["imported"] == 1 and res["skipped"] == 1, res)
    check("records = 5 after merge", live(client, BABY_A) == 5)

    # Replace with the earlier export, edited: bottle amount changed and the
    # soft-deleted diaper present again. The comment isn't in it.
    replacement = json.loads(json.dumps(backup))
    for x in replacement["entries"]:
        if x["id"] == bottle["id"]:
            x["amountMl"] = 120
    replacement["entries"].append(next(x for x in full["entries"] if x["id"] == diaper["id"]) | {"deletedAt": None})
    status, res = request(
        "POST", f"/api/babylog/backup?baby_id={BABY_A}&mode=replace", replacement, client.token
    )
    check(
        "replace: 3 unchanged, 2 updated (edit + revive), 1 soft-deleted",
        status == 200 and (res["unchanged"], res["updated"], res["deleted"], res["imported"]) == (3, 2, 1, 0),
        res,
    )
    check("bottle updated", client.state("sensor.junior_feeding")["attributes"]["last_amount_ml"] == 120)
    check("diaper revived", same_instant(client.state("sensor.junior_diaper")["attributes"]["since"], diaper["started_at"]))
    res = client.call("list", {"baby_id": BABY_A, "summary": True})
    by_id = {r["id"]: r for r in res["records"]}
    check("comment soft-deleted, not removed", by_id[comment["id"]]["deleted_at"] is not None, by_id)
    check("records = 5 live after replace", res["count"] == 5 and live(client, BABY_A) == 5, res)
    check("twin untouched by replace", live(client, BABY_B) == 1)

    print("statistics (babylog.stats) from records")
    start = (datetime.now(UTC) - timedelta(days=3)).date().isoformat()
    st = client.call("stats", {"baby_id": BABY_A, "start": start, "days": 5})
    sm = st["summary"]
    check("stats milk", sm["milk_ml"] == 120 and sm["bottles"] == 1, sm)
    check("stats feeds by type", sm["feed_types"] == {"bottle": 1, "left": 1, "right": 1, "breast": 0}, sm["feed_types"])
    check("stats diapers", (sm["diapers"], sm["wet"], sm["poopy"]) == (1, 1, 1), sm)
    check("stats sleep minutes", sum(d["sleep_min"] for d in st["days"]) == 25, st["days"])
    check("stats skip soft-deleted", not any(e.get("comment") == "hiccups" for e in st["events"]), st["events"])
    check("stats segments", sorted(x["type"] for x in st["segments"]) == ["bottle", "left", "right", "sleep"], st["segments"])
    check("stats default baby", client.call("stats", {"days": 1})["baby"]["id"] in (BABY_A, BABY_B))
    for name in ("junior-panel.js", "junior-card.js"):
        status, _ = request("GET", f"/babylog_static/{name}")
        check(f"serves {name}", status == 200, status)
    for entity in ("sensor.junior_feeding", "sensor.junior_diaper"):
        check(f"{entity} has no state_class", "state_class" not in client.state(entity)["attributes"])

    print("pairing webhook")
    _, pair = options_step(client.token, "pair")
    link = pair["description_placeholders"]["link"]
    url = pair["description_placeholders"]["url"]
    check("pair step shows QR", pair["step_id"] == "pair" and pair["data_schema"][0]["selector"]["qr_code"]["data"] == link, pair)
    check("QR encodes junior:// link", link.startswith("junior://pair?url=") and urllib.parse.unquote(link.split("url=")[1]) == url, link)
    # HA advertises its own address (the container's IP); reach it via localhost.
    hook = "/api/webhook/" + url.rsplit("/", 1)[1]
    status, res = request("POST", hook, {"action": "list", "summary": True, "baby_id": BABY_A})
    check("webhook list without a token", status == 200 and res["count"] == 5, (status, res))
    wh = {**bottle, "id": "B0000000-0000-4000-8000-0000000000EB", "started_at": iso(t(1)), "ended_at": iso(t(1)), "amount_ml": 10}
    status, res = request("POST", hook, {"action": "upsert", "records": [wh]})
    check("webhook upsert", status == 200 and res["accepted"] == [wh["id"]], (status, res))
    status, res = request("POST", hook, {"action": "delete", "ids": [wh["id"]]})
    check("webhook delete", status == 200 and res["deleted"] == [wh["id"]], (status, res))
    status, res = request("POST", hook, {"action": "stats"})
    check("webhook refuses other actions", status == 400, (status, res))
    status, res = request("POST", hook, {"action": "upsert", "records": [{"id": "x"}]})
    check("webhook validates", status == 400 and "error" in res, (status, res))

    flow_id, confirm = options_step(client.token, "reset_pairing")
    status, res = request("POST", f"/api/config/config_entries/options/flow/{flow_id}", {}, client.token)
    check("reset pairing", res.get("reason") == "pairing_reset", res)
    time.sleep(3)  # entry reloads
    status, res = request("POST", hook, {"action": "list", "summary": True})
    check("old pairing link stops working", res is None or "count" not in res, (status, res))
    _, pair2 = options_step(client.token, "pair")
    hook2 = "/api/webhook/" + pair2["description_placeholders"]["url"].rsplit("/", 1)[1]
    status, res = request("POST", hook2, {"action": "list", "summary": True, "baby_id": BABY_A})
    check("new pairing link works", hook2 != hook and status == 200 and res["count"] == 5, (status, res))

    print("predictions (10 days of realistic data)")
    sys.path.insert(0, str(ROOT / "scripts"))
    import seed  # noqa: PLC0415

    demo = seed.generate(10)
    for i in range(0, len(demo), 500):
        client.call("upsert", {"babies": [{"id": seed.BABY, "name": "Demo"}], "records": demo[i : i + 500]}, response=False)
    time.sleep(1)
    feeding = client.state("sensor.demo_feeding")["attributes"]
    check(
        "feeding has next_feed window",
        feeding.get("next_feed_earliest") and feeding["next_feed_earliest"] <= feeding["next_feed_at"] <= feeding["next_feed_latest"],
        feeding,
    )
    sleeping = client.state("binary_sensor.demo_sleeping")
    prefix = "wake" if sleeping["state"] == "on" else "next_sleep"
    sa = sleeping["attributes"]
    check(f"sleeping has {prefix} window ({sa.get(prefix + '_kind')})", sa.get(prefix + "_earliest") and sa.get(prefix + "_kind"), sa)
    check("learned bedtime / morning wake", sa.get("bedtime") and sa.get("morning_wake"), sa)
    check(
        "today vs last week (%)",
        isinstance(feeding.get("milk_today_pct"), int) and isinstance(sa.get("sleep_today_pct"), int),
        (feeding.get("milk_today_pct"), sa.get("sleep_today_pct")),
    )
    st = client.call("stats", {"baby_id": seed.BABY, "days": 1})
    check("stats carries prediction", st["prediction"]["feed"] is not None, st["prediction"])
    fc = client.call("stats", {"baby_id": seed.BABY, "days": 1})["forecast"]
    evs = fc["events"]
    check("forecast has a chain of events", len(evs) >= 3 and {e["type"] for e in evs} >= {"feed"}, fc)
    check(
        "forecast covers 4 h back and 16 h ahead, no night wakings shown",
        (datetime.fromisoformat(fc["now"]) - datetime.fromisoformat(fc["start"])) == timedelta(hours=4)
        and (datetime.fromisoformat(fc["end"]) - datetime.fromisoformat(fc["now"])) == timedelta(hours=16)
        and not any((e["type"], e["kind"]) in (("wake", "night"), ("sleep", "back_to_sleep")) for e in evs),
        evs,
    )
    ts = datetime.fromisoformat
    check(
        "forecast stays within range, ordered, windows around likely",
        all(ts(fc["now"]) <= ts(e["earliest"]) <= ts(e["likely"]) <= ts(e["latest"]) for e in evs)
        and all(ts(e["likely"]) <= ts(fc["end"]) for e in evs)
        and [ts(e["likely"]) for e in evs] == sorted(ts(e["likely"]) for e in evs),
        evs,
    )
    check(
        "forecast carries the logged last 4 h",
        fc["past"] and all(ts(fc["start"]) <= ts(e["start"]) <= ts(e["end"] or fc["now"]) <= ts(fc["now"]) for e in fc["past"]),
        fc["past"],
    )
    window = client.state("binary_sensor.demo_feed_window")
    in_window = ts(feeding["next_feed_earliest"]) <= datetime.now(UTC) <= ts(feeding["next_feed_latest"])
    check(
        "feed window matches next_feed window",
        window["state"] == ("on" if in_window else "off")
        and window["attributes"].get("next_feed_earliest") == feeding["next_feed_earliest"],
        (window, feeding.get("next_feed_earliest"), feeding.get("next_feed_latest")),
    )
    check("tiles get % of usual", isinstance(st["summary"]["milk_pct"], int) and isinstance(st["summary"]["sleep_pct"], int), st["summary"])
    acc = st["accuracy"]
    check(
        "stats carries backtest accuracy",
        acc["sleep"]["n"] > 10 and acc["feed"]["n"] > 10 and 0 <= acc["feed"]["hit_rate"] <= 1,
        acc,
    )

    print("import via UI file upload (options flow)")
    upload = {**legacy, "entries": [{**comment, "id": "C0000000-0000-4000-8000-000000000002", "comment": "uploaded"}]}
    res = options_import(client.token, json.dumps(upload).encode(), BABY_B, "merge")
    check(
        "upload merge imported",
        res.get("type") == "abort" and res.get("reason") == "imported" and res["description_placeholders"]["imported"] == "1",
        res,
    )
    check("upload landed on chosen baby", live(client, BABY_B) == 2)
    res = options_import(client.token, b"{not json", "auto", "merge")
    check("bad upload shows form error", res.get("type") == "form" and res["errors"] == {"file": "invalid_backup"}, res)

    if restart:
        print("restart persistence")
        subprocess.run(["docker", "compose", "restart"], cwd=ROOT, check=True, capture_output=True)
        time.sleep(3)
        wait_for_ha()
        client.token = login()
        for _ in range(30):
            status, _ = request("GET", "/api/states/sensor.junior_feeding", token=client.token)
            if status == 200:
                break
            time.sleep(2)
        check("records survive restart", live(client, BABY_A) == 5)
        check("uploaded import survives restart", live(client, BABY_B) == 2)
        check(
            "last feed survives restart",
            same_instant(client.state("sensor.junior_feeding")["attributes"]["last_feed_started_at"], bottle["started_at"]),
        )
        res = client.call("list", {"include_history": True, "baby_id": BABY_A})
        by_id = {r["id"]: r for r in res["records"]}
        check("soft deletes + history survive restart", by_id[comment["id"]]["deleted_at"] and by_id[diaper["id"]]["history"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="also verify persistence across an HA restart")
    parser.add_argument("--setup-only", action="store_true", help="onboard + add integration, skip checks")
    args = parser.parse_args()

    wait_for_ha()
    token = login()
    (ROOT / ".ha-token").write_text(token)
    ensure_integration(token)
    if args.setup_only:
        print(f"token (30 min) written to {ROOT / '.ha-token'}")
        return

    # Nothing is ever deleted through the API, so start from a clean slate by
    # removing and re-adding the integration (drops its storage and entities).
    reset_integration(token)
    client = Client(token)

    run(client, args.restart)
    print(f"\n{passed} checks passed")


if __name__ == "__main__":
    main()
