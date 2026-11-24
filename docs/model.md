# The model

All of it lives in `custom_components/power_metered_water_heater/model.py`, which has
no Home Assistant imports and is replayed offline by the tests and tools. Nothing is
learned: every parameter is a setting.

## Inputs

- Switch state (on/off).
- Power in W. Above 500 W the element is heating. The latest heating power is also
  used as the element's help in Shower Minutes Left.

## State

The tank's heat deficit `D` in kWh below full. Tank temperature is
`T = thermostat − D / C`, where `C = volume × 4.186 / 3600` kWh/°C (0.0407 for 35 L).

| Effect | Change to D |
|---|---|
| Standby loss | `+ heat_loss × (T − room) × dt` |
| Heater | `− power × dt` |
| Water drawn during a reheat | `+` at least the proven draw; with showers enabled, an assumed ongoing shower (capped at 12 min) once a draw is evident. Tank state only |
| Thermostat stops a reheat, switch still on | `D = 0` (anchor: tank full) |
| Thermostat starts a reheat by itself | `D = band` |

## Booking

Between two anchors the water energy is exact apart from the heat loss setting:

```
water = measured heat − modelled loss
```

- Litres = water ÷ (4.186 × (warm − cold) / 3600).
- With showers enabled, water of at least the shortest shower
  (`shortest × flow` litres) is a shower; less is other water.

Energy is booked whenever a reheat stops: the water it proves goes to water, the
rest of its measured heat to heat loss. So whenever no reheat is running,
water + heat loss = energy, hour by hour.

Live, during a reheat, only the guaranteed minimum (`heat − deficit at start −
margin`) is booked. Once it reaches the shortest shower, the reheat is a shower and
its litres are booked live; without showers, water is booked live directly. Any
over-booking is kept as a debt and taken from the next bookings, so running totals
never fall.

| Case | Handling |
|---|---|
| Switch turned off during a reheat | Water this reheat proves is booked at once. If it is less than a shower so far, it is held and classified with the rest at the next full tank (only this held amount is missing from the parts until then). The rest of a cut-short shower (up to 12 min in total) is added to that shower at the next full tank |
| First small reheat within 4 h of a big one | The tank was not fully mixed: after a reheat from cold, heat loss; after a shower, the heat above the modelled loss belongs to that shower |
| Water used while switched off | Found in the first reheat after switch-on; classified like any other water |

## Choosing the settings from history

`tools/analyze.py` reads a saved history (`tools/fetch_history.py`):

- **Element power**: median power while heating.
- **Thermostat band**: median energy of the small reheats in idle cycles.
- **Heat loss**: idle cycles are full tank → next small reheat with the switch on.
  Water use and tank mixing only shorten a cycle, so cycles under 3 h and the first
  small reheat after a big one or a switch-on are skipped, and the 25th percentile
  of the rest is used. Each cycle's W is divided by (average tank temperature −
  room).
- **Thermostat lower bound**: the most heat lost while off cannot exceed
  `C × (thermostat − room)`.
- **Off periods** (full tank → switch-off → reheat to full) are listed as a
  cross-check. They include any water used while off.

## Reference numbers (Joven JSH35, 13–24 Nov 2026)

| Quantity | Value |
|---|---|
| Element | 2.95 kW |
| Thermostat | 70 °C (hot tap measured 68 °C right after a full tank) |
| Thermostat band | 0.24 kWh, very consistent |
| Heat loss | clean idle cycles 51–61 W with the tank full: 1.5 W/°C at 70 °C and 30 °C room |
| Off periods | 43–53 W average over 6–26 h (include evening washing while off) |
| Shower | thermostatic mixer at 42.5 °C; heads 7.5 and 8.5 L/min; cold water 29.8–32.9 °C. Draw ≈ 5.3–6 kW |
| Timed shower check | about 10.5 min of running water; model 10.6 min |

Two Rheem EHG 30 L units in the same home are set to 65 and 75 °C and lose 57 and
42 W: heat loss depends on where a heater is installed, not only on its model.

## Calibrating

1. **Thermostat setting**: when Hot Water Ready turns on, run the hot tap alone for
   30 s and measure a cup with a kitchen thermometer. Add 1–2 °C.
2. **Warm and cold water**: a cup at the shower at your usual setting, and one from
   the cold tap after a minute.
3. **Heat loss**: leave the heater on overnight (or while away) with no hot water
   used, then run `tools/analyze.py`.
4. **Shower flow**: time how long a bucket of known size takes to fill at shower
   temperature. With two heads, average them.
