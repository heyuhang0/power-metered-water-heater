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

EXPECTED = [
    "water_heater.master_bath_water_heater",
    "binary_sensor.master_bath_water_heater_hot_water_ready",
    "event.master_bath_water_heater_shower",
    "sensor.master_bath_water_heater_tank_temperature",
    "sensor.master_bath_water_heater_hot_water_remaining",
    "sensor.master_bath_water_heater_shower_minutes_left",
    "sensor.master_bath_water_heater_total_water",
    "sensor.master_bath_water_heater_shower_water",
    "sensor.master_bath_water_heater_other_water",
    "sensor.master_bath_water_heater_shower_energy",
    "sensor.master_bath_water_heater_other_energy",
    "sensor.master_bath_water_heater_heat_loss_energy",
    "sensor.master_bath_water_heater_shower_count",
    "sensor.master_bath_water_heater_last_shower_water",
    "sensor.master_bath_water_heater_last_shower_duration",
    "sensor.master_bath_water_heater_heat_loss_rate",
    "sensor.master_bath_water_heater_last_correction",
    "number.master_bath_water_heater_thermostat_setting",
    "number.master_bath_water_heater_shower_flow",
    "number.master_bath_water_heater_shower_temperature",
    "number.master_bath_water_heater_cold_water_temperature",
]


async def _plug(hass: HomeAssistant) -> None:
    src = MockConfigEntry(domain="mqtt", title="plug")
    src.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=src.entry_id, identifiers={("mqtt", "zigbee2mqtt_0x01")}, name="Master Bath Water Heater")
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


async def _setup(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "switch_entity": SWITCH, "power_entity": POWER, "tank_volume": 35})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "settings"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "thermostat": 66, "shower_flow": 9.5, "shower_temperature": 38, "cold_water_temperature": 30,
        "learn_from_history": True, "import_statistics": True})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "Master Bath Water Heater"
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
    # all attached to the plug's device
    dev_ids = {e.device_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert dev_ids == {reg.async_get(SWITCH).device_id}

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
async def test_settings_number_changes_model(recorder_mock, our_integration, hass: HomeAssistant, freezer) -> None:
    await _plug(hass)
    await _record_week(hass, freezer)
    entry = await _setup(hass)
    await hass.services.async_call("number", "set_value", {
        "entity_id": "number.master_bath_water_heater_thermostat_setting", "value": 70}, blocking=True)
    assert entry.runtime_data.model.settings.thermostat_c == 70
    assert hass.states.get("water_heater.master_bath_water_heater").attributes["temperature"] == 70


@pytest.mark.parametrize("recorder_config", [{"commit_interval": 0}])
async def test_rejects_switch_without_device(recorder_mock, our_integration, hass: HomeAssistant) -> None:
    hass.states.async_set("switch.loose", STATE_OFF)
    hass.states.async_set(POWER, "0")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {
        "switch_entity": "switch.loose", "power_entity": POWER, "tank_volume": 35})
    assert r["errors"] == {"switch_entity": "no_device"}
