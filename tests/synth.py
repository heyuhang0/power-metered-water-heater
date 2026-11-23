"""Synthetic storage heater: physics ground truth for public tests (no household data)."""
from __future__ import annotations

from dataclasses import dataclass, field

KJ = 4.186


@dataclass
class TrueHeater:
    volume_l: float = 35.0
    setpoint_c: float = 66.0
    band_k: float = 6.0
    element_w: float = 2950.0
    ua_w_per_k: float = 1.8
    room_c: float = 30.0
    cold_c: float = 30.0
    mix_c: float = 38.0
    temp_c: float = 66.0
    switch: bool = True
    heating: bool = False
    t: float = 0.0
    events: list = field(default_factory=list)
    drawn_kwh: dict = field(default_factory=lambda: {"shower": 0.0, "other": 0.0})
    showers: int = 0

    @property
    def c(self) -> float:
        return self.volume_l * KJ / 3600

    def _emit_power(self):
        self.events.append((self.t, "power", self.element_w if (self.switch and self.heating) else 0.0))

    def set_switch(self, on: bool):
        self.switch = on
        self.events.append((self.t, "switch", on))
        self._thermostat(force=True)

    def _thermostat(self, force=False):
        was = self.heating and self.switch
        if self.temp_c <= self.setpoint_c - self.band_k:
            self.heating = True
        elif self.temp_c >= self.setpoint_c:
            self.heating = False
        now = self.heating and self.switch
        if now != was or force:
            self._emit_power()

    def run(self, seconds: float, draw_kw: float = 0.0, kind: str = "other"):
        step = 10.0
        end = self.t + seconds
        while self.t < end - 1e-9:
            dt = min(step, end - self.t)
            dt_h = dt / 3600
            q = -self.ua_w_per_k * (self.temp_c - self.room_c) / 1000 * dt_h
            if self.switch and self.heating:
                q += self.element_w / 1000 * dt_h
            if draw_kw:
                d = min(draw_kw * dt_h, max(0.0, (self.temp_c - self.mix_c) * self.c))
                q -= d
                self.drawn_kwh[kind] += d
            self.temp_c += q / self.c
            self.t += dt
            self._thermostat()

    def shower(self, minutes: float, flow_lpm: float = 9.5):
        kw = flow_lpm * KJ * (self.mix_c - self.cold_c) / 3600 * 60
        self.showers += 1
        self.run(minutes * 60, draw_kw=kw, kind="shower")

    def sink(self, litres: float):
        kwh = litres * KJ * (self.mix_c - self.cold_c) / 3600
        self.run(60, draw_kw=kwh * 60, kind="other")

    def litres(self, kind: str) -> float:
        return self.drawn_kwh[kind] / (KJ * (self.mix_c - self.cold_c) / 3600)


def week(seed_offset: float = 0.0) -> TrueHeater:
    """Seven days: morning on/off schedule, showers, sink use, a manual switch-off mid-reheat."""
    h = TrueHeater()
    h.events.append((0.0, "switch", True))
    h._emit_power()
    H = 3600
    for day in range(7):
        h.run(8 * H - (h.t % (24 * H)) if day else 8 * H)       # until 08:00
        if not h.switch:
            h.set_switch(True)
        h.run(30 * 60)                                            # wait for hot water
        h.shower(10 + day % 4)                                    # 10-13 min
        h.run(40 * 60)
        if day % 2 == 0:
            h.sink(6)
        h.run(10 * H - (h.t % (24 * H)))                          # until 10:00
        if day == 3:                                              # stays on all day, evening shower
            h.run(9 * H)
            h.shower(12)
            h.run(5 * 60)
            h.set_switch(False)                                   # cut the reheat short
            h.run(2 * H)
        else:
            h.set_switch(False)
            h.run(H)
            h.sink(3)                                             # used while off
            h.run(13 * H - (h.t % (24 * H)) % H)
        h.run(max(0.0, 24 * H * (day + 1) - h.t - 1))
    return h


def true_settings(h: TrueHeater, showers: bool = True):
    """The model settings that describe this synthetic heater exactly."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "power_metered_water_heater"))
    from model import Settings
    return Settings(tank_volume_l=h.volume_l, thermostat_c=h.setpoint_c, band_kwh=h.band_k * h.c,
                    heat_loss_w_per_k=h.ua_w_per_k, room_temp_c=h.room_c, cold_temp_c=h.cold_c,
                    warm_temp_c=h.mix_c, shower_flow_lpm=9.5 if showers else None,
                    shortest_shower_min=6.0 if showers else None)


def as_fixture(h: TrueHeater, start: float = 1_790_000_000.0) -> dict:
    """The synthetic events in fetch_history.py's fixture format (for tools/analyze.py)."""
    import datetime as dt
    iso = lambda t: dt.datetime.fromtimestamp(start + t, dt.timezone.utc).isoformat()  # noqa: E731
    sw = [[iso(t), "on" if v else "off"] for t, k, v in h.events if k == "switch"]
    pw = [[iso(t), str(v)] for t, k, v in h.events if k == "power"]
    return {"entities": ["switch.x", "sensor.x_power"], "history": {"switch.x": sw, "sensor.x_power": pw}}
