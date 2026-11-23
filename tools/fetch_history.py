#!/usr/bin/env python3
"""Save a heater's raw HA history (switch, power, energy) as a test fixture.

    python3 -I tools/fetch_history.py OUT.json switch.x sensor.x_power [sensor.x_energy] [--days 10]

Reads HA_URL and HA_TOKEN from the environment. The output reveals household
routines, so keep it under tests/fixtures/private/ (gitignored).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.parse
import urllib.request


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("entities", nargs="+")
    ap.add_argument("--days", type=float, default=10.5)
    a = ap.parse_args()
    url = os.environ["HA_URL"].rstrip("/")
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=a.days)
    q = urllib.parse.urlencode({
        "filter_entity_id": ",".join(a.entities),
        "end_time": end.isoformat(),
        "minimal_response": "",
        "no_attributes": "",
    })
    req = urllib.request.Request(
        f"{url}/api/history/period/{urllib.parse.quote(start.isoformat())}?{q}",
        headers={"Authorization": f"Bearer {os.environ['HA_TOKEN']}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        hist = json.load(r)
    out = {"entities": a.entities, "fetched": end.isoformat(),
           "history": {h[0]["entity_id"]: [[s["last_changed"], s["state"]] for s in h] for h in hist if h}}
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    for e, rows in out["history"].items():
        print(f"{e}: {len(rows)} states, {rows[0][0]} .. {rows[-1][0]}")


if __name__ == "__main__":
    main()
