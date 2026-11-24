"""Glue between the pure model and Home Assistant: inputs, persistence, catch-up, statistics."""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from homeassistant.components.recorder import get_instance, history
from homeassistant.components.recorder.db_schema import StatisticsShortTerm
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics, get_last_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import device as device_helper, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BAND, CONF_COLD_TEMP, CONF_FROM_HISTORY, CONF_HEAT_LOSS, CONF_POWER, CONF_ROOM_TEMP,
    CONF_SHORTEST_SHOWER, CONF_SHOWER_FLOW, CONF_SWITCH, CONF_TANK_VOLUME, CONF_THERMOSTAT,
    CONF_WARM_TEMP, DOMAIN, HISTORY_DAYS, SAVE_DELAY_S, TICK_S,
)
from .model import HeaterModel, Settings, replay

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
BAD = (None, STATE_UNKNOWN, STATE_UNAVAILABLE)

# sensor key -> (unit, unit_class, value as the sensor reports it)
STAT_TOTALS: dict[str, tuple[str | None, str | None, Callable[[HeaterModel], float]]] = {
    "energy": ("kWh", "energy", lambda m: round(m.totals.energy_kwh, 4)),
    "water_energy": ("kWh", "energy", lambda m: round(m.totals.water_kwh, 4)),
    "total_water": ("L", "volume", lambda m: round(m.totals.total_l, 2)),
    "shower_water": ("L", "volume", lambda m: round(m.totals.shower_l, 2)),
    "other_water": ("L", "volume", lambda m: round(m.totals.other_l, 2)),
    "shower_energy": ("kWh", "energy", lambda m: round(m.totals.shower_kwh, 4)),
    "other_energy": ("kWh", "energy", lambda m: round(m.totals.other_kwh, 4)),
    "heat_loss_energy": ("kWh", "energy", lambda m: round(m.totals.loss_kwh, 4)),
    "shower_count": (None, None, lambda m: float(m.totals.shower_count)),
}


SHOWER_STATS = ("shower_water", "other_water", "shower_energy", "other_energy", "shower_count")


def entry_config(entry: ConfigEntry) -> dict[str, Any]:
    """The current configuration: Configure (options) replaces setup's values as a whole."""
    return {**entry.data, **entry.options} if not entry.options else {
        CONF_FROM_HISTORY: entry.data.get(CONF_FROM_HISTORY, True), **entry.options}


def settings_from_entry(entry: ConfigEntry) -> Settings:
    c = entry_config(entry)
    opt = lambda k: float(c[k]) if c.get(k) else None  # noqa: E731
    return Settings(
        tank_volume_l=float(c[CONF_TANK_VOLUME]), thermostat_c=float(c[CONF_THERMOSTAT]),
        band_kwh=float(c[CONF_BAND]), heat_loss_w_per_k=float(c[CONF_HEAT_LOSS]),
        room_temp_c=float(c[CONF_ROOM_TEMP]), cold_temp_c=float(c[CONF_COLD_TEMP]),
        warm_temp_c=float(c[CONF_WARM_TEMP]),
        shower_flow_lpm=opt(CONF_SHOWER_FLOW), shortest_shower_min=opt(CONF_SHORTEST_SHOWER),
    )


def _events_from_states(states: dict[str, list[State]], switch_id: str, power_id: str) -> list[tuple[float, str, Any]]:
    ev: list[tuple[float, str, Any]] = []
    for st in states.get(switch_id, []):
        if st.state not in BAD:
            ev.append((st.last_changed.timestamp(), "switch", st.state == STATE_ON))
    for st in states.get(power_id, []):
        if st.state in BAD:
            continue
        try:
            ev.append((st.last_changed.timestamp(), "power", float(st.state)))
        except ValueError:
            continue
    ev.sort(key=lambda e: e[0])
    return ev


class HeaterRuntime:
    """Owns the model for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = entry_config(entry)
        self.switch_id: str = cfg[CONF_SWITCH]
        self.power_id: str = cfg[CONF_POWER]
        self.device = device_helper.async_entity_id_to_device(hass, self.switch_id)
        self.model = HeaterModel(settings=settings_from_entry(entry))
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._listeners: list[CALLBACK_TYPE] = []
        self._shower_listeners: list[Callable[[float, float, float], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._save_pending = False
        self._buffer: list[tuple[float, str, Any]] | None = []
        self._snapshots: list[tuple[float, dict[str, float]]] = []
        self.first_setup = False
        self.stats_imported = False
        self._live = False

    # -------------------------------------------------------------- lifecycle
    async def async_start(self) -> None:
        stored = await self._store.async_load()
        settings = self.model.settings
        if stored and stored.get("model"):
            self.model = HeaterModel.from_dict(stored["model"], settings)
            self.stats_imported = bool(stored.get("stats_imported"))
            start = dt_util.utc_from_timestamp(self.model.t) if self.model.t else None
        else:
            self.first_setup = True
            replay_history = entry_config(self.entry).get(CONF_FROM_HISTORY, True)
            start = dt_util.utcnow() - dt.timedelta(days=HISTORY_DAYS) if replay_history else None
        self.model.on_shower.append(self._on_shower)
        # subscribe first, buffer live events while history is replayed
        self._unsubs.append(async_track_state_change_event(
            self.hass, [self.switch_id, self.power_id], self._on_state))
        now = dt_util.utcnow()
        if start is not None:
            await self._catch_up(start, now, snapshots=self.first_setup)
        else:
            self._seed_current_inputs(now)
        buffered, self._buffer = self._buffer or [], None
        self._live = True
        for e in buffered:
            if self.model.t is None or e[0] >= self.model.t:
                self._apply(e)
        self.model.advance(dt_util.utcnow().timestamp())
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, dt.timedelta(seconds=TICK_S)))
        self._schedule_save()

    async def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        await self._store.async_save(self._data())

    async def _catch_up(self, start: dt.datetime, end: dt.datetime, snapshots: bool) -> None:
        states = await get_instance(self.hass).async_add_executor_job(partial(
            history.get_significant_states, self.hass, start, end, [self.switch_id, self.power_id],
            None, True, False, False, True))
        ev = _events_from_states(states, self.switch_id, self.power_id)
        if self.model.t is not None:
            ev = [e for e in ev if e[0] > self.model.t]
        snap = None
        if snapshots:
            def snap(t: float, m: HeaterModel) -> None:
                self._snapshots.append((t, {k: f(m) for k, (_, _, f) in STAT_TOTALS.items()}))
        replay(self.model, ev, until=end.timestamp(), snapshot=snap)
        _LOGGER.debug("%s: replayed %d events since %s", self.entry.title, len(ev), start)

    def _seed_current_inputs(self, now: dt.datetime) -> None:
        t = now.timestamp()
        if (s := self.hass.states.get(self.switch_id)) and s.state not in BAD:
            self.model.set_switch(t, s.state == STATE_ON)
        if (p := self.hass.states.get(self.power_id)) and p.state not in BAD:
            try:
                self.model.set_power(t, float(p.state))
            except ValueError:
                pass

    # -------------------------------------------------------------- statistics
    async def async_import_statistics(self) -> None:
        """Backfill hourly statistics from the first-setup replay, once per entry."""
        if self.stats_imported or not self._snapshots:
            return
        if not entry_config(self.entry).get(CONF_FROM_HISTORY, True):
            return
        reg = er.async_get(self.hass)
        now = dt_util.utcnow().timestamp()
        # HA continues a sum from the latest 5-minute row, so leave one at the current value
        short_start = dt_util.utc_from_timestamp(now - now % 300 - 300)
        recorder = get_instance(self.hass)
        done = 0
        for key, (unit, unit_class, value_fn) in STAT_TOTALS.items():
            if key in SHOWER_STATS and not self.model.settings.showers_enabled:
                continue
            entity_id = reg.async_get_entity_id("sensor", DOMAIN, f"{self.entry.entry_id}_{key}")
            if entity_id is None:
                continue
            existing = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, entity_id, False, {"sum"})
            if existing.get(entity_id):
                continue                      # never overwrite statistics HA already has
            meta: StatisticMetaData = {
                "source": "recorder", "statistic_id": entity_id, "name": None,
                "unit_of_measurement": unit, "unit_class": unit_class,
                "mean_type": StatisticMeanType.NONE, "has_sum": True,
            }
            rows: list[StatisticData] = [
                {"start": dt_util.utc_from_timestamp(t - 3600), "state": v[key], "sum": v[key]}
                for t, v in self._snapshots if t <= now
            ]
            if rows:
                async_import_statistics(self.hass, meta, rows)
                current = value_fn(self.model)
                recorder.async_import_statistics(
                    meta, [{"start": short_start, "state": current, "sum": current}], StatisticsShortTerm)
                done += 1
        self.stats_imported = True
        self._snapshots.clear()
        self._schedule_save()
        _LOGGER.info("%s: imported hourly statistics for %d sensors", self.entry.title, done)

    # ------------------------------------------------------------------ inputs
    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None or new.state in BAD:
            return
        t = new.last_changed.timestamp()
        if new.entity_id == self.switch_id:
            e: tuple[float, str, Any] = (t, "switch", new.state == STATE_ON)
        else:
            try:
                e = (t, "power", float(new.state))
            except ValueError:
                return
        if self._buffer is not None:
            self._buffer.append(e)
            return
        self._apply(e)
        self._notify()

    def _apply(self, e: tuple[float, str, Any]) -> None:
        t = max(e[0], self.model.t or e[0])
        if e[1] == "switch":
            self.model.set_switch(t, e[2])
        else:
            self.model.set_power(t, e[2])

    @callback
    def _tick(self, now: dt.datetime) -> None:
        self.model.advance(now.timestamp())
        self._notify()
        self._schedule_save()

    @callback
    def _on_shower(self, t: float, litres: float, minutes: float) -> None:
        if not self._live:
            return                            # no events for showers found while replaying history
        for cb in self._shower_listeners:
            cb(t, litres, minutes)

    # ---------------------------------------------------------------- plumbing
    @callback
    def async_add_listener(self, cb: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def async_add_shower_listener(self, cb: Callable[[float, float, float], None]) -> CALLBACK_TYPE:
        self._shower_listeners.append(cb)
        return lambda: self._shower_listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _data(self) -> dict[str, Any]:
        self._save_pending = False
        return {"model": self.model.to_dict(), "stats_imported": self.stats_imported}

    @callback
    def _schedule_save(self) -> None:
        # async_delay_save restarts its timer on every call, and the tick calls this every
        # minute: only schedule when no save is pending, or it never fires.
        if not self._save_pending:
            self._save_pending = True
            self._store.async_delay_save(self._data, SAVE_DELAY_S)

    @property
    def switch_available(self) -> bool:
        st = self.hass.states.get(self.switch_id)
        return st is not None and st.state != STATE_UNAVAILABLE
