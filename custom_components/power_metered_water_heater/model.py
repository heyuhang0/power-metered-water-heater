"""Storage water heater model driven only by a metering switch's on/off state and power.

Pure Python (no Home Assistant imports) so it can be replayed and tested offline.

How it works
------------
The tank's *heat deficit* D (kWh below full) is tracked continuously:

* D grows with standby heat loss (Newton cooling towards room temperature)
  and with hot water use.
* D shrinks with the heater energy the switch measures.
* When the thermostat stops a reheat while the switch is still on, the tank is
  full: D = 0. These *anchors* make the accounting exact. Between two anchors,
  water energy = heater energy - modelled heat loss.

Water energy is converted to litres of mixed water at the shower temperature.
A reheat whose water energy exceeds ``shower_threshold_kwh`` is a shower.

Energy: when a reheat ends with the tank full, the segment's metered heat is
split into water (shower / other) and heat loss, so the three parts always add
up to what the switch measured.

Live behaviour: during a reheat only the guaranteed minimum is booked, so the
running totals never have to go down. Once that minimum passes the shower
threshold the reheat is known to be a shower and its litres are booked live.
Everything else is settled when the reheat ends.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

WATER_KJ_PER_L_K = 4.186
HEATING_W = 500.0            # power above this = element on
END_CONFIRM_S = 30.0         # wait this long after a power drop before deciding why it stopped
STEP_S = 60.0                # integration step
BAND_MARGIN_KWH = 0.03       # slack on the thermostat band before counting a draw
FOLLOWUP_S = 2 * 3600        # quick band cycle after a big reheat = tank was not fully mixed
QUIET_MIN_S = 3 * 3600       # idle gap long enough to learn heat loss from
NOISE_KWH = 0.05             # usage below this is model noise
MAX_MINUTES_LEFT = 240


@dataclass
class Settings:
    tank_volume_l: float = 35.0
    thermostat_c: float = 65.0
    shower_flow_lpm: float = 9.5
    shower_temp_c: float = 38.0
    cold_temp_c: float = 30.0
    room_temp_c: float = 30.0
    shower_threshold_kwh: float = 0.35

    @property
    def capacity_kwh_per_k(self) -> float:
        return self.tank_volume_l * WATER_KJ_PER_L_K / 3600

    @property
    def kwh_per_litre(self) -> float:
        return WATER_KJ_PER_L_K * max(0.5, self.shower_temp_c - self.cold_temp_c) / 3600

    @property
    def shower_kw(self) -> float:
        return self.shower_flow_lpm * self.kwh_per_litre * 60


@dataclass
class Learned:
    element_kw: float = 2.95
    band_kwh: float = 0.24
    loss_w_full: float = 68.0        # heat loss with the tank full
    typical_shower_min: float = 12.0
    quiet_cycles: int = 0


@dataclass
class Totals:
    shower_l: float = 0.0
    other_l: float = 0.0
    shower_kwh: float = 0.0
    other_kwh: float = 0.0
    loss_kwh: float = 0.0
    shower_count: int = 0

    @property
    def total_l(self) -> float:
        return self.shower_l + self.other_l


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
    classified: float = 0.0  # water energy already classified (cut-short reheats)
    booked: float = 0.0      # water energy already added to totals
    was_off: bool = False
    bursts: int = 0
    cut_shower_kwh: float = 0.0   # shower credited from a reheat the switch cut short
    water_booked: float = 0.0     # water energy added to the totals in this segment (after debts)
    loss_prebooked: float = 0.0   # heat loss already booked live by versions before 0.1.4


ShowerCallback = Callable[[float, float, float], None]   # (time, litres, minutes)


@dataclass
class HeaterModel:
    settings: Settings = field(default_factory=Settings)
    learned: Learned = field(default_factory=Learned)
    totals: Totals = field(default_factory=Totals)
    t: float | None = None
    switch_on: bool = False
    power_w: float = 0.0
    deficit: float | None = None          # None until the first full reheat
    on_since: float | None = None
    full_since_on: bool = False
    last_shower_l: float | None = None
    last_shower_min: float | None = None
    last_correction_l: float | None = None
    _burst: _Burst | None = None
    _seg: _Segment | None = None
    _end_pending: float | None = None
    _prev_big: dict | None = None
    _water_debt: float = 0.0
    _loss_debt: float = 0.0
    _heat_w_ewma: float | None = None

    def __post_init__(self) -> None:
        self.on_shower: list[ShowerCallback] = []

    # ------------------------------------------------------------------ inputs
    def set_switch(self, t: float, on: bool) -> None:
        self.advance(t)
        if on == self.switch_on:
            return
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
        span = s.thermostat_c - s.shower_temp_c
        return max(0.0, min(100.0, (tt - s.shower_temp_c) / span * 100)) if span > 0 else 0.0

    @property
    def shower_minutes_left(self) -> float | None:
        tt = self.tank_temp_c
        if tt is None:
            return None
        s = self.settings
        stored = max(0.0, s.capacity_kwh_per_k * (tt - s.shower_temp_c))
        net = s.shower_kw - (self.learned.element_kw if self.switch_on else 0.0)
        if net <= 0:
            return float(MAX_MINUTES_LEFT)
        return min(float(MAX_MINUTES_LEFT), stored / net * 60)

    @property
    def heat_loss_w(self) -> float | None:
        tt = self.tank_temp_c
        return None if tt is None else max(0.0, self._ua() * (tt - self.settings.room_temp_c))

    @property
    def hot_water_ready(self) -> bool:
        return self.switch_on and self.full_since_on

    @property
    def heating(self) -> bool:
        return self.power_w > HEATING_W

    # --------------------------------------------------------------- internals
    def _ua(self) -> float:
        s = self.settings
        return self.learned.loss_w_full / max(1.0, s.thermostat_c - s.room_temp_c)

    def _step(self, t: float, dt_h: float) -> None:
        s, ln = self.settings, self.learned
        p_kw = self.power_w / 1000 if self.power_w > HEATING_W else 0.0
        if self.deficit is not None:
            tt = self.tank_temp_c
            loss = self._ua() * (tt - s.room_temp_c) / 1000 * dt_h if tt is not None else 0.0
            loss = max(0.0, loss)
            self.deficit += loss
            if self._seg is not None:
                self._seg.loss += loss
        if p_kw <= 0 or self._burst is None:
            return
        b = self._burst
        heat = p_kw * dt_h
        b.heat += heat
        if self._seg is not None:
            self._seg.heat += heat
        if self.deficit is not None:
            self.deficit = max(0.0, self.deficit - heat)
        self._heat_w_ewma = self.power_w if self._heat_w_ewma is None else 0.98 * self._heat_w_ewma + 0.02 * self.power_w
        ln.element_kw = self._heat_w_ewma / 1000
        if self.deficit is None or self._seg is None:
            return
        # guaranteed minimum water energy drawn during this reheat
        lb = max(0.0, b.heat - b.d0 - BAND_MARGIN_KWH)
        b.lb = max(b.lb, lb)
        if not b.shower_live and b.lb >= s.shower_threshold_kwh:
            b.shower_live = True
        if b.shower_live and b.lb > b.booked:
            self._book_water("shower", b.lb - b.booked)
            self._seg.booked += b.lb - b.booked
            b.booked = b.lb
        # best guess for tank state: assume an ongoing shower once a draw is evident
        elapsed_min = (t - b.start) / 60 + dt_h * 60
        if b.early or b.heat > b.d0 + BAND_MARGIN_KWH:
            mins = max(b.lb / s.shower_kw * 60, min(elapsed_min, ln.typical_shower_min))
            best = mins / 60 * s.shower_kw
            if best > b.best:
                self.deficit += best - b.best
                b.best = best

    def _start_burst(self, t: float) -> None:
        ln = self.learned
        after_on = self.on_since is not None and t - self.on_since <= 120
        d = self.deficit if self.deficit is not None else 0.0
        early = (not after_on) and self.deficit is not None and d < ln.band_kwh * 0.8
        d0 = d if after_on else max(d, ln.band_kwh)
        if not after_on and self.deficit is not None and d < ln.band_kwh:
            # the thermostat fired: the tank is one band down whatever we predicted
            self.deficit = ln.band_kwh
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
        if not anchor:
            # reheat cut short by the switch: credit this reheat's own draw now
            if b.early or b.after_on or b.shower_live:
                u = max(0.0, b.heat - b.d0)
                if u >= NOISE_KWH:
                    kind = self._classify(u, t_end, b)
                    if kind == "shower":
                        seg.cut_shower_kwh += u
                    extra = max(0.0, u - b.booked)
                    self._book_water(kind, extra)
                    seg.booked += extra
                    seg.classified += u
                    self.deficit = max(0.0, self.deficit - b.best + u)
            return
        s, ln = self.settings, self.learned
        u = seg.heat - seg.loss - seg.classified
        live_unclassified = seg.booked - seg.classified
        quiet = (seg.bursts == 1 and not seg.was_off and b.heat < 0.3
                 and not b.early and t_end - seg.start >= QUIET_MIN_S)
        follow = (self._prev_big is not None and t_end - self._prev_big["end"] < FOLLOWUP_S + 3600
                  and b.start - self._prev_big["end"] < FOLLOWUP_S and b.heat < 0.3 and not seg.was_off)
        if follow and self._prev_big["kind"] != "shower":
            # tank was not fully mixed after a reheat from cold: this is heat loss
            self.last_correction_l = 0.0
            self._prev_big = None
        elif follow:
            extra = max(0.0, u - live_unclassified)
            self._book_water("shower", extra)
            add_l = extra / s.kwh_per_litre
            if self.last_shower_l is not None:
                self.last_shower_l += add_l
                self.last_shower_min = (self.last_shower_min or 0) + extra / s.shower_kw * 60
            self.last_correction_l = add_l
            self._prev_big = None
        else:
            uu = max(0.0, u)
            if seg.cut_shower_kwh > 0 and uu > 0:
                # the heat still missing from a cut-short shower shows up in this reheat
                room = max(0.0, ln.typical_shower_min / 60 * s.shower_kw - seg.cut_shower_kwh)
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
            self.last_correction_l = (uu - live_unclassified) / s.kwh_per_litre
            self._prev_big = ({"end": t_end, "kind": kind}
                              if (kind == "shower" or b.heat > 0.3) else None)
        if quiet and not follow:
            self._learn_quiet(b.heat, (t_end - seg.start) / 3600)
        # the heater has now replaced everything the tank lost: whatever this segment's
        # metered heat did not put into water is heat loss, so the parts add up to the meter
        loss = seg.heat - seg.water_booked - seg.loss_prebooked
        if loss < 0:
            self._loss_debt += -loss
        else:
            self._book_loss(loss)
        self.deficit = 0.0
        self._seg = _Segment(start=t_end)
        self._mark_full()

    def _classify(self, u: float, t: float, b: _Burst) -> str | None:
        s, ln = self.settings, self.learned
        if b.shower_live or u >= s.shower_threshold_kwh:
            litres = u / s.kwh_per_litre
            minutes = u / s.shower_kw * 60
            self.totals.shower_count += 1
            self.last_shower_l, self.last_shower_min = litres, minutes
            ln.typical_shower_min = 0.8 * ln.typical_shower_min + 0.2 * minutes
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

    def _learn_quiet(self, band_heat: float, hours: float) -> None:
        s, ln = self.settings, self.learned
        c = s.capacity_kwh_per_k
        avg_temp = s.thermostat_c - band_heat / c / 2
        w = band_heat / hours * 1000
        w_full = w * (s.thermostat_c - s.room_temp_c) / max(1.0, avg_temp - s.room_temp_c)
        a = 0.3 if ln.quiet_cycles < 5 else 0.1
        ln.loss_w_full = (1 - a) * ln.loss_w_full + a * w_full
        ln.band_kwh = (1 - a) * ln.band_kwh + a * band_heat
        ln.quiet_cycles += 1

    def _mark_full(self) -> None:
        if self.switch_on and self.on_since is not None:
            self.full_since_on = True

    def _check_full_without_reheat(self) -> None:
        """Switched on with the tank still full: no reheat comes, so it is ready."""
        if (self.switch_on and not self.full_since_on and self.on_since is not None
                and self._burst is None and self.deficit is not None
                and self.t is not None and self.t - self.on_since >= 300
                and self.deficit < self.learned.band_kwh):
            self.full_since_on = True

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "on_shower"}
        for k in ("settings", "learned", "totals"):
            d[k] = asdict(d[k])
        d["_burst"] = asdict(self._burst) if self._burst else None
        d["_seg"] = asdict(self._seg) if self._seg else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HeaterModel":
        d = dict(d)
        m = cls(settings=Settings(**d.pop("settings")), learned=Learned(**d.pop("learned")),
                totals=Totals(**d.pop("totals")))
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
