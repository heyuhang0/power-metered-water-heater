#!/usr/bin/env python3
"""Replay a saved history through the model with given settings and print what it finds.

    python3 -I tools/backtest.py FIXTURE --tank 35 --thermostat 65 --band 0.24 --heat-loss 1.6 \
        --room 30 --cold 30 --warm 38 [--flow 9.5 --shortest 6]
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
    """(t, 'switch'|'power', value) events from a fetch_history.py fixture (switch first, power second)."""
    fx = json.load(open(path))
    sw, pw = fx["entities"][0], fx["entities"][1]
    ev = []
    for eid, rows in fx["history"].items():
        for ts, st in rows:
            t = dt.datetime.fromisoformat(ts).timestamp()
            if eid == sw and st in ("on", "off"):
                ev.append((t, "switch", st == "on"))
            elif eid == pw and st not in ("unknown", "unavailable"):
                ev.append((t, "power", float(st)))
    return ev, fx


def add_settings_args(ap: argparse.ArgumentParser) -> None:
    for flag, help_ in (("tank", "tank volume, L"), ("thermostat", "thermostat setting, C"),
                        ("band", "thermostat band, kWh"), ("heat-loss", "heat loss, W per C"),
                        ("room", "room temperature, C"), ("cold", "cold water, C"), ("warm", "warm water, C")):
        ap.add_argument(f"--{flag}", type=float, required=True, help=help_)
    ap.add_argument("--flow", type=float, help="shower flow, L/min (showers off if omitted)")
    ap.add_argument("--shortest", type=float, help="shortest shower, min")


def settings_from_args(a) -> Settings:
    return Settings(tank_volume_l=a.tank, thermostat_c=a.thermostat, band_kwh=a.band,
                    heat_loss_w_per_k=a.heat_loss, room_temp_c=a.room, cold_temp_c=a.cold,
                    warm_temp_c=a.warm, shower_flow_lpm=a.flow, shortest_shower_min=a.shortest)


def run(path: str, settings: Settings) -> tuple[HeaterModel, list, list]:
    ev, _ = load_events(path)
    m = HeaterModel(settings=settings)
    showers, snaps = [], []
    m.on_shower.append(lambda t, l, mins: showers.append((t, l, mins)))
    replay(m, ev, until=max(e[0] for e in ev) + 300,
           snapshot=lambda t, mm: snaps.append((t, mm.totals.total_l, mm.totals.shower_l, mm.totals.other_l,
                                                 mm.totals.shower_kwh, mm.totals.other_kwh, mm.totals.loss_kwh,
                                                 mm.totals.energy_kwh)))
    return m, showers, snaps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    add_settings_args(ap)
    a = ap.parse_args()
    m, showers, snaps = run(a.fixture, settings_from_args(a))
    tz = dt.datetime.now().astimezone().tzinfo
    for t, lit, mins in showers:
        print(f"shower  {dt.datetime.fromtimestamp(t, tz):%a %m-%d %H:%M}  {lit:5.0f} L  {mins:4.1f} min")
    tt = m.totals
    print(f"water   total {tt.total_l:.0f} L = shower {tt.shower_l:.0f} + other {tt.other_l:.0f}")
    print(f"energy  {tt.energy_kwh:.2f} kWh = water {tt.water_kwh:.2f} + heat loss {tt.loss_kwh:.2f}"
          f" (+ {m._seg.heat - m._seg.loss_settled if m._seg else 0:.2f} not yet booked)")
    dec = sum(1 for i in range(1, len(snaps)) for k in range(1, 8) if snaps[i][k] < snaps[i - 1][k] - 1e-9)
    print(f"hourly snapshots {len(snaps)}, decreases {dec}")


if __name__ == "__main__":
    main()
