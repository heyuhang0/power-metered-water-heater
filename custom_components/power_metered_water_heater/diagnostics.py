"""Diagnostics: the model's full state (no personal data beyond heater usage)."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    return {"entry": {"data": dict(entry.data), "options": dict(entry.options)}, "model": rt.model.to_dict(),
            "stats_imported": rt.stats_imported}
