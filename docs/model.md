# The model

All of it lives in `custom_components/power_metered_water_heater/model.py`,
which has no Home Assistant imports and is replayed offline by the tests.

## Inputs

- Switch state (on/off).
- Power of the switch in W. Above 500 W the element is heating.

## State

The tank's heat deficit `D` in kWh below full. Tank temperature is
`T = thermostat − D / C`, where `C = volume × 4.186 / 3600` kWh/K
(0.0407 kWh/K for 35 L).

| Effect | Change to D |
|---|---|
| Standby loss | `+ UA × (T − room) × dt`, with `UA = loss_when_full / (thermostat − room)` |
| Heater | `− power × dt` |
| Hot water (best guess, tank state only) | `+` assumed shower draw during a reheat that shows one |
| Thermostat stops a reheat, switch still on | `D = 0` (anchor) |
| Thermostat starts a reheat by itself | `D = band` |

## Booking water

Between two anchors the water energy is exact apart from the loss model:

```
U = heater energy − modelled loss
```

- `U ≥ shower threshold` (0.35 kWh) → shower, otherwise other water.
- Litres = `U / (4.186 × (shower temp − cold temp) / 3600)`.
- Minutes = litres / shower flow.

Live, during a reheat, only the guaranteed minimum `heat − deficit at start − margin`
is booked. Once it passes the threshold the reheat is known to be a shower and is
booked to Shower live. The rest is settled when the reheat ends. Any over-booking
is kept as a debt and taken from the next bookings, so running totals never fall.

Special cases:

| Case | Handling |
|---|---|
| Switch turned off during a reheat | That reheat's draw is credited at switch-off. The heat still missing is credited to the same shower at the next full tank (up to a typical shower). |
| Quick small reheat within 2 h of a big one | The tank was not fully mixed: after a reheat from cold it is heat loss, after a shower it belongs to that shower. |
| Water used while switched off | Shows up in the first reheat after switch-on; booked then. |

## Learning

- Element power: running average of the power while heating.
- Thermostat band and loss when full: from quiet cycles (switch on, ≥ 3 h, a single
  band-sized reheat, no draw).
- Typical shower length: running average of detected showers.

## Reference numbers (Joven JSH35, 13–23 Nov 2026)

| Quantity | Value |
|---|---|
| Element | 2.94 kW |
| Thermostat band | 0.24 kWh (≈ 6 °C on 35 L), very consistent |
| Loss when full | 51–78 W from clean idle gaps; learned 71 W |
| Cool-down while off | ≈ 50–55 W average over 6–26 h |
| Shower draw | 5.3 kW at 9.5 L/min, 38 °C, 30 °C cold |
| Energy balance | water + loss = 24.21 kWh vs the switch's energy meter 24.21 kWh |
| Timed shower check | user timed 12–13 min; model 12.6 min (not fitted) |

The thermostat setting was hidden on this heater. The data only bounds it to at
least ~61 °C (a 25.8 h cool-down lost 1.27 kWh). It changes the tank outputs, not
the water totals (±3 % between 61 and 70 °C).

## Calibrating

1. **Thermostat setting**: when Hot Water Ready turns on, run the hot tap alone for
   30 s and measure a cup with a kitchen thermometer.
2. **Shower flow**: time how long a bucket of known size takes to fill at shower
   temperature. With two heads, average them.
3. Watch **Last Correction**. Consistently large values mean a setting is off.
