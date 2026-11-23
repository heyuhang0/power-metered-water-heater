# Power Metered Water Heater

A Home Assistant helper for a **storage (tank) water heater powered through a
power-metering smart plug**. From the plug's switch and power reading alone it
gives you a proper `water_heater` entity, the tank's estimated temperature, how
much hot water is left, and how much water was used: showers and everything else.

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=heyuhang0&repository=power-metered-water-heater&category=integration)

No cloud, no extra hardware, no flow meter. It runs entirely on the plug data
Home Assistant already records.

## How it works

A storage heater's thermostat switches the element on whenever the tank has
lost a fixed amount of heat, and off when it is full again. The plug's power
reading shows exactly when that happens and how much energy went in.

The integration tracks the tank's **heat deficit**:

- standby heat loss raises it (learned from the quiet hours when nobody uses water),
- hot water use raises it,
- the heater energy measured by the plug lowers it,
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

- A storage water heater with its own thermostat, on a smart plug that has
  - a `switch` entity, and
  - a `sensor` with device class `power` (W), reporting changes within a few seconds.
- The plug's entities must belong to a device. The new entities are added to that device.
- The recorder enabled (it is by default).
- Home Assistant 2026.10 or newer.

## Verified heaters

| Heater | Tank | Element | Notes |
|---|---|---|---|
| Joven JSH35 | 35 L | 3.0 kW (2.95 kW measured) | thermostat band ~0.24 kWh (~6 °C), standby loss ~68 W when full |

Other storage heaters should work. Tell us about yours in an issue.

## Install

### HACS

1. In HACS, open **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/heyuhang0/power-metered-water-heater` with category **Integration**.
3. Install **Power Metered Water Heater** and restart Home Assistant.

### Manual

```bash
cp -r custom_components/power_metered_water_heater /config/custom_components/
```

Restart Home Assistant.

## Set up

**Settings → Devices & services → Helpers → Create helper → Power Metered Water Heater.**

**Step 1: the plug**

| Field | Notes |
|---|---|
| Switch | The plug's on/off switch. |
| Power sensor | The plug's power in W. This is the main input. |
| Energy sensor | Optional. Only used to check the model's energy balance. |
| Tank volume | From the heater's label. |

**Step 2: starting settings** (all can be changed later on the device page)

| Field | Default | Notes |
|---|---|---|
| Thermostat setting | 65 °C | Water temperature when the tank is full. Often hidden on the heater: measure the hot tap right after a reheat finishes. |
| Shower flow | 9.5 L/min | Time how long a bucket of known size takes to fill. With two heads, use the average. |
| Shower temperature | 38 °C | |
| Cold water temperature | 30 °C | Cold mains water. Each 1 °C error moves litres by roughly 12 %. |
| Learn from recorder history | on | Replays the plug's recorded history (10 days by default) to learn the heater and start with the current tank state. |
| Backfill statistics | on | Writes hourly statistics for that period, so water and energy charts start before today. History graphs still start at setup. |

After a restart the integration replays what happened while Home Assistant was down.

## Entities

Names follow the plug's device name, e.g. `Master Bath Water Heater`.

| Entity | Unit | Meaning |
|---|---|---|
| `water_heater.<device>` | | On (Electric) / Off. Controls the plug's switch. Shows the estimated tank temperature. |
| `binary_sensor.<device>_hot_water_ready` | | On once the first reheat after switch-on has finished. |
| `sensor.<device>_tank_temperature` | °C | Estimated. Exact at the end of each reheat. |
| `sensor.<device>_hot_water_remaining` | % | Heat stored above the shower temperature, relative to a full tank. |
| `sensor.<device>_shower_minutes_left` | min | How long a shower can last from now, with the heater's help when it is on. |
| `sensor.<device>_total_water` | L | All water this heater supplied. Use this one in the Energy dashboard. |
| `sensor.<device>_shower_water` | L | Part of the total that came from showers. |
| `sensor.<device>_other_water` | L | Sink, short draws, use while switched off, model noise. |
| `sensor.<device>_shower_energy` | kWh | Same split as the water, in energy. |
| `sensor.<device>_other_energy` | kWh | |
| `sensor.<device>_heat_loss_energy` | kWh | Standby losses. Shower + Other + Heat Loss = the plug's energy at every full tank. |
| `event.<device>_shower` | | Fired once per shower with `water` (L) and `duration` (min). |
| `sensor.<device>_shower_count` | | Running count. Daily counts come from statistics. |
| `sensor.<device>_last_shower_water` | L | |
| `sensor.<device>_last_shower_duration` | min | At the configured shower flow. |
| `number.<device>_thermostat_setting` | °C | Settings, on the device page. |
| `number.<device>_shower_flow` | L/min | |
| `number.<device>_shower_temperature` | °C | |
| `number.<device>_cold_water_temperature` | °C | |
| `sensor.<device>_heat_loss_rate` | W | Diagnostic, hidden by default. |
| `sensor.<device>_last_correction` | L | Diagnostic: how much the last reheat end added on top of the live estimate. |

Tip: hide the plug's own switch and use the water heater entity in automations,
dashboards and HomeKit.

## Accuracy and limits

- Litres depend on the cold water and shower temperatures. Minutes also depend on
  the shower flow. Water energy itself is measured.
- Draws smaller than one thermostat band (about 25 L of shower water on a 35 L
  tank) are only seen when the next reheat ends. Totals stay right; timing is coarse.
- Two showers within one reheat (about 30 min) count as one long shower.
- Water used while the switch is off is counted when it is switched on again.
- The plug must report power changes promptly. Plugs that only report every few
  minutes will blur reheat start and end times.

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
