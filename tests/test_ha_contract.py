"""Contract against the installed Home Assistant (CI: the latest release).

Imports every platform, builds every entity with a fake runtime and reads the
properties HA reads, and checks translations. No HA instance is started.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "power_metered_water_heater"
sys.path.insert(0, str(ROOT))

from custom_components.power_metered_water_heater import (  # noqa: E402
    binary_sensor, config_flow, event, number, sensor, water_heater,
)
from custom_components.power_metered_water_heater.model import HeaterModel, replay  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from synth import week  # noqa: E402


def _runtime():
    m = HeaterModel()
    h = week()
    replay(m, h.events, until=h.t)
    return SimpleNamespace(model=m, entry=SimpleNamespace(entry_id="E", title="Water Heater"), device=None, hass=None, switch_id="switch.x",
                           switch_available=True, async_add_listener=lambda cb: (lambda: None),
                           async_add_shower_listener=lambda cb: (lambda: None), set_setting=lambda k, v: None)


def test_every_entity_builds_and_reports_state():
    rt = _runtime()
    ents = [sensor.HeaterSensor(rt, d) for d in sensor.SENSORS]
    ents += [binary_sensor.HotWaterReady(rt, "hot_water_ready"), event.ShowerEvent(rt, "shower"),
             water_heater.PoweredWaterHeater(rt, "water_heater")]
    ents += [number.SettingNumber(rt, d) for d in number.NUMBERS]
    for e in ents:
        e.hass = None
        assert e.unique_id.startswith("E_")
        if isinstance(e, sensor.HeaterSensor):
            e.native_value
        elif isinstance(e, number.SettingNumber):
            assert e.native_value > 0
    wh = ents[-1 - len(number.NUMBERS)]
    assert wh.current_operation in wh.operation_list
    assert wh._tank_temp() is not None and wh._target_temp() == rt.model.settings.thermostat_c
    # HA 2026.11+: the native_* members, so no deprecation warning is logged
    if hasattr(water_heater.WaterHeaterEntity, "native_current_temperature"):
        assert "native_current_temperature" in vars(water_heater.PoweredWaterHeater)
        assert "current_temperature" not in vars(water_heater.PoweredWaterHeater)


def test_translations_cover_every_entity_and_match():
    strings = json.loads((COMP / "strings.json").read_text())
    assert strings == json.loads((COMP / "translations" / "en.json").read_text())
    ent = strings["entity"]
    for d in sensor.SENSORS:
        assert d.key in ent["sensor"]
    for d in number.NUMBERS:
        assert d.key in ent["number"]
    assert "hot_water_ready" in ent["binary_sensor"] and "shower" in ent["event"]


def test_config_flow_fields_are_translated():
    s = json.loads((COMP / "strings.json").read_text())["config"]["step"]
    for schema, step in ((config_flow.SOURCES, "user"), (config_flow.SETTINGS, "settings")):
        for k in schema.schema:
            assert str(k) in s[step]["data"]


def test_manifest():
    m = json.loads((COMP / "manifest.json").read_text())
    assert m["domain"] == "power_metered_water_heater" and m["config_flow"] and "recorder" in m["dependencies"]
