"""Setup: step 1 picks the switch and its power sensor, step 2 the starting settings."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import device as device_helper, entity_registry as er, selector

from .const import (
    CONF_COLD_TEMP, CONF_IMPORT_STATISTICS, CONF_LEARN_HISTORY, CONF_POWER, CONF_ROOM_TEMP,
    CONF_SHOWER_FLOW, CONF_SHOWER_TEMP, CONF_SHOWER_THRESHOLD, CONF_SWITCH, CONF_TANK_VOLUME,
    CONF_THERMOSTAT, DEFAULT_COLD_TEMP, DEFAULT_ROOM_TEMP, DEFAULT_SHOWER_FLOW, DEFAULT_SHOWER_TEMP,
    DEFAULT_SHOWER_THRESHOLD, DEFAULT_TANK_VOLUME, DEFAULT_THERMOSTAT, DOMAIN,
)


def _num(lo: float, hi: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX))


def _power_sel() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER))


SOURCES = vol.Schema({
    vol.Required(CONF_SWITCH): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
    vol.Required(CONF_POWER): _power_sel(),
    vol.Required(CONF_TANK_VOLUME, default=DEFAULT_TANK_VOLUME): _num(5, 500, 1, "L"),
})

SETTINGS = vol.Schema({
    vol.Required(CONF_THERMOSTAT, default=DEFAULT_THERMOSTAT): _num(40, 80, 0.5, "°C"),
    vol.Required(CONF_SHOWER_FLOW, default=DEFAULT_SHOWER_FLOW): _num(2, 25, 0.1, "L/min"),
    vol.Required(CONF_SHOWER_TEMP, default=DEFAULT_SHOWER_TEMP): _num(30, 50, 0.5, "°C"),
    vol.Required(CONF_COLD_TEMP, default=DEFAULT_COLD_TEMP): _num(5, 40, 0.5, "°C"),
    vol.Required(CONF_LEARN_HISTORY, default=True): selector.BooleanSelector(),
    vol.Required(CONF_IMPORT_STATISTICS, default=True): selector.BooleanSelector(),
})


class PowerMeteredWaterHeaterFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._sources: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            switch = user_input[CONF_SWITCH]
            entry = er.async_get(self.hass).async_get(switch)
            await self.async_set_unique_id(entry.id if entry else switch)
            self._abort_if_unique_id_configured()
            self._sources = user_input
            # the new device is named after the switch's device (or the switch itself)
            device = device_helper.async_entity_id_to_device(self.hass, switch)
            state = self.hass.states.get(switch)
            self._title = ((device and (device.name_by_user or device.name))
                           or (state and state.name) or switch)
            return await self.async_step_settings()
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(SOURCES, user_input))

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self._title, data={**self._sources, **user_input})
        return self.async_show_form(step_id="settings", data_schema=SETTINGS,
                                    description_placeholders={"device": self._title})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PowerMeteredWaterHeaterOptions()


class PowerMeteredWaterHeaterOptions(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_POWER): _power_sel(),
                    vol.Required(CONF_TANK_VOLUME): _num(5, 500, 1, "L"),
            vol.Required(CONF_SHOWER_THRESHOLD, default=DEFAULT_SHOWER_THRESHOLD): _num(0.1, 2, 0.05, "kWh"),
            vol.Required(CONF_ROOM_TEMP, default=DEFAULT_ROOM_TEMP): _num(5, 45, 0.5, "°C"),
        })
        return self.async_show_form(step_id="init", data_schema=self.add_suggested_values_to_schema(schema, cur))
