"""Settings you can change on the device page."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeaterEntity


@dataclass(frozen=True, kw_only=True)
class SettingDescription(NumberEntityDescription):
    attr: str


NUMBERS = (
    SettingDescription(key="thermostat_setting", attr="thermostat_c", device_class=NumberDeviceClass.TEMPERATURE,
                       native_unit_of_measurement=UnitOfTemperature.CELSIUS, native_min_value=40, native_max_value=80,
                       native_step=0.5),
    SettingDescription(key="shower_flow", attr="shower_flow_lpm", native_unit_of_measurement="L/min",
                       native_min_value=2, native_max_value=25, native_step=0.1),
    SettingDescription(key="shower_temperature", attr="shower_temp_c", device_class=NumberDeviceClass.TEMPERATURE,
                       native_unit_of_measurement=UnitOfTemperature.CELSIUS, native_min_value=30, native_max_value=50,
                       native_step=0.5),
    SettingDescription(key="cold_water_temperature", attr="cold_temp_c", device_class=NumberDeviceClass.TEMPERATURE,
                       native_unit_of_measurement=UnitOfTemperature.CELSIUS, native_min_value=5, native_max_value=40,
                       native_step=0.5),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities(SettingNumber(rt, d) for d in NUMBERS)


class SettingNumber(HeaterEntity, NumberEntity):
    entity_description: SettingDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, runtime, description: SettingDescription) -> None:
        super().__init__(runtime, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        return getattr(self.runtime.model.settings, self.entity_description.attr)

    async def async_set_native_value(self, value: float) -> None:
        self.runtime.set_setting(self.entity_description.attr, value)
