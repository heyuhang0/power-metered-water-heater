"""Base entity: all entities sit on the integration's own device, linked to the plug."""
from __future__ import annotations

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .runtime import HeaterRuntime


def heater_device_info(runtime: HeaterRuntime) -> DeviceInfo:
    """The water heater device: named after the entry, in the plug's area, connected via the plug."""
    info: dict = {
        "identifiers": {(DOMAIN, runtime.entry.entry_id)},
        "name": runtime.entry.title,
        "manufacturer": "Power Metered Water Heater",
        "model": "Storage water heater",
    }
    plug = runtime.device
    if plug is not None:
        info["via_device_id"] = plug.id
        if plug.area_id and runtime.hass is not None and (area := ar.async_get(runtime.hass).async_get_area(plug.area_id)):
            info["suggested_area"] = area.name
    return DeviceInfo(**info)


class HeaterEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HeaterRuntime, key: str) -> None:
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = heater_device_info(runtime)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.runtime.async_add_listener(self.async_write_ha_state))
