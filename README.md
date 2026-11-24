# Power Metered Water Heater

A Home Assistant helper for a **storage (tank) water heater powered through a
power-metering switch or smart plug**. From its on/off state and power reading
alone it gives you a proper `water_heater` entity with the tank's estimated
temperature, how much hot water is left, how much warm water was used, and where
the heater's electricity went. Optionally it splits water use into showers and
everything else.

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=heyuhang0&repository=power-metered-water-heater&category=integration)

No cloud, no extra hardware, no flow meter, and nothing learned on the device:
every parameter is a setting you can see.

## How it works

A storage heater's thermostat switches the element on whenever the tank has lost
a fixed amount of heat, and off when it is full again. The switch's power reading
shows exactly when that happens and how much energy went in.

The integration tracks the tank's **heat deficit**:

- standby heat loss raises it: heat loss (W per °C) × (tank − room temperature),
- warm water use raises it,
- the heat the switch measures lowers it,
- every time the thermostat stops a reheat with the switch still on, the tank is
  full again and the deficit is exactly zero.

Whenever a reheat stops, its measured heat is booked: the water it proves goes to
water, the rest to heat loss. Water the tank still lacks is booked when heat
replaces it. So whenever no reheat is running, Water Energy + Heat Loss Energy =
Energy. Water energy is converted to litres at the warm water temperature.

The totals are exact over time. Single events are only known when their reheat
ends, so live numbers lag a little and are settled then. Running totals never go
down. See [docs/model.md](docs/model.md) for details.

## Requirements

- A storage water heater with its own thermostat, powered through a relay switch or
  smart plug that has
  - a `switch` entity, and
  - a `sensor` with device class `power` (W), reporting changes within a few seconds.
- The recorder enabled (it is by default).
- Home Assistant 2026.10 or newer.

## Install

### Marketplace or HACS

1. Add `https://github.com/heyuhang0/power-metered-water-heater` as a custom repository
   with category **Integration**: in the built-in **Marketplace** on Home Assistant
   2026.11 and newer, or in HACS on 2026.10.
2. Install **Power Metered Water Heater** and restart Home Assistant.

### Manual

```bash
cp -r custom_components/power_metered_water_heater /config/custom_components/
```

Restart Home Assistant.

## Set up

**Settings → Devices & services → Helpers → Create helper → Power Metered Water Heater.**
There are no default values: take them from your heater, its recorded history (see
[Choosing the settings](#choosing-the-settings)) or a measurement.

| Step | Field | Meaning |
|---|---|---|
| 1. Switch | Switch | The on/off switch powering the heater. It is controlled through the new water heater entity |
| | Power sensor | Its power in W, the only measurement |
| 2. Name | Name | Suggested: the switch's device name without "Switch" or "Plug". The device is placed in the switch's area; entity IDs = area + name |
| 3. Heater | Tank volume | L, from the heater's label |
| | Thermostat setting | °C, water temperature when the tank is full |
| | Thermostat band | kWh, energy of one normal small reheat (topping up a full tank after standing idle) |
| | Heat loss | W per °C between tank and room: a constant of the heater and its pipes |
| | Room temperature | °C, air around the tank |
| 4. Water | Cold water temperature | °C, mains water |
| | Warm water temperature | °C. All water is counted as litres at this temperature |
| | Start from recorded history | Replays the switch's recorded history (10 days by default) to start with the current tank state, and writes statistics for that period so charts start before today |
| 5. Showers (optional) | Shower flow | L/min at the shower head; with two heads, their average |
| | Shortest shower | min. Water use at least this long (at the shower flow) counts as a shower, also when it was used while the heater was off |

Leave both shower fields empty to count water as one total.

**Configure** (on the helper) has the same fields except the name, in one form.
Changes apply from then on; past statistics stay as they are. If the switch or power
sensor is renamed, the helper follows it.

## Entities

The integration creates its own device for the water heater, with the name from step
2, in the switch's area, shown as connected via the switch's device. `<id>` below
stands for area + name, e.g. `master_bath_water_heater`.

| Entity | Unit | Meaning |
|---|---|---|
| `water_heater.<id>` | | Off / Electric; turns the switch on and off. Current temperature = estimated tank temperature; target = thermostat setting |
| `binary_sensor.<id>_hot_water_ready` | | On once the first reheat after switch-on has finished |
| `sensor.<id>_hot_water_remaining` | % | Heat stored above the warm water temperature, relative to a full tank |
| `sensor.<id>_energy` | kWh | Everything the heater used since tracking began, from the power readings |
| `sensor.<id>_water_energy` | kWh | The part that went into water |
| `sensor.<id>_heat_loss_energy` | kWh | The part that replaced standby heat loss |
| `sensor.<id>_total_water` | L | All warm water |

With showers enabled:

| Entity | Unit | Meaning |
|---|---|---|
| `sensor.<id>_shower_water`, `sensor.<id>_other_water` | L | Shower + Other = Total Water |
| `sensor.<id>_shower_energy`, `sensor.<id>_other_energy` | kWh | Shower + Other = Water Energy |
| `event.<id>_shower` | | Fired once per shower with `water` (L) and `duration` (min) |
| `sensor.<id>_shower_count` | | Running count; daily counts come from statistics |
| `sensor.<id>_last_shower_water`, `sensor.<id>_last_shower_duration` | L, min | Duration at the shower flow |
| `sensor.<id>_shower_minutes_left` | min | Shower time from now. With the switch on, the element's help is included |

**Energy dashboard:** add `sensor.<id>_energy` as an individual device, with Shower
Energy and Other Energy (or Water Energy) as devices with it as their upstream
device. Its untracked consumption is then the heat loss.

Entities that a new version no longer provides are removed automatically. Don't
delete the integration's entities yourself: for a helper, Home Assistant deletes the
whole helper with them. Tip: hide the switch entity and use the water heater entity
in automations, dashboards and HomeKit.

## Choosing the settings

| Setting | How |
|---|---|
| Thermostat setting | When Hot Water Ready turns on, run the hot tap alone for 30 s and measure a cup with a kitchen thermometer (reads 1–2 °C low) |
| Thermostat band, heat loss | From recorded history: `tools/analyze.py` (below). For heat loss it uses idle cycles of at least 3 h with the switch on, skips the first small reheat after a big one or after a switch-on, and takes the 25th percentile. A night with the heater on and no hot water used gives the cleanest data |
| Shower flow | Time how long a bucket of known size takes to fill at shower temperature |
| Cold water temperature | Thermometer at the cold tap after running it for a minute. It varies over the day (29.8–32.9 °C in the reference home); use the value at the time you use most hot water |
| Warm water temperature | Thermometer in a cup at the shower, at your usual setting |

Reference heaters (Singapore, measured November 2026):

| Setting | Joven JSH35 | Rheem EHG 30 L (2 units) |
|---|---|---|
| Tank volume | 35 L | 30 L |
| Thermostat | 70 °C | 65 °C and 75 °C (set differently) |
| Thermostat band | 0.24 kWh | 0.20–0.28 kWh |
| Heat loss | 1.5 W/°C (about 55 W with the tank full) | 1.0–1.8 W/°C (42–57 W), depending on where it is installed |
| Room / cold / warm water | 30 / 31 / 42.5 °C | 30 / 31 / 42.5 °C |
| Shower flow / shortest shower | 8 L/min / 6 min | |

## Accuracy and limits

- Litres depend on the cold and warm water temperatures; minutes also on the shower
  flow. Water energy itself is measured.
- Draws smaller than one thermostat band (about 18 L at 42.5 °C on a 35 L tank) are only
  seen when the next reheat ends. Totals stay right; timing is coarse.
- Two showers within one reheat (about 30 min) count as one long shower.
- Water used while the switch is off is booked when it is switched on and the tank is
  full again.
- If the switch cuts a reheat short, water that reheat proves is booked at once; if it
  is too little to call a shower yet, it waits for the next full tank and is
  classified with the rest.
- The power sensor must report changes promptly. Devices that only report every few
  minutes blur reheat start and end times.

## Privacy

Everything stays in Home Assistant. Diagnostics downloads contain the model's state,
which reveals when water was used.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install 'homeassistant==2026.11.2' -r requirements_test.txt
.venv/bin/python -m pytest -q --ignore=tests/test_integration.py      # model + HA contract

python3 -m venv .venv-ha && .venv-ha/bin/pip install 'pytest-homeassistant-custom-component==0.13.375'
.venv-ha/bin/python -m pytest -q                                       # + end to end in a real HA
```

The public tests use a synthetic heater (`tests/synth.py`) with known ground truth.
For your own heater:

```bash
python3 -I tools/fetch_history.py tests/fixtures/private/heater.json switch.x sensor.x_power   # HA_URL, HA_TOKEN
python3 -I tools/analyze.py  tests/fixtures/private/heater.json --tank 35 --thermostat 70 --room 30
python3 -I tools/backtest.py tests/fixtures/private/heater.json --tank 35 --thermostat 65 --band 0.24 \
    --heat-loss 1.5 --room 30 --cold 31 --warm 42.5 --flow 8 --shortest 6
```

`tests/fixtures/private/` is gitignored: recorded history reveals your routine.

## License

MIT
