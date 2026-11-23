"""Power Metered Water Heater: a storage water heater on a power-metering smart plug."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .runtime import HeaterRuntime

PLATFORMS = [Platform.WATER_HEATER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.NUMBER]

type HeaterConfigEntry = ConfigEntry[HeaterRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    runtime = HeaterRuntime(hass, entry)
    await runtime.async_start()
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_import_statistics()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _async_options_updated(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
