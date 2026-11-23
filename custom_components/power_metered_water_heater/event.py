"""Shower event: fired once per shower when its reheat ends."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_SHOWER
from .entity import HeaterEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ShowerEvent(entry.runtime_data, "shower")])


class ShowerEvent(HeaterEntity, EventEntity):
    _attr_event_types = [EVENT_SHOWER]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_shower_listener(self._shower))

    @callback
    def _shower(self, t: float, litres: float, minutes: float) -> None:
        self._trigger_event(EVENT_SHOWER, {"water": round(litres, 1), "duration": round(minutes, 1)})
        self.async_write_ha_state()
