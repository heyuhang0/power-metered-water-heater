"""End-to-end against a real Home Assistant with an in-memory recorder."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from homeassistant.components.recorder.statistics import statistics_during_period  # noqa: E402
from homeassistant.const import STATE_OFF, STATE_ON  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry, async_fire_time_changed, async_mock_service,
)
from pytest_homeassistant_custom_component.components.recorder.common import (  # noqa: E402
    async_wait_recording_done,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth import week  # noqa: E402

DOMAIN = "power_metered_water_heater"
SWITCH, POWER = "switch.master_bath_water_heater", "sensor.master_bath_water_heater_power"
START = dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.timezone.utc)

P = "master_bath_water_heater"
ALWAYS = [f"water_heater.{P}", f"binary_sensor.{P}_hot_water_ready"] + [f"sensor.{P}_{k}" for k in (
    "hot_water_remaining", "energy", "water_energy", "heat_loss_energy", "total_water")]
SHOWERS = [f"event.{P}_shower"] + [f"sensor.{P}_{k}" for k in (
    "shower_water", "other_water", "shower_energy", "other_energy", "shower_count", "last_shower_water",
    "last_shower_duration", "shower_minutes_left")]
EXPECTED = ALWAYS + SHOWERS
HEATER = {"tank_volume": 35, "thermostat": 66, "thermostat_band": 0.244, "heat_loss": 1.8, "room_temperature": 30}
WATER = {"cold_water_temperature": 30, "warm_water_temperature": 38}
SHOWER = {"shower_flow": 9.5, "shortest_shower": 6}


async def _plug(hass: HomeAssistant) -> None:
    src = MockConfigEntry(domain="mqtt", title="plug")
    src.add_to_hass(hass)
    from homeassistant.helpers import area_registry as ar
    area = ar.async_get(hass).async_create("Master Bath")
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=src.entry_id, identifiers={("mqtt", "zigbee2mqtt_0x01")}, name="Water Heater Switch")
    dr.async_get(hass).async_update_device(dev.id, area_id=area.id)
    reg = er.async_get(hass)
    reg.async_get_or_create("switch", "mqtt", "sw", suggested_object_id="master_bath_water_heater",
                            config_entry=src, device_id=dev.id)
    reg.async_get_or_create("sensor", "mqtt", "pw", suggested_object_id="master_bath_water_heater_power",
                            config_entry=src, device_id=dev.id)


async def _record_week(hass: HomeAssistant, freezer) -> object:
    h = week()
    for t, kind, value in sorted(h.events, key=lambda e: e[0]):
        freezer.move_to(START + dt.timedelta(seconds=t))
        if kind == "switch":
            hass.states.async_set(SWITCH, STATE_ON if value else STATE_OFF)
        else:
            hass.states.async_set(POWER, str(value), {"unit_of_measurement": "W", "device_class": "power"})
        await hass.async_block_till_done()
    freezer.move_to(START + dt.timedelta(seconds=h.t + 1800))
    await async_wait_recording_done(hass)
    return h


async def _setup(hass: HomeAssistant, showers: bool = True, from_history: bool = True):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"switch_entity": SWITCH, "power_entity": POWER})
    assert r["step_id"] == "name"
    assert r["data_schema"]({})["name"] == "Water Heater"            # "Water Heater Switch" minus "Switch"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "Water Heater"})
    assert r["step_id"] == "heater"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], HEATER)
    assert r["step_id"] == "water"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**WATER, "from_history": from_history})
    assert r["step_id"] == "showers"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], SHOWER if showers else {})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Water Heater"
    await hass.async_block_till_done()
    return r["result"]


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_setup_replays_history_and_names_entities(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    h = await _record_week(hass, freezer)
    entry = await _setup(hass)

    reg = er.async_get(hass)
    ours = sorted(e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id))
    assert ours == sorted(EXPECTED)
    # all on the integration's own device, in the plug's area, connected via the plug
    dev_ids = {e.device_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert len(dev_ids) == 1
    plug_id = reg.async_get(SWITCH).device_id
    heater = dr.async_get(hass).async_get(dev_ids.pop())
    assert heater.id != plug_id and heater.via_device_id == plug_id
    assert heater.name == "Water Heater" and heater.area_id == "master_bath"

    truth = h.litres("shower") + h.litres("other")
    total = float(hass.states.get("sensor.master_bath_water_heater_total_water").state)
    assert total == pytest.approx(truth, rel=0.08)
    assert hass.states.get("sensor.master_bath_water_heater_shower_count").state == str(h.showers)
    wh = hass.states.get("water_heater.master_bath_water_heater")
    assert wh.state == STATE_OFF                 # synthetic week ends switched off
    assert wh.attributes["current_temperature"] is not None

    # hourly statistics backfilled for the replayed week
    await async_wait_recording_done(hass)
    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, START, None, {"sensor.master_bath_water_heater_total_water"},
        "hour", None, {"sum"})
    rows = stats["sensor.master_bath_water_heater_total_water"]
    assert len(rows) > 24 * 6
    assert rows[-1]["sum"] == pytest.approx(total, rel=0.02)
    sums = [r["sum"] for r in rows]
    assert sums == sorted(sums)


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_water_heater_controls_switch_and_live_updates(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    await _setup(hass)
    on_calls = async_mock_service(hass, "switch", "turn_on")
    await hass.services.async_call("water_heater", "turn_on",
                                   {"entity_id": "water_heater.master_bath_water_heater"}, blocking=True)
    assert on_calls and on_calls[0].data["entity_id"] == SWITCH

    # live: switch on, a reheat runs and finishes -> Hot Water Ready
    now = dt_util.utcnow()
    hass.states.async_set(SWITCH, STATE_ON)
    hass.states.async_set(POWER, "2950", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    freezer.move_to(now + dt.timedelta(minutes=20))
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    freezer.move_to(now + dt.timedelta(minutes=22))
    async_fire_time_changed(hass, now + dt.timedelta(minutes=22))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.master_bath_water_heater_hot_water_ready").state == STATE_ON
    assert hass.states.get("water_heater.master_bath_water_heater").state == "electric"


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_state_is_saved_while_running(recorder_mock, our_integration, hass: HomeAssistant, freezer, hass_storage) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    entry = await _setup(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage.pop(key, None)
    now = dt_util.utcnow()
    for minute in range(1, 8):                    # the 1-minute tick must not keep postponing the save
        freezer.move_to(now + dt.timedelta(minutes=minute))
        async_fire_time_changed(hass, now + dt.timedelta(minutes=minute))
        await hass.async_block_till_done()
    assert key in hass_storage
    assert hass_storage[key]["data"]["model"]["totals"]


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_configure_changes_settings_and_can_turn_showers_off(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    entry = await _setup(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["step_id"] == "init"
    # one shower field only: refused
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "switch_entity": SWITCH, "power_entity": POWER, **HEATER, "thermostat": 70, **WATER, "shower_flow": 9.5})
    assert r["errors"] == {"base": "showers_incomplete"}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {
        "switch_entity": SWITCH, "power_entity": POWER, **HEATER, "thermostat": 70, **WATER})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert rt.model.settings.thermostat_c == 70 and not rt.model.settings.showers_enabled
    assert hass.states.get(f"water_heater.{P}").attributes["temperature"] == 70
    ours = sorted(e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
    assert ours == sorted(ALWAYS)                      # shower entities removed, nothing left behind


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_switch_without_device_is_named_after_the_switch(recorder_mock, our_integration, hass: HomeAssistant) -> None:
    hass.states.async_set("switch.loose", STATE_OFF, {"friendly_name": "Loose Heater Plug"})
    hass.states.async_set(POWER, "0")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"switch_entity": "switch.loose", "power_entity": POWER})
    assert r["data_schema"]({})["name"] == "Loose Heater"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "Loose Heater"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], HEATER)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**WATER, "from_history": False})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.CREATE_ENTRY and r["title"] == "Loose Heater"
    await hass.async_block_till_done()
    assert hass.states.get("water_heater.loose_heater") is not None
    assert hass.states.get("event.loose_heater_shower") is None          # showers off


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_follows_switch_rename(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    entry = await _setup(hass)
    total_before = hass.states.get("sensor.master_bath_water_heater_total_water").state
    er.async_get(hass).async_update_entity(SWITCH, new_entity_id="switch.master_bath_water_heater_switch")
    await hass.async_block_till_done()
    assert entry.data["switch_entity"] == "switch.master_bath_water_heater_switch"
    assert entry.runtime_data.switch_id == "switch.master_bath_water_heater_switch"
    # model state survives the reload
    assert hass.states.get("sensor.master_bath_water_heater_total_water").state == total_before
    hass.states.async_set("switch.master_bath_water_heater_switch", STATE_ON)   # the plug reports under its new ID
    await hass.async_block_till_done()
    calls = async_mock_service(hass, "switch", "turn_off")
    await hass.services.async_call("water_heater", "turn_off",
                                   {"entity_id": "water_heater.master_bath_water_heater"}, blocking=True)
    assert calls[0].data["entity_id"] == "switch.master_bath_water_heater_switch"


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_migrates_old_entry_with_learned_values(recorder_mock, our_integration, hass: HomeAssistant, hass_storage) -> None:
    """A 0.1.x entry: number-entity values and learned values live in the saved model state."""
    await _plug(hass)
    old = MockConfigEntry(domain=DOMAIN, version=2, title="Water Heater", data={
        "switch_entity": SWITCH, "power_entity": POWER, "tank_volume": 35, "thermostat": 65, "shower_flow": 9.5,
        "shower_temperature": 38, "cold_water_temperature": 30, "learn_from_history": False, "import_statistics": True},
        options={"power_entity": POWER, "tank_volume": 35, "shower_threshold": 0.35, "room_temperature": 30})
    old.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{old.entry_id}"] = {"version": 1, "minor_version": 1, "key": f"{DOMAIN}.{old.entry_id}",
        "data": {"model": {"settings": {"thermostat_c": 70.0, "shower_flow_lpm": 9.0, "shower_temp_c": 39.0,
                                        "cold_temp_c": 29.0},
                           "learned": {"band_kwh": 0.242, "loss_w_full": 72.0}}}}
    assert await hass.config_entries.async_setup(old.entry_id)
    await hass.async_block_till_done()
    d = old.data
    assert old.version == 3 and old.options == {}
    assert d["thermostat"] == 70.0 and d["shower_flow"] == 9.0 and d["warm_water_temperature"] == 39.0
    assert d["cold_water_temperature"] == 29.0 and d["thermostat_band"] == 0.242
    assert d["heat_loss"] == pytest.approx(72.0 / 40, abs=0.01) and d["shortest_shower"] == 6.0
    for gone in ("shower_temperature", "learn_from_history", "import_statistics", "shower_threshold"):
        assert gone not in d


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_backfilled_sums_continue_after_live_statistics(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    """HA continues a sum from its latest 5-minute row; the backfill must leave one."""
    from homeassistant.components.recorder.statistics import statistics_during_period
    from pytest_homeassistant_custom_component.components.recorder.common import do_adhoc_statistics

    await _plug(hass)
    await _record_week(hass, freezer)
    await _setup(hass)
    await async_wait_recording_done(hass)
    sid = "sensor.master_bath_water_heater_total_water"
    total = float(hass.states.get(sid).state)
    now = dt_util.utcnow()
    nxt = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=5 - now.minute % 5)
    for k in range(13):                      # a full hour of 5-minute runs, including the hourly one
        start = nxt + dt.timedelta(minutes=5 * k)
        freezer.move_to(start + dt.timedelta(minutes=5, seconds=10))
        do_adhoc_statistics(hass, start=start)
        await async_wait_recording_done(hass)
    short = await hass.async_add_executor_job(
        statistics_during_period, hass, now - dt.timedelta(hours=1), None, {sid}, "5minute", None, {"sum"})
    assert short[sid][-1]["sum"] == pytest.approx(total, abs=0.5)
    hourly = await hass.async_add_executor_job(
        statistics_during_period, hass, START, None, {sid}, "hour", None, {"sum"})
    sums = [r["sum"] for r in hourly[sid]]
    assert sums == sorted(sums) and sums[-1] == pytest.approx(total, abs=0.5)



@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_retired_entities_are_removed(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    entry = await _setup(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("number", DOMAIN, f"{entry.entry_id}_thermostat_setting", config_entry=entry,
                            suggested_object_id="master_bath_water_heater_thermostat_setting")
    assert reg.async_get("number.master_bath_water_heater_thermostat_setting")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get("number.master_bath_water_heater_thermostat_setting") is None
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None
    assert reg.async_get("water_heater.master_bath_water_heater")
