"""Base entity: attached to the plug's existing device, updated by the runtime."""
from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .runtime import HeaterRuntime


class HeaterEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, runtime: HeaterRuntime, key: str) -> None:
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self.device_entry = runtime.device

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.runtime.async_add_listener(self.async_write_ha_state))
