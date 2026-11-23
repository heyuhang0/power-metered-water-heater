# Power Metered Water Heater

A Home Assistant helper for a **storage (tank) water heater powered through a
power-metering switch or smart plug**. From its on/off state and power reading alone it
gives you a proper `water_heater` entity with the tank's estimated temperature, how
much hot water is left, how much water was used (showers and everything else),
and where the heater's electricity went.

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=heyuhang0&repository=power-metered-water-heater&category=integration)

No cloud, no extra hardware, no flow meter. It runs entirely on the switch data
Home Assistant already records.

## How it works

A storage heater's thermostat switches the element on whenever the tank has
lost a fixed amount of heat, and off when it is full again. The switch's power
reading shows exactly when that happens and how much energy went in.

The integration tracks the tank's **heat deficit**:

- standby heat loss raises it (learned from the quiet hours when nobody uses water),
- hot water use raises it,
- the heater energy measured by the switch lowers it,
- every time the thermostat stops a reheat with the switch still on, the tank is
  full again and the deficit is exactly zero.

Between two "tank full" moments, **water energy = heater energy − heat loss**.
That energy is converted to litres of mixed water at your shower temperature.
A reheat that used more than a shower's worth of heat is counted as a shower.

The totals are exact over time. Single events are only known when their reheat
ends, so live numbers lag a little during a shower and are corrected at the end.
Running totals never go down. See [docs/model.md](docs/model.md) for the details
and the numbers behind them.

## Requirements

- A storage water heater with its own thermostat, powered through a relay switch or
  smart plug that has
  - a `switch` entity, and
  - a `sensor` with device class `power` (W), reporting changes within a few seconds.
- The recorder enabled (it is by default).
- Home Assistant 2026.10 or newer.

## Verified heaters

| Heater | Tank | Element | Notes |
|---|---|---|---|
| Joven JSH35 | 35 L | 3.0 kW (2.95 kW measured) | thermostat band ~0.24 kWh (~6 °C), standby loss ~68 W when full |

Other storage heaters should work. Tell us about yours in an issue.

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

**Step 1: the switch**

| Field | Notes |
|---|---|
| Switch | The on/off switch that powers the heater (a relay switch or a smart plug). |
| Power sensor | The switch's power reading in W. This is the main input. |
| Tank volume | From the heater's label. |

**Step 2: the name** of the water heater device. The default is the switch's device
name without a trailing hardware word ("Water Heater Switch" becomes "Water Heater").
Entity IDs are built from the area and this name.

**Step 3: starting settings** (all can be changed later on the device page)

| Field | Default | Notes |
|---|---|---|
| Thermostat setting | 65 °C | Water temperature when the tank is full. Often hidden on the heater: measure the hot tap right after a reheat finishes. |
| Shower flow | 9.5 L/min | Time how long a bucket of known size takes to fill. With two heads, use the average. |
| Shower temperature | 38 °C | |
| Cold water temperature | 30 °C | Cold mains water. Each 1 °C error moves litres by roughly 12 %. |
| Learn from recorder history | on | Replays the switch's recorded history (10 days by default) to learn the heater and start with the current tank state. |
| Backfill statistics | on | Writes statistics for that period, so water and energy charts start before today. History graphs still start at setup. |

After a restart the integration replays what happened while Home Assistant was down.

## Entities

The integration creates its own device for the water heater, with the name from
step 2, placed in the switch's area and shown as connected via the switch's device.
You can rename the device afterwards; existing entity IDs stay as they are.

Entities removed in a new version are removed from the registry automatically.
Don't delete the integration's entities yourself: for a helper, Home Assistant
deletes the whole helper with them.

Entity IDs follow Home Assistant's usual pattern of area + device name + entity
name. Below, `<id>` stands for that prefix, e.g. `master_bath_water_heater`.

| Entity | Unit | Meaning |
|---|---|---|
| `water_heater.<id>` | | On (Electric) / Off. Turns the switch on and off. Its current temperature is the estimated tank temperature (exact at the end of each reheat). |
| `binary_sensor.<id>_hot_water_ready` | | On once the first reheat after switch-on has finished. |
| `sensor.<id>_hot_water_remaining` | % | Heat stored above the shower temperature, relative to a full tank. |
| `sensor.<id>_shower_minutes_left` | min | How long a shower can last from now, with the heater's help when it is on. |
| `sensor.<id>_total_water` | L | All water this heater supplied. Use this one in the Energy dashboard. |
| `sensor.<id>_shower_water` | L | Part of the total that came from showers. |
| `sensor.<id>_other_water` | L | Sink, short draws, use while switched off, model noise. |
| `sensor.<id>_shower_energy` | kWh | Electricity that heated shower water. |
| `sensor.<id>_other_energy` | kWh | Electricity that heated other water. |
| `sensor.<id>_heat_loss_energy` | kWh | Electricity that replaced standby heat loss. Booked when a reheat ends. |
| `event.<id>_shower` | | Fired once per shower with `water` (L) and `duration` (min). |
| `sensor.<id>_shower_count` | | Running count. Daily counts come from statistics. |
| `sensor.<id>_last_shower_water` | L | |
| `sensor.<id>_last_shower_duration` | min | At the configured shower flow. |
| `number.<id>_thermostat_setting` | °C | Settings, on the device page. |
| `number.<id>_shower_flow` | L/min | |
| `number.<id>_shower_temperature` | °C | |
| `number.<id>_cold_water_temperature` | °C | |
| `sensor.<id>_heat_loss_rate` | W | Diagnostic: current heat loss at the estimated tank temperature. |
| `sensor.<id>_last_correction` | L | Diagnostic: how much the last reheat end added on top of the live estimate. |

The three energy sensors split the electricity the switch measured: at every full
tank, Shower + Other + Heat Loss equals the switch's energy since setup. In the
Energy dashboard, add the switch's energy sensor as the device and the three parts
as devices included in it. There is no separate total: the switch's meter is it.

Tip: hide the switch entity and use the water heater entity in automations,
dashboards and HomeKit. The integration follows the switch and power sensor if
you rename their entity IDs.

## Accuracy and limits

- Litres depend on the cold water and shower temperatures. Minutes also depend on
  the shower flow. Water energy itself is measured.
- Draws smaller than one thermostat band (about 25 L of shower water on a 35 L
  tank) are only seen when the next reheat ends. Totals stay right; timing is coarse.
- Two showers within one reheat (about 30 min) count as one long shower.
- Water used while the switch is off is counted when it is switched on again.
- The switch must report power changes promptly. Devices that only report every
  few minutes will blur reheat start and end times.

## Privacy

Everything stays in Home Assistant. Diagnostics downloads contain the model's
state, which reveals when water was used.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install 'homeassistant==2026.11.2' -r requirements_test.txt
.venv/bin/python -m pytest -q --ignore=tests/test_integration.py      # model + HA contract

python3 -m venv .venv-ha && .venv-ha/bin/pip install 'pytest-homeassistant-custom-component==0.13.375'
.venv-ha/bin/python -m pytest -q                                       # + end to end in a real HA
```

The public tests use a synthetic heater (`tests/synth.py`) with known ground
truth. To backtest against your own heater, save its history with
`tools/fetch_history.py` into `tests/fixtures/private/` (gitignored, it reveals
your routine) and run `tools/backtest.py`.

## License

MIT
