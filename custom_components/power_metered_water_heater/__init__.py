"""Power Metered Water Heater: a storage water heater on a power-metering switch or smart plug."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes

from .const import (
    CONF_BAND, CONF_FROM_HISTORY, CONF_HEAT_LOSS, CONF_POWER, CONF_ROOM_TEMP, CONF_SHORTEST_SHOWER, CONF_SHOWER_FLOW,
    CONF_SWITCH, CONF_WARM_TEMP,
)
from .runtime import HeaterRuntime

PLATFORMS = [Platform.WATER_HEATER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT]

type HeaterConfigEntry = ConfigEntry[HeaterRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    runtime = HeaterRuntime(hass, entry)
    _remove_retired_entities(hass, entry, runtime.model.settings.showers_enabled)
    await runtime.async_start()
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_import_statistics()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _track_source_renames(hass, entry)
    return True


def _remove_retired_entities(hass: HomeAssistant, entry: HeaterConfigEntry, showers: bool) -> None:
    """Remove registry entries for entities not provided with this version and these settings.

    Otherwise they linger as "no longer provided", and deleting one in the UI
    deletes the whole helper.
    """
    from .sensor import current_keys

    keys = current_keys(showers)
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
    """v1 -> v2: drop the unused energy sensor. v2 -> v3: settings for the no-learning model."""
    data, options = dict(entry.data), dict(entry.options)
    if entry.version == 1:
        data.pop("energy_entity", None)
        options.pop("energy_entity", None)
    if entry.version < 3:
        old = {**data, **options}          # the old options flow only overrode some keys
        # the old number entities kept their values in the saved model state, not the entry
        from homeassistant.helpers.storage import Store

        from .const import DOMAIN
        from .runtime import STORE_VERSION

        stored = await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_load()
        numbers = ((stored or {}).get("model") or {}).get("settings") or {}
        for key, attr in (("thermostat", "thermostat_c"), ("shower_flow", "shower_flow_lpm"),
                          ("shower_temperature", "shower_temp_c"), ("cold_water_temperature", "cold_temp_c")):
            if numbers.get(attr) is not None:
                old[key] = numbers[attr]
        learned = ((stored or {}).get("model") or {}).get("learned") or {}
        thermostat, room = float(old.get("thermostat", 65.0)), float(old.get(CONF_ROOM_TEMP, 30.0))
        data = {
            CONF_SWITCH: old[CONF_SWITCH], CONF_POWER: old[CONF_POWER],
            "tank_volume": old.get("tank_volume", 35.0), "thermostat": thermostat,
            # what the old version had learned on the device; review them in Configure
            CONF_BAND: round(learned.get("band_kwh", 0.24), 3),
            CONF_HEAT_LOSS: round(learned.get("loss_w_full", 68.0) / max(1.0, thermostat - room), 2),
            CONF_ROOM_TEMP: room,
            "cold_water_temperature": old.get("cold_water_temperature", 30.0),
            CONF_WARM_TEMP: old.get("shower_temperature", 38.0),
            CONF_FROM_HISTORY: old.get("learn_from_history", True),
        }
        if old.get(CONF_SHOWER_FLOW):
            data[CONF_SHOWER_FLOW] = old[CONF_SHOWER_FLOW]
            data[CONF_SHORTEST_SHOWER] = 6.0
        options = {}
    hass.config_entries.async_update_entry(entry, data=data, options=options, version=3)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HeaterConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _async_options_updated(hass: HomeAssistant, entry: HeaterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
