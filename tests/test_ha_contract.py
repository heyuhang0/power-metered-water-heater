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
sys.path.insert(0, str(ROOT / "tests"))

from custom_components.power_metered_water_heater import (  # noqa: E402
    binary_sensor, config_flow, event, sensor, water_heater,
)
from custom_components.power_metered_water_heater.model import HeaterModel, replay  # noqa: E402
from synth import true_settings, week  # noqa: E402


def _runtime(showers=True):
    h = week()
    m = HeaterModel(settings=true_settings(h, showers))
    replay(m, h.events, until=h.t)
    return SimpleNamespace(model=m, entry=SimpleNamespace(entry_id="E", title="Water Heater"), device=None, hass=None,
                           switch_id="switch.x", switch_available=True, async_add_listener=lambda cb: (lambda: None),
                           async_add_shower_listener=lambda cb: (lambda: None))


@pytest.mark.parametrize("showers", [True, False])
def test_every_entity_builds_and_reports_state(showers):
    rt = _runtime(showers)
    descs = [d for d in sensor.SENSORS if showers or not d.showers]
    ents = [sensor.HeaterSensor(rt, d) for d in descs]
    ents += [binary_sensor.HotWaterReady(rt, "hot_water_ready"), water_heater.PoweredWaterHeater(rt, "water_heater")]
    if showers:
        ents.append(event.ShowerEvent(rt, "shower"))
    for e in ents:
        e.hass = None
        assert e.unique_id.startswith("E_")
        if isinstance(e, sensor.HeaterSensor):
            e.native_value
    assert {e.unique_id[2:] for e in ents} == sensor.current_keys(showers)
    wh = next(e for e in ents if isinstance(e, water_heater.PoweredWaterHeater))
    assert wh.current_operation in wh.operation_list
    assert wh._tank_temp() is not None and wh._target_temp() == rt.model.settings.thermostat_c
    if hasattr(water_heater.WaterHeaterEntity, "native_current_temperature"):
        assert "native_current_temperature" in vars(water_heater.PoweredWaterHeater)
        assert "current_temperature" not in vars(water_heater.PoweredWaterHeater)


def test_translations_cover_every_entity_and_match():
    strings = json.loads((COMP / "strings.json").read_text())
    assert strings == json.loads((COMP / "translations" / "en.json").read_text())
    ent = strings["entity"]
    for d in sensor.SENSORS:
        assert d.key in ent["sensor"]
    assert "hot_water_ready" in ent["binary_sensor"] and "shower" in ent["event"]


def test_every_setup_and_configure_field_is_translated():
    s = json.loads((COMP / "strings.json").read_text())
    steps = s["config"]["step"]
    for fields, step in ((config_flow.HEATER, "heater"), (config_flow.WATER, "water"), (config_flow.SHOWERS, "showers"),
                         (config_flow._switch_fields(), "user")):
        for k in fields:
            assert str(k) in steps[step]["data"], (step, k)
            assert str(k) in s["options"]["step"]["init"]["data"], k


def test_manifest():
    m = json.loads((COMP / "manifest.json").read_text())
    assert m["domain"] == "power_metered_water_heater" and m["config_flow"] and "recorder" in m["dependencies"]
