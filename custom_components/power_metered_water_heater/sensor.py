"""Sensors: hot water left, water and energy totals, shower stats, diagnostics.

The tank temperature is the water heater entity's current temperature.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeaterEntity
from .model import HeaterModel


@dataclass(frozen=True, kw_only=True)
class HeaterSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HeaterModel], float | int | None]
    digits: int | None = None


L, KWH, MEAS, TOTAL_INC = UnitOfVolume.LITERS, UnitOfEnergy.KILO_WATT_HOUR, SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING

SENSORS: tuple[HeaterSensorDescription, ...] = (
    HeaterSensorDescription(key="hot_water_remaining", state_class=MEAS, native_unit_of_measurement=PERCENTAGE,
                            suggested_display_precision=0, value_fn=lambda m: m.hot_water_remaining_pct, digits=1),
    HeaterSensorDescription(key="shower_minutes_left", device_class=SensorDeviceClass.DURATION, state_class=MEAS,
                            native_unit_of_measurement=UnitOfTime.MINUTES, suggested_display_precision=0,
                            value_fn=lambda m: m.shower_minutes_left, digits=1),
    HeaterSensorDescription(key="total_water", device_class=SensorDeviceClass.WATER, state_class=TOTAL_INC,
                            native_unit_of_measurement=L, suggested_display_precision=0,
                            value_fn=lambda m: m.totals.total_l, digits=2),
    HeaterSensorDescription(key="shower_water", device_class=SensorDeviceClass.WATER, state_class=TOTAL_INC,
                            native_unit_of_measurement=L, suggested_display_precision=0,
                            value_fn=lambda m: m.totals.shower_l, digits=2),
    HeaterSensorDescription(key="other_water", device_class=SensorDeviceClass.WATER, state_class=TOTAL_INC,
                            native_unit_of_measurement=L, suggested_display_precision=0,
                            value_fn=lambda m: m.totals.other_l, digits=2),
    HeaterSensorDescription(key="shower_energy", device_class=SensorDeviceClass.ENERGY, state_class=TOTAL_INC,
                            native_unit_of_measurement=KWH, suggested_display_precision=2,
                            value_fn=lambda m: m.totals.shower_kwh, digits=4),
    HeaterSensorDescription(key="other_energy", device_class=SensorDeviceClass.ENERGY, state_class=TOTAL_INC,
                            native_unit_of_measurement=KWH, suggested_display_precision=2,
                            value_fn=lambda m: m.totals.other_kwh, digits=4),
    HeaterSensorDescription(key="heat_loss_energy", device_class=SensorDeviceClass.ENERGY, state_class=TOTAL_INC,
                            native_unit_of_measurement=KWH, suggested_display_precision=2,
                            value_fn=lambda m: m.totals.loss_kwh, digits=4),
    HeaterSensorDescription(key="shower_count", state_class=TOTAL_INC, value_fn=lambda m: m.totals.shower_count),
    HeaterSensorDescription(key="last_shower_water", device_class=SensorDeviceClass.VOLUME,
                            native_unit_of_measurement=L, suggested_display_precision=0,
                            value_fn=lambda m: m.last_shower_l, digits=1),
    HeaterSensorDescription(key="last_shower_duration", device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.MINUTES, suggested_display_precision=1,
                            value_fn=lambda m: m.last_shower_min, digits=1),
    HeaterSensorDescription(key="heat_loss_rate", device_class=SensorDeviceClass.POWER, state_class=MEAS,
                            native_unit_of_measurement=UnitOfPower.WATT, suggested_display_precision=0,
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_visible_default=False,
                            value_fn=lambda m: m.heat_loss_w, digits=1),
    HeaterSensorDescription(key="last_correction", device_class=SensorDeviceClass.VOLUME,
                            native_unit_of_measurement=L, suggested_display_precision=1,
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_visible_default=False,
                            value_fn=lambda m: m.last_correction_l, digits=1),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities(HeaterSensor(rt, d) for d in SENSORS)


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

    @property
    def extra_state_attributes(self):
        if self.entity_description.key == "last_shower_duration":
            return {"assumed_flow_l_min": self.runtime.model.settings.shower_flow_lpm}
        return None
