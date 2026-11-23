"""Constants for Power Metered Water Heater."""
from __future__ import annotations

DOMAIN = "power_metered_water_heater"

CONF_SWITCH = "switch_entity"
CONF_POWER = "power_entity"
CONF_ENERGY = "energy_entity"
CONF_TANK_VOLUME = "tank_volume"
CONF_THERMOSTAT = "thermostat"
CONF_SHOWER_FLOW = "shower_flow"
CONF_SHOWER_TEMP = "shower_temperature"
CONF_COLD_TEMP = "cold_water_temperature"
CONF_ROOM_TEMP = "room_temperature"
CONF_SHOWER_THRESHOLD = "shower_threshold"
CONF_LEARN_HISTORY = "learn_from_history"
CONF_IMPORT_STATISTICS = "import_statistics"

DEFAULT_TANK_VOLUME = 35.0
DEFAULT_THERMOSTAT = 65.0
DEFAULT_SHOWER_FLOW = 9.5
DEFAULT_SHOWER_TEMP = 38.0
DEFAULT_COLD_TEMP = 30.0
DEFAULT_ROOM_TEMP = 30.0
DEFAULT_SHOWER_THRESHOLD = 0.35

HISTORY_DAYS = 30            # how far back to replay on first setup (recorder keeps 10 by default)
SAVE_DELAY_S = 300
TICK_S = 60
EVENT_SHOWER = "shower"
