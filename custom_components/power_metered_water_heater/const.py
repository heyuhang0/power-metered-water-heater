"""Constants for Power Metered Water Heater."""
from __future__ import annotations

DOMAIN = "power_metered_water_heater"

CONF_NAME = "name"
CONF_SWITCH = "switch_entity"
CONF_POWER = "power_entity"
# heater
CONF_TANK_VOLUME = "tank_volume"
CONF_THERMOSTAT = "thermostat"
CONF_BAND = "thermostat_band"
CONF_HEAT_LOSS = "heat_loss"
CONF_ROOM_TEMP = "room_temperature"
# water
CONF_COLD_TEMP = "cold_water_temperature"
CONF_WARM_TEMP = "warm_water_temperature"
CONF_FROM_HISTORY = "from_history"
# showers (optional)
CONF_SHOWER_FLOW = "shower_flow"
CONF_SHORTEST_SHOWER = "shortest_shower"

HEATER_KEYS = (CONF_TANK_VOLUME, CONF_THERMOSTAT, CONF_BAND, CONF_HEAT_LOSS, CONF_ROOM_TEMP)
WATER_KEYS = (CONF_COLD_TEMP, CONF_WARM_TEMP)
SHOWER_KEYS = (CONF_SHOWER_FLOW, CONF_SHORTEST_SHOWER)

HARDWARE_SUFFIXES = (" Switch", " Plug", " Socket", " Relay")
HISTORY_DAYS = 30            # how far back to replay on first setup (recorder keeps 10 by default)
SAVE_DELAY_S = 300
TICK_S = 60
EVENT_SHOWER = "shower"
