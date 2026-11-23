"""Sensors: hot water left, energy and water totals, and (optional) shower stats.

The tank temperature is the water heater entity's current temperature.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeaterEntity
from .model import HeaterModel


@dataclass(frozen=True, kw_only=True)
class HeaterSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HeaterModel], float | int | None]
    digits: int | None = None
    showers: bool = False          # only with showers enabled


L, KWH, MEAS, TOTAL_INC = UnitOfVolume.LITERS, UnitOfEnergy.KILO_WATT_HOUR, SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING


def _total(key: str, unit: str, value_fn, showers: bool = False) -> HeaterSensorDescription:
    water = unit == L
    return HeaterSensorDescription(
        key=key, device_class=SensorDeviceClass.WATER if water else SensorDeviceClass.ENERGY,
        state_class=TOTAL_INC, native_unit_of_measurement=unit, suggested_display_precision=0 if water else 2,
        value_fn=value_fn, digits=2 if water else 4, showers=showers)


SENSORS: tuple[HeaterSensorDescription, ...] = (
    HeaterSensorDescription(key="hot_water_remaining", state_class=MEAS, native_unit_of_measurement=PERCENTAGE,
                            suggested_display_precision=0, value_fn=lambda m: m.hot_water_remaining_pct, digits=1),
    _total("energy", KWH, lambda m: m.totals.energy_kwh),
    _total("water_energy", KWH, lambda m: m.totals.water_kwh),
    _total("heat_loss_energy", KWH, lambda m: m.totals.loss_kwh),
    _total("total_water", L, lambda m: m.totals.total_l),
    # showers
    _total("shower_water", L, lambda m: m.totals.shower_l, showers=True),
    _total("other_water", L, lambda m: m.totals.other_l, showers=True),
    _total("shower_energy", KWH, lambda m: m.totals.shower_kwh, showers=True),
    _total("other_energy", KWH, lambda m: m.totals.other_kwh, showers=True),
    HeaterSensorDescription(key="shower_count", state_class=TOTAL_INC, showers=True,
                            value_fn=lambda m: m.totals.shower_count),
    HeaterSensorDescription(key="last_shower_water", device_class=SensorDeviceClass.VOLUME, showers=True,
                            native_unit_of_measurement=L, suggested_display_precision=0,
                            value_fn=lambda m: m.last_shower_l, digits=1),
    HeaterSensorDescription(key="last_shower_duration", device_class=SensorDeviceClass.DURATION, showers=True,
                            native_unit_of_measurement=UnitOfTime.MINUTES, suggested_display_precision=1,
                            value_fn=lambda m: m.last_shower_min, digits=1),
    HeaterSensorDescription(key="shower_minutes_left", device_class=SensorDeviceClass.DURATION, state_class=MEAS,
                            native_unit_of_measurement=UnitOfTime.MINUTES, suggested_display_precision=0,
                            showers=True, value_fn=lambda m: m.shower_minutes_left, digits=1),
)


def current_keys(showers: bool) -> set[str]:
    """Unique-id keys of every entity this version provides with these settings."""
    keys = {d.key for d in SENSORS if showers or not d.showers} | {"water_heater", "hot_water_ready"}
    return keys | {"shower"} if showers else keys


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    showers = rt.model.settings.showers_enabled
    async_add_entities(HeaterSensor(rt, d) for d in SENSORS if showers or not d.showers)


class HeaterSensor(HeaterEntity, SensorEntity):
    entity_description: HeaterSensorDescription

    def __init__(self, runtime, description: HeaterSensorDescription) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        v = self.entity_description.value_fn(self.runtime.model)
        if v is None or self.entity_description.digits is None:
            return v
        return round(v, self.entity_description.digits)

