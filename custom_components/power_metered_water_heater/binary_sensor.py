"""Hot Water Ready."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HeaterEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([HotWaterReady(entry.runtime_data, "hot_water_ready")])


class HotWaterReady(HeaterEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return self.runtime.model.hot_water_ready
