"""Setup: switch, name, heater, water, showers (optional). Configure edits all but the name."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device as device_helper, entity_registry as er, selector

from .const import (
    CONF_BAND, CONF_COLD_TEMP, CONF_FROM_HISTORY, CONF_HEAT_LOSS, CONF_NAME, CONF_POWER, CONF_ROOM_TEMP,
    CONF_SHORTEST_SHOWER, CONF_SHOWER_FLOW, CONF_SWITCH, CONF_TANK_VOLUME, CONF_THERMOSTAT,
    CONF_WARM_TEMP, DOMAIN, HARDWARE_SUFFIXES,
)


def _num(lo: float, hi: float, step: float, unit: str) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX))


def _switch_fields() -> dict:
    return {
        vol.Required(CONF_SWITCH): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
        vol.Required(CONF_POWER): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.POWER)),
    }


HEATER = {
    vol.Required(CONF_TANK_VOLUME): _num(5, 500, 1, "L"),
    vol.Required(CONF_THERMOSTAT): _num(40, 90, 0.5, "°C"),
    vol.Required(CONF_BAND): _num(0.02, 3, 0.001, "kWh"),
    vol.Required(CONF_HEAT_LOSS): _num(0.1, 20, 0.01, "W/°C"),
    vol.Required(CONF_ROOM_TEMP): _num(0, 45, 0.5, "°C"),
}
WATER = {
    vol.Required(CONF_COLD_TEMP): _num(0, 40, 0.5, "°C"),
    vol.Required(CONF_WARM_TEMP): _num(25, 60, 0.5, "°C"),
}
SHOWERS = {
    vol.Optional(CONF_SHOWER_FLOW): _num(1, 30, 0.1, "L/min"),
    vol.Optional(CONF_SHORTEST_SHOWER): _num(1, 30, 0.5, "min"),
}


def default_name(hass: HomeAssistant, switch: str) -> str:
    """The switch's device name (or the switch's name) without a trailing hardware word."""
    device = device_helper.async_entity_id_to_device(hass, switch)
    state = hass.states.get(switch)
    name = (device and (device.name_by_user or device.name)) or (state and state.name) or switch
    for suffix in HARDWARE_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _showers_valid(data: dict[str, Any]) -> bool:
    """Both shower fields, or neither."""
    return bool(data.get(CONF_SHOWER_FLOW)) == bool(data.get(CONF_SHORTEST_SHOWER))


class PowerMeteredWaterHeaterFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 3

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            switch = user_input[CONF_SWITCH]
            entry = er.async_get(self.hass).async_get(switch)
            await self.async_set_unique_id(entry.id if entry else switch)
            self._abort_if_unique_id_configured()
            self._data.update(user_input)
            self._title = default_name(self.hass, switch)
            return await self.async_step_name()
        return self.async_show_form(step_id="user", data_schema=vol.Schema(_switch_fields()))

    async def async_step_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._title = user_input[CONF_NAME].strip() or self._title
            return await self.async_step_heater()
        return self.async_show_form(step_id="name", data_schema=vol.Schema({
            vol.Required(CONF_NAME, default=self._title): selector.TextSelector()}))

    async def async_step_heater(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_water()
        return self.async_show_form(step_id="heater", data_schema=vol.Schema(HEATER))

    async def async_step_water(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_showers()
        return self.async_show_form(step_id="water", data_schema=vol.Schema({
            **WATER, vol.Required(CONF_FROM_HISTORY, default=True): selector.BooleanSelector()}))

    async def async_step_showers(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if _showers_valid(user_input):
                return self.async_create_entry(title=self._title, data={**self._data, **user_input})
            errors["base"] = "showers_incomplete"
        return self.async_show_form(step_id="showers", data_schema=vol.Schema(SHOWERS), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PowerMeteredWaterHeaterOptions()


class PowerMeteredWaterHeaterOptions(OptionsFlow):
    """All settings except the name, in one form. Saving reloads the helper."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if _showers_valid(user_input):
                return self.async_create_entry(data=user_input)
            errors["base"] = "showers_incomplete"
        schema = vol.Schema({**_switch_fields(), **HEATER, **WATER, **SHOWERS})
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(schema, user_input or current))
