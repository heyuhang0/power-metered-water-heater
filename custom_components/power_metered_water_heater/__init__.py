"""Power Metered Water Heater: a storage water heater on a power-metering switch or smart plug."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes

from .const import CONF_POWER, CONF_SWITCH, LEGACY_CONF_ENERGY
from .runtime import HeaterRuntime

PLATFORMS = [Platform.WATER_HEATER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.NUMBER]

type HeaterConfigEntry = ConfigEntry[HeaterRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    _remove_retired_entities(hass, entry)
    runtime = HeaterRuntime(hass, entry)
    await runtime.async_start()
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_import_statistics()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _track_source_renames(hass, entry)
    return True


def _remove_retired_entities(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    """Remove registry entries for entities this version no longer provides.

    Otherwise they linger as "no longer provided", and deleting one in the UI
    deletes the whole helper.
    """
    from .number import NUMBERS
    from .sensor import SENSORS

    keys = {d.key for d in SENSORS} | {d.key for d in NUMBERS} | {"hot_water_ready", "shower", "water_heater"}
    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id.startswith(prefix) and ent.unique_id[len(prefix):] not in keys:
            reg.async_remove(ent.entity_id)


async def async_remove_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    """Delete the saved model state with the helper."""
    from homeassistant.helpers.storage import Store

    from .const import DOMAIN
    from .runtime import STORE_VERSION

    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


def _track_source_renames(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    """Follow the switch and power sensor if their entity IDs change."""
    cfg = {**entry.data, **entry.options}
    for key in (CONF_SWITCH, CONF_POWER):
        source = cfg[key]

        @callback
        def _set(new_id: str, key: str = key) -> None:
            data = {**entry.data, key: new_id} if key in entry.data else dict(entry.data)
            options = {**entry.options, key: new_id} if key in entry.options else dict(entry.options)
            hass.config_entries.async_update_entry(entry, data=data, options=options)

        entry.async_on_unload(async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=_set,
            source_device_id=async_entity_id_to_device_id(hass, source),
            source_entity_id_or_uuid=source,
        ))


async def async_migrate_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    """v1 -> v2: drop the unused energy sensor setting."""
    if entry.version == 1:
        data = {k: v for k, v in entry.data.items() if k != LEGACY_CONF_ENERGY}
        options = {k: v for k, v in entry.options.items() if k != LEGACY_CONF_ENERGY}
        hass.config_entries.async_update_entry(entry, data=data, options=options, version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _async_options_updated(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
