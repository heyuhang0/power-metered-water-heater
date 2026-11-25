"""Storage water heater model driven only by a metering switch's on/off state and power.

Pure Python (no Home Assistant imports) so it can be replayed and tested offline.
Nothing is learned: every parameter is a setting, chosen once from past data or a
measurement. The only live inputs are the switch state and its power.

How it works
------------
The tank's *heat deficit* D (kWh below full) is tracked continuously:

* D grows with standby heat loss, heat_loss_w_per_k x (tank - room), and with
  warm water use.
* D shrinks with the heat the switch measures.
* When the thermostat stops a reheat while the switch is still on, the tank is
  full: D = 0. These *anchors* make the accounting exact.

Booking: whenever a reheat stops (tank full, or cut short by the switch), its
measured heat is booked. The water it proves goes to water, the rest to heat
loss; water the tank still lacks is booked when heat replaces it. So whenever no
reheat is running, water energy + heat loss = the measured energy.

Water energy is converted to litres at the warm water temperature. With showers
enabled, water of at least ``shortest_shower_min`` (at the shower flow) is a shower.

Live behaviour: during a reheat only the guaranteed minimum is booked, so running
totals never go down.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

WATER_KJ_PER_L_K = 4.186
HEATING_W = 500.0            # power above this = element on
END_CONFIRM_S = 30.0         # wait this long after a power drop before deciding why it stopped
STEP_S = 60.0                # integration step
BAND_MARGIN_KWH = 0.03       # slack on the thermostat band before counting a draw
MIXING_WINDOW_S = 4 * 3600   # first small reheat this soon after a big one = tank was not fully mixed
BIG_REHEAT_KWH = 0.3
NOISE_KWH = 0.05             # usage below this is model noise
TYPICAL_SHOWER_MIN = 12.0    # live tank guess during a shower; cap for a cut-short shower's remainder
MAX_MINUTES_LEFT = 240


@dataclass
class Settings:
    tank_volume_l: float
    thermostat_c: float
    band_kwh: float                     # energy of one normal small reheat
    heat_loss_w_per_k: float            # standby loss per degree between tank and room
    room_temp_c: float
    cold_temp_c: float
    warm_temp_c: float                  # all water is counted as litres at this temperature
    shower_flow_lpm: float | None = None
    shortest_shower_min: float | None = None

    @property
    def capacity_kwh_per_k(self) -> float:
        return self.tank_volume_l * WATER_KJ_PER_L_K / 3600

    @property
    def kwh_per_litre(self) -> float:
        return WATER_KJ_PER_L_K * max(0.5, self.warm_temp_c - self.cold_temp_c) / 3600

    @property
    def showers_enabled(self) -> bool:
        return bool(self.shower_flow_lpm) and bool(self.shortest_shower_min)

    @property
    def shower_kw(self) -> float | None:
        return self.shower_flow_lpm * self.kwh_per_litre * 60 if self.showers_enabled else None

    @property
    def shower_threshold_kwh(self) -> float | None:
        return self.shortest_shower_min / 60 * self.shower_kw if self.showers_enabled else None


@dataclass
class Totals:
    shower_l: float = 0.0
    other_l: float = 0.0
    shower_kwh: float = 0.0
    other_kwh: float = 0.0
    loss_kwh: float = 0.0
    energy_kwh: float = 0.0              # all measured heat since tracking began
    shower_count: int = 0

    @property
    def total_l(self) -> float:
        return self.shower_l + self.other_l

    @property
    def water_kwh(self) -> float:
        return self.shower_kwh + self.other_kwh


@dataclass
class _Burst:
    start: float
    d0: float
    after_on: bool
    early: bool
    heat: float = 0.0
    lb: float = 0.0          # guaranteed-minimum water energy so far
    best: float = 0.0        # best-guess water energy so far (tank state only)
    shower_live: bool = False
    booked: float = 0.0      # water energy already booked from this burst


@dataclass
class _Segment:
    start: float
    heat: float = 0.0
    loss: float = 0.0
    classified: float = 0.0       # water energy already classified (cut-short reheats)
    booked: float = 0.0           # water energy already added to totals
    was_off: bool = False
    bursts: int = 0
    cut_shower_kwh: float = 0.0   # shower credited from a reheat the switch cut short
    water_booked: float = 0.0     # water energy added to the totals in this segment (after debts)
    loss_prebooked: float = 0.0   # heat loss already booked live by versions before 0.1.4
    loss_settled: float = 0.0     # heat booked as loss at switch-offs within this segment
    pending_kwh: float = 0.0      # water from a cut-short reheat, too small to call yet: classified with the rest


ShowerCallback = Callable[[float, float, float], None]   # (time, litres, minutes)


@dataclass
class HeaterModel:
    settings: Settings
    totals: Totals = field(default_factory=Totals)
    t: float | None = None
    switch_on: bool = False
    power_w: float = 0.0
    heating_kw: float | None = None       # element power seen in the latest reheat
    deficit: float | None = None          # None until the first full reheat
    on_since: float | None = None
    full_since_on: bool = False
    last_shower_l: float | None = None
    last_shower_min: float | None = None
    _burst: _Burst | None = None
    _seg: _Segment | None = None
    _end_pending: float | None = None
    _prev_big: dict | None = None
    _water_debt: float = 0.0
    _loss_debt: float = 0.0

    def __post_init__(self) -> None:
        self.on_shower: list[ShowerCallback] = []

    # ------------------------------------------------------------------ inputs
    def set_switch(self, t: float, on: bool) -> None:
        self.advance(t)
        if on == self.switch_on:
            return
        if not on and self._end_pending is not None:
            # the reheat had already stopped when the switch went off (a drop the switch
            # causes is always reported after it): the thermostat stopped it, tank full
            self._finish_burst(self._end_pending, anchor=True)
        self.switch_on = on
        if on:
            self.on_since = t
            self.full_since_on = False
        else:
            self.on_since = None
            self.full_since_on = False
            if self._seg is not None:
                self._seg.was_off = True
            if self._end_pending is not None or self._burst is not None:
                self._finish_burst(t, anchor=False)

    def set_power(self, t: float, watts: float) -> None:
        self.advance(t)
        was = self.power_w > HEATING_W
        now = watts > HEATING_W
        self.power_w = watts
        if now and not was:
            if self._end_pending is not None:      # heating resumed: same reheat
                self._end_pending = None
            else:
                self._start_burst(t)
        elif was and not now and self._burst is not None:
            self._end_pending = t

    def advance(self, t: float) -> None:
        """Integrate from the last update to ``t`` with the current inputs."""
        if self.t is None:
            self.t = t
            return
        while self.t < t:
            if self._end_pending is not None and self.t >= self._end_pending + END_CONFIRM_S:
                self._finish_burst(self._end_pending, anchor=self.switch_on)
            nxt = min(t, self.t + STEP_S)
            if self._end_pending is not None:
                nxt = min(nxt, max(self.t + 1e-6, self._end_pending + END_CONFIRM_S))
            self._step(self.t, (nxt - self.t) / 3600)
            self.t = nxt
        if self._end_pending is not None and self.t >= self._end_pending + END_CONFIRM_S:
            self._finish_burst(self._end_pending, anchor=self.switch_on)
        self._check_full_without_reheat()

    # ---------------------------------------------------------------- outputs
    @property
    def tank_temp_c(self) -> float | None:
        if self.deficit is None:
            return None
        s = self.settings
        return max(s.cold_temp_c, s.thermostat_c - self.deficit / s.capacity_kwh_per_k)

    @property
    def hot_water_remaining_pct(self) -> float | None:
        tt = self.tank_temp_c
        if tt is None:
            return None
        s = self.settings
        span = s.thermostat_c - s.warm_temp_c
        return max(0.0, min(100.0, (tt - s.warm_temp_c) / span * 100)) if span > 0 else 0.0

    @property
    def shower_minutes_left(self) -> float | None:
        """Shower time from now; with the switch on the element helps (the thermostat will call)."""
        tt = self.tank_temp_c
        s = self.settings
        if tt is None or not s.showers_enabled:
            return None
        stored = max(0.0, s.capacity_kwh_per_k * (tt - s.warm_temp_c))
        net = s.shower_kw - ((self.heating_kw or 0.0) if self.switch_on else 0.0)
        if net <= 0:
            return float(MAX_MINUTES_LEFT)
        return min(float(MAX_MINUTES_LEFT), stored / net * 60)

    @property
    def hot_water_ready(self) -> bool:
        return self.switch_on and self.full_since_on

    @property
    def heating(self) -> bool:
        return self.power_w > HEATING_W

    # --------------------------------------------------------------- internals
    def _step(self, t: float, dt_h: float) -> None:
        s = self.settings
        p_kw = self.power_w / 1000 if self.power_w > HEATING_W else 0.0
        if self.deficit is not None:
            tt = self.tank_temp_c
            loss = max(0.0, s.heat_loss_w_per_k * (tt - s.room_temp_c) / 1000 * dt_h) if tt is not None else 0.0
            self.deficit += loss
            if self._seg is not None:
                self._seg.loss += loss
        if p_kw <= 0 or self._burst is None:
            return
        self.heating_kw = p_kw
        b = self._burst
        heat = p_kw * dt_h
        b.heat += heat
        if self._seg is not None:
            self._seg.heat += heat
            self.totals.energy_kwh += heat
        if self.deficit is not None:
            self.deficit = max(0.0, self.deficit - heat)
        if self.deficit is None or self._seg is None:
            return
        # guaranteed minimum water energy drawn during this reheat
        b.lb = max(b.lb, b.heat - b.d0 - BAND_MARGIN_KWH)
        if s.showers_enabled:
            if not b.shower_live and b.lb >= s.shower_threshold_kwh:
                b.shower_live = True
            live_kind = "shower" if b.shower_live else None
        else:
            live_kind = "other"            # no showers: all water is one total, booked live
        if live_kind and b.lb > b.booked:
            self._book_water(live_kind, b.lb - b.booked)
            self._seg.booked += b.lb - b.booked
            b.booked = b.lb
        # tank state: at least the proven draw has left the tank; with showers enabled,
        # assume an ongoing shower once a draw is evident
        best = b.lb
        if s.showers_enabled and (b.early or b.heat > b.d0 + BAND_MARGIN_KWH):
            elapsed_min = (t - b.start) / 60 + dt_h * 60
            best = max(best, min(elapsed_min, TYPICAL_SHOWER_MIN) / 60 * s.shower_kw)
        if best > b.best:
            self.deficit += best - b.best
            b.best = best

    def _start_burst(self, t: float) -> None:
        band = self.settings.band_kwh
        after_on = self.on_since is not None and t - self.on_since <= 120
        d = self.deficit if self.deficit is not None else 0.0
        early = (not after_on) and self.deficit is not None and d < band * 0.8
        d0 = d if after_on else max(d, band)
        if not after_on and self.deficit is not None and d < band:
            # the thermostat fired: the tank is one band down whatever we predicted
            self.deficit = band
        self._burst = _Burst(start=t, d0=d0, after_on=after_on, early=early)
        if self._seg is not None:
            self._seg.bursts += 1

    def _finish_burst(self, t_end: float, anchor: bool) -> None:
        b = self._burst
        self._burst = None
        self._end_pending = None
        if b is None:
            return
        seg = self._seg
        if self.deficit is None:
            if anchor:                           # first full tank: start tracking
                self.deficit = 0.0
                self._seg = _Segment(start=t_end)
                self._mark_full()
            return
        if seg is None:
            seg = self._seg = _Segment(start=b.start)
        s = self.settings
        if not anchor:
            # reheat cut short by the switch: credit the water this reheat proves
            if b.early or b.after_on or b.shower_live:
                u = max(0.0, b.heat - b.d0)
                if u >= NOISE_KWH:
                    if s.showers_enabled and not b.shower_live and u < s.shower_threshold_kwh:
                        # not provably a shower yet: it is part of the water found at the next
                        # full tank and classified with it; until then keep it out of heat loss
                        seg.pending_kwh += u
                    else:
                        kind = self._classify(u, t_end, b)
                        if kind == "shower":
                            seg.cut_shower_kwh += u
                        extra = max(0.0, u - b.booked)
                        self._book_water(kind, extra)
                        seg.booked += extra
                        seg.classified += u
                    self.deficit = max(0.0, self.deficit - b.best + u)
            # the switch measured this heat now: book it now, so the parts match the meter
            self._settle_loss(seg)
            return
        u = seg.heat - seg.loss - seg.classified
        live_unclassified = seg.booked - seg.classified
        mixing = (self._prev_big is not None and b.start - self._prev_big["end"] < MIXING_WINDOW_S
                  and b.heat < BIG_REHEAT_KWH and not seg.was_off and not seg.pending_kwh)
        if mixing and self._prev_big["kind"] != "shower":
            # the tank was not fully mixed after a reheat from cold: this is heat loss
            self._prev_big = None
        elif mixing:
            # ... after a shower: the shower drew more than its reheat showed
            extra = max(0.0, u - live_unclassified)
            self._book_water("shower", extra)
            if self.last_shower_l is not None:
                self.last_shower_l += extra / s.kwh_per_litre
                self.last_shower_min = (self.last_shower_min or 0) + extra / s.shower_kw * 60
            self._prev_big = None
        else:
            uu = max(0.0, u)                  # includes water held from a cut-short reheat
            seg.pending_kwh = 0.0
            if seg.cut_shower_kwh > 0 and uu > 0 and s.showers_enabled:
                # the heat still missing from a cut-short shower shows up in this reheat
                room = max(0.0, TYPICAL_SHOWER_MIN / 60 * s.shower_kw - seg.cut_shower_kwh)
                carry = min(uu, room)
                if carry > 0:
                    self._book_water("shower", max(0.0, carry - max(0.0, live_unclassified)))
                    live_unclassified = max(0.0, live_unclassified - carry)
                    self.last_shower_l = (self.last_shower_l or 0) + carry / s.kwh_per_litre
                    self.last_shower_min = (self.last_shower_min or 0) + carry / s.shower_kw * 60
                    uu -= carry
            kind = self._classify(uu, t_end, b) if (uu >= NOISE_KWH or b.shower_live) else None
            extra = uu - live_unclassified
            if extra > 0:
                self._book_water(kind or "other", extra)
            elif extra < 0:
                self._water_debt += -extra
            self._prev_big = ({"end": t_end, "kind": kind}
                              if (kind == "shower" or b.heat > BIG_REHEAT_KWH) else None)
        # the heater has now replaced everything the tank lost: whatever this segment's
        # measured heat did not put into water is heat loss, so the parts add up to the meter
        self._settle_loss(seg)
        self.deficit = 0.0
        self._seg = _Segment(start=t_end)
        self._mark_full()

    def _classify(self, u: float, t: float, b: _Burst) -> str | None:
        s = self.settings
        if s.showers_enabled and (b.shower_live or u >= s.shower_threshold_kwh):
            litres = u / s.kwh_per_litre
            minutes = u / s.shower_kw * 60
            self.totals.shower_count += 1
            self.last_shower_l, self.last_shower_min = litres, minutes
            for cb in self.on_shower:
                cb(t, litres, minutes)
            return "shower"
        return "other" if u >= NOISE_KWH else None

    def _book_water(self, kind: str | None, kwh: float) -> None:
        if kwh <= 0:
            return
        if self._water_debt:
            take = min(self._water_debt, kwh)
            self._water_debt -= take
            kwh -= take
            if kwh <= 0:
                return
        if self._seg is not None:
            self._seg.water_booked += kwh
        lit = kwh / self.settings.kwh_per_litre
        if kind == "shower":
            self.totals.shower_kwh += kwh
            self.totals.shower_l += lit
        else:
            self.totals.other_kwh += kwh
            self.totals.other_l += lit

    def _book_loss(self, kwh: float) -> None:
        if kwh <= 0:
            return
        if self._loss_debt:
            take = min(self._loss_debt, kwh)
            self._loss_debt -= take
            kwh -= take
        self.totals.loss_kwh += kwh

    def _settle_loss(self, seg: _Segment) -> None:
        """Book as heat loss the segment's measured heat not yet booked as water or loss."""
        x = seg.heat - seg.water_booked - seg.loss_prebooked - seg.loss_settled - seg.pending_kwh
        if x < 0:
            self._loss_debt += -x
        else:
            self._book_loss(x)
        seg.loss_settled += x

    def _mark_full(self) -> None:
        if self.switch_on and self.on_since is not None:
            self.full_since_on = True

    def _check_full_without_reheat(self) -> None:
        """Switched on with the tank still full: no reheat comes, so it is ready."""
        if (self.switch_on and not self.full_since_on and self.on_since is not None
                and self._burst is None and self.deficit is not None
                and self.t is not None and self.t - self.on_since >= 300
                and self.deficit < self.settings.band_kwh):
            self.full_since_on = True

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        """Model state. Settings are not stored: they come from the config entry."""
        d = {k: v for k, v in self.__dict__.items() if k not in ("on_shower", "settings")}
        d["totals"] = asdict(self.totals)
        d["_burst"] = asdict(self._burst) if self._burst else None
        d["_seg"] = asdict(self._seg) if self._seg else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], settings: Settings) -> "HeaterModel":
        d = dict(d)
        d.pop("settings", None)
        d.pop("learned", None)                       # saved by <= 0.1.x
        d.pop("last_correction_l", None)
        d.pop("_heat_w_ewma", None)
        totals = d.pop("totals", None) or {}
        if "energy_kwh" not in totals:
            # saved by <= 0.1.x: start the energy total where the parts are
            totals = {**totals, "energy_kwh": sum(totals.get(k, 0.0) for k in ("shower_kwh", "other_kwh", "loss_kwh"))}
        m = cls(settings=settings, totals=Totals(**totals))
        b, sg = d.pop("_burst", None), d.pop("_seg", None)
        for k, v in d.items():
            if hasattr(m, k):
                setattr(m, k, v)
        m._burst = _Burst(**b) if b else None
        if sg and "water_booked" not in sg:
            # saved by <= 0.1.3, which booked heat loss live: don't book this segment's loss twice
            sg = {**sg, "water_booked": sg.get("booked", 0.0), "loss_prebooked": sg.get("loss", 0.0)}
        m._seg = _Segment(**sg) if sg else None
        return m


def replay(model: HeaterModel, events: list[tuple[float, str, Any]], until: float | None = None,
           snapshot: Callable[[float, HeaterModel], None] | None = None, every_s: float = 3600) -> None:
    """Feed time-ordered (t, 'switch'|'power', value) events; optionally snapshot on the hour."""
    next_snap = None
    for t, kind, value in sorted(events, key=lambda e: e[0]):
        if snapshot is not None:
            if next_snap is None:
                next_snap = (t // every_s + 1) * every_s
            while next_snap <= t:
                model.advance(next_snap)
                snapshot(next_snap, model)
                next_snap += every_s
        if kind == "switch":
            model.set_switch(t, bool(value))
        elif kind == "power":
            model.set_power(t, float(value))
    if until is not None:
        if snapshot is not None and next_snap is not None:
            while next_snap <= until:
                model.advance(next_snap)
                snapshot(next_snap, model)
                next_snap += every_s
        model.advance(until)
