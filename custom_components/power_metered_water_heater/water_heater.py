"""The water heater: controls the plug's switch, shows the estimated tank temperature."""
from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ELECTRIC, WaterHeaterEntity, WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeaterEntity

# HA 2026.11 renamed the temperature members to native_*; keep 2026.10 working.
_NATIVE = hasattr(WaterHeaterEntity, "native_current_temperature")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([PoweredWaterHeater(entry.runtime_data, "water_heater")])


class PoweredWaterHeater(HeaterEntity, WaterHeaterEntity):
    _attr_name = None                       # the device's own name
    _attr_translation_key = None
    _attr_supported_features = WaterHeaterEntityFeature.ON_OFF | WaterHeaterEntityFeature.OPERATION_MODE
    _attr_operation_list = [STATE_OFF, STATE_ELECTRIC]
    _attr_precision = 0.1
    if _NATIVE:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, runtime, key: str) -> None:
        super().__init__(runtime, key)
        self._attr_translation_key = None

    @property
    def available(self) -> bool:
        return self.runtime.switch_available

    @property
    def current_operation(self) -> str:
        return STATE_ELECTRIC if self.runtime.model.switch_on else STATE_OFF

    def _tank_temp(self) -> float | None:
        t = self.runtime.model.tank_temp_c
        return None if t is None else round(t, 1)

    def _target_temp(self) -> float:
        return self.runtime.model.settings.thermostat_c

    if _NATIVE:
        @property
        def native_current_temperature(self) -> float | None:
            return self._tank_temp()

        @property
        def native_target_temperature(self) -> float:
            return self._target_temp()
    else:
        @property
        def current_temperature(self) -> float | None:
            return self._tank_temp()

        @property
        def target_temperature(self) -> float:
            return self._target_temp()

    @property
    def min_temp(self) -> float:
        return self.runtime.model.settings.cold_temp_c

    @property
    def max_temp(self) -> float:
        return self.runtime.model.settings.thermostat_c

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self.runtime.model
        return {"heating": m.heating, "hot_water_ready": m.hot_water_ready}

    async def _switch(self, on: bool) -> None:
        await self.hass.services.async_call(
            "switch", "turn_on" if on else "turn_off", {ATTR_ENTITY_ID: self.runtime.switch_id}, blocking=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._switch(False)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self._switch(operation_mode != STATE_OFF)
