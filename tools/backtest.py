#!/usr/bin/env python3
"""Replay a saved history fixture through the model and print what it found.

    python3 -I tools/backtest.py tests/fixtures/private/master_bath.json [--thermostat 65]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "power_metered_water_heater"))
from model import HeaterModel, Settings, replay  # noqa: E402


def load_events(path: str) -> tuple[list, dict]:
    fx = json.load(open(path))
    sw, pw, en = fx["entities"][:3] if len(fx["entities"]) >= 3 else (*fx["entities"], None)
    ev = []
    for eid, rows in fx["history"].items():
        for ts, st in rows:
            t = dt.datetime.fromisoformat(ts).timestamp()
            if eid == sw and st in ("on", "off"):
                ev.append((t, "switch", st == "on"))
            elif eid == pw and st not in ("unknown", "unavailable"):
                ev.append((t, "power", float(st)))
    return ev, fx


def run(path: str, **settings) -> tuple[HeaterModel, list, list]:
    ev, fx = load_events(path)
    m = HeaterModel(settings=Settings(**settings))
    showers, snaps = [], []
    m.on_shower.append(lambda t, l, mins: showers.append((t, l, mins)))
    replay(m, ev, until=max(e[0] for e in ev) + 300,
           snapshot=lambda t, mm: snaps.append((t, mm.totals.total_l, mm.totals.shower_l, mm.totals.other_l,
                                                 mm.totals.shower_kwh, mm.totals.other_kwh, mm.totals.loss_kwh)))
    return m, showers, snaps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--thermostat", type=float, default=65.0)
    a = ap.parse_args()
    m, showers, snaps = run(a.fixture, thermostat_c=a.thermostat)
    tz = dt.timezone(dt.timedelta(hours=8))
    for t, lit, mins in showers:
        print(f"shower  {dt.datetime.fromtimestamp(t, tz):%a %m-%d %H:%M}  {lit:5.0f} L  {mins:4.1f} min")
    tt = m.totals
    print(f"water  total {tt.total_l:.0f} L = shower {tt.shower_l:.0f} + other {tt.other_l:.0f}")
    print(f"energy shower {tt.shower_kwh:.2f} + other {tt.other_kwh:.2f} + loss {tt.loss_kwh:.2f} = {tt.shower_kwh+tt.other_kwh+tt.loss_kwh:.2f} kWh")
    print(f"learned {m.learned}")
    print(f"now tank {m.tank_temp_c:.1f} C, remaining {m.hot_water_remaining_pct:.0f} %, minutes left {m.shower_minutes_left:.0f}, ready {m.hot_water_ready}")
    dec = [i for i in range(1, len(snaps)) for k in range(1, 7) if snaps[i][k] < snaps[i-1][k] - 1e-9]
    print("hourly snapshots", len(snaps), "decreases", len(dec))


if __name__ == "__main__":
    main()
