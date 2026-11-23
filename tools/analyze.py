#!/usr/bin/env python3
"""Suggest heater settings from a saved history (see fetch_history.py).

    python3 -I tools/analyze.py FIXTURE --thermostat 65 --room 30 [--tank 35]

Reports, from the switch's power alone:
* element power: median power while heating
* thermostat band: median energy of a normal small reheat
* heat loss (W per C): 25th percentile of clean idle cycles. A cycle is the time from
  a full tank to the next small reheat with the switch on throughout. Water use and
  tank mixing can only shorten a cycle (make the loss look higher), so cycles under
  3 h and the first small reheat after a big one or a switch-on are skipped, and a
  low percentile of the rest is used.
* off periods: average loss from a full tank, through a switch-off, to the reheat back
  to full. They include any water used while off, so they are a cross-check only.
* thermostat lower bound: the most heat a tank lost while off cannot exceed
  volume x (thermostat - room).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics as st
import sys

WATER_KJ_PER_L_K = 4.186
HEATING_W = 500.0
BIG_REHEAT_KWH = 0.3
MIXING_WINDOW_S = 4 * 3600
MIN_IDLE_H = 3.0          # shorter idle cycles were cut short by water use


def analyse(fx: dict, thermostat: float, room: float, tank: float) -> dict:
    sw_id, pw_id = fx["entities"][0], fx["entities"][1]
    H = fx["history"]
    T = lambda s: dt.datetime.fromisoformat(s).timestamp()  # noqa: E731
    pw = [(T(a), float(b)) for a, b in H[pw_id] if b not in ("unknown", "unavailable")]
    sw = [(T(a), b == "on") for a, b in H[sw_id] if b in ("on", "off")]

    def sw_at(t: float) -> bool:
        s = False
        for a, b in sw:
            if a <= t:
                s = b
            else:
                break
        return s

    end = pw[-1][0]
    bursts, cur = [], None
    for (a, p), (n, _) in zip(pw, pw[1:] + [(end, 0)]):
        if p > HEATING_W:
            if cur is None:
                cur = {"start": a, "end": n, "kwh": 0.0, "w": []}
            cur["end"] = n
            cur["kwh"] += p * (n - a) / 3.6e6
            cur["w"].append(p)
        elif cur:
            bursts.append(cur)
            cur = None
    for b in bursts:
        b["full"] = sw_at(b["end"] + 20)                       # thermostat stopped it, tank full
        b["after_on"] = any(on and 0 <= b["start"] - a <= 120 for a, on in sw)
    element_w = st.median(w for b in bursts for w in b["w"])

    cap = tank * WATER_KJ_PER_L_K / 3600
    idle, off = [], []
    for prev, b in zip(bursts, bursts[1:]):
        if not (prev["full"] and b["full"]):
            continue
        hours = (b["end"] - prev["end"]) / 3600
        on_all = all(sw_at(t) for t in range(int(prev["end"]), int(b["start"]), 300))
        if on_all and b["kwh"] < BIG_REHEAT_KWH:
            mixing = ((prev["kwh"] >= BIG_REHEAT_KWH or prev["after_on"])
                      and b["start"] - prev["end"] < MIXING_WINDOW_S)
            disturbed = "tank mixing after a big reheat or switch-on" if mixing else (
                f"shorter than {MIN_IDLE_H:g} h: water was used" if hours < MIN_IDLE_H else None)
            avg_temp = thermostat - b["kwh"] / cap / 2
            idle.append({"at": prev["end"], "hours": hours, "kwh": b["kwh"], "w": b["kwh"] / hours * 1000,
                         "w_per_k": b["kwh"] / hours * 1000 / max(1.0, avg_temp - room), "skipped": disturbed})
        elif not on_all and b["after_on"] and hours > 1:
            off.append({"at": prev["end"], "hours": hours, "kwh": b["kwh"], "w": b["kwh"] / hours * 1000})

    band = st.median(x["kwh"] for x in idle) if idle else None
    clean = sorted(x["w_per_k"] for x in idle if not x["skipped"])
    p25 = clean[max(0, int(round(0.25 * (len(clean) - 1))))] if clean else None
    off_max = max((x["kwh"] for x in off), default=None)
    return {"element_w": element_w, "band_kwh": band, "heat_loss_w_per_k": p25, "clean_cycles": len(clean),
            "idle": idle, "off": off,
            "thermostat_at_least_c": room + off_max / cap if off_max else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--thermostat", type=float, required=True)
    ap.add_argument("--room", type=float, required=True)
    ap.add_argument("--tank", type=float, required=True)
    a = ap.parse_args()
    r = analyse(json.load(open(a.fixture)), a.thermostat, a.room, a.tank)
    tz = dt.datetime.now().astimezone().tzinfo
    f = lambda t: dt.datetime.fromtimestamp(t, tz).strftime("%a %d %H:%M")  # noqa: E731
    print("Idle cycles (switch on, one small reheat):")
    for x in sorted(r["idle"], key=lambda x: x["w"]):
        print(f"  {f(x['at'])}  {x['hours']:5.2f} h  {x['kwh']:.3f} kWh  {x['w']:6.1f} W  {x['w_per_k']:.2f} W/C"
              f"{'  (skipped: ' + x['skipped'] + ')' if x['skipped'] else ''}")
    print("Off periods (full -> off -> reheat to full; include water used while off):")
    for x in sorted(r["off"], key=lambda x: x["hours"]):
        print(f"  {f(x['at'])}  {x['hours']:5.2f} h  {x['kwh']:.3f} kWh  avg {x['w']:6.1f} W")
    print("Suggested settings:")
    print(f"  element power      {r['element_w']:.0f} W (measured, not a setting)")
    if r["band_kwh"]:
        print(f"  thermostat band    {r['band_kwh']:.3f} kWh")
    if r["heat_loss_w_per_k"]:
        print(f"  heat loss          {r['heat_loss_w_per_k']:.2f} W/C  (25th percentile of {r['clean_cycles']} clean cycles)")
    if r["thermostat_at_least_c"]:
        print(f"  thermostat         at least {r['thermostat_at_least_c']:.0f} C (from the largest off-period loss)")


if __name__ == "__main__":
    main()
