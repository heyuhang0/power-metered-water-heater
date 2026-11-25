from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "power_metered_water_heater"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import HeaterModel, Settings, replay  # noqa: E402
from synth import as_fixture, true_settings, week  # noqa: E402


def _run(h, showers=True):
    m = HeaterModel(settings=true_settings(h, showers))
    showers_seen, snaps = [], []
    m.on_shower.append(lambda t, l, mins: showers_seen.append((t, l, mins)))
    replay(m, h.events, until=h.t, snapshot=lambda t, mm: snaps.append(
        (mm.totals.shower_l, mm.totals.other_l, mm.totals.shower_kwh, mm.totals.other_kwh,
         mm.totals.loss_kwh, mm.totals.energy_kwh, mm.totals.shower_count)))
    return m, showers_seen, snaps


@pytest.fixture(scope="module")
def synthetic():
    h = week()
    return h, *_run(h)


def test_counts_every_shower(synthetic):
    h, m, showers, _ = synthetic
    assert m.totals.shower_count == h.showers == len(showers)


def test_shower_litres_close_to_truth(synthetic):
    h, m, *_ = synthetic
    assert m.totals.shower_l == pytest.approx(h.litres("shower"), rel=0.06)


def test_total_litres_close_to_truth(synthetic):
    h, m, *_ = synthetic
    assert m.totals.total_l == pytest.approx(h.litres("shower") + h.litres("other"), rel=0.08)


def test_totals_never_decrease(synthetic):
    *_, snaps = synthetic
    for a, b in zip(snaps, snaps[1:]):
        assert all(y >= x - 1e-9 for x, y in zip(a, b))


def test_parts_match_energy_whenever_no_reheat_runs():
    """Hour by hour: with no reheat running, water + heat loss = energy (incl. switch-offs mid-reheat)."""
    h = week()
    m = HeaterModel(settings=true_settings(h))
    checked = 0

    def snap(t, mm):
        nonlocal checked
        if mm._burst is None and mm._end_pending is None and mm.deficit is not None:
            # water held from a cut-short reheat is the only part not yet booked
            parts = mm.totals.water_kwh + mm.totals.loss_kwh - mm._loss_debt + mm._seg.pending_kwh
            assert parts == pytest.approx(mm.totals.energy_kwh, abs=1e-6), t / 3600
            checked += 1
    replay(m, h.events, until=h.t, snapshot=snap)
    assert checked > 100


def test_energy_equals_measured_heat_since_tracking(synthetic):
    h, m, *_ = synthetic
    pw = sorted((t, v) for t, k, v in h.events if k == "power")
    first_full = next(pw[i + 1][0] for i, (t, v) in enumerate(pw[:-1]) if v > 0)
    heat = sum(v * (pw[i + 1][0] - t) for i, (t, v) in enumerate(pw[:-1]) if t >= first_full) / 3.6e6
    assert m.totals.energy_kwh == pytest.approx(heat, abs=1e-6)


def test_water_and_energy_align(synthetic):
    _, m, *_ = synthetic
    kpl = m.settings.kwh_per_litre
    assert m.totals.shower_kwh == pytest.approx(m.totals.shower_l * kpl)
    assert m.totals.other_kwh == pytest.approx(m.totals.other_l * kpl)


def test_showers_off_counts_one_total(synthetic):
    h, with_showers, *_ = synthetic
    m, showers, snaps = _run(h, showers=False)
    assert showers == [] and m.totals.shower_l == 0 and m.totals.shower_count == 0
    assert m.shower_minutes_left is None
    assert m.totals.total_l == pytest.approx(with_showers.totals.total_l, rel=0.02)
    for a, b in zip(snaps, snaps[1:]):
        assert all(y >= x - 1e-9 for x, y in zip(a, b))


def test_thermostat_stop_just_before_a_switch_off_is_a_full_tank():
    """The reheat stopped 9 s before an automation turned the switch off: tank full, water booked."""
    m = HeaterModel(settings=Settings(35, 70, 0.24, 1.5, 30, 31, 39.5, 8, 6))
    t1 = 4.5 * 3600
    drop = t1 + 600
    replay(m, [(0, "switch", True), (0, "power", 0), (3000, "power", 2950), (3300, "power", 0),
               (t1, "power", 2950), (drop, "power", 20), (drop + 9.4, "switch", False)],
           until=drop + 120)
    assert m.deficit == pytest.approx(0.0, abs=0.01)
    assert m.totals.water_kwh > 0.2
    assert m.totals.water_kwh + m.totals.loss_kwh == pytest.approx(m.totals.energy_kwh, abs=1e-6)


def test_ready_after_reheat():
    h = week()
    m = HeaterModel(settings=true_settings(h))
    seen = []
    for ev in sorted(h.events, key=lambda e: e[0]):
        replay(m, [ev])
        seen.append(m.hot_water_ready)
    assert any(seen) and not all(seen)


def test_round_trip_persistence(synthetic):
    _, m, *_ = synthetic
    m2 = HeaterModel.from_dict(json.loads(json.dumps(m.to_dict())), m.settings)
    assert m2.totals == m.totals and m2.deficit == m.deficit and m2.heating_kw == m.heating_kw


def test_state_saved_by_0_1_loads_without_double_counting(synthetic):
    """0.1.x saved settings and learned values and had no energy total."""
    _, m, *_ = synthetic
    d = json.loads(json.dumps(m.to_dict()))
    d["settings"] = {"tank_volume_l": 35.0}
    d["learned"] = {"loss_w_full": 71.0}
    d["last_correction_l"] = 1.0
    del d["totals"]["energy_kwh"]
    d["_seg"] = {k: v for k, v in d["_seg"].items() if k not in ("water_booked", "loss_prebooked")}
    d["_seg"]["loss"] = 0.4
    m2 = HeaterModel.from_dict(d, m.settings)
    t = m2.totals
    assert t.energy_kwh == pytest.approx(t.shower_kwh + t.other_kwh + t.loss_kwh)
    assert m2._seg.loss_prebooked == 0.4


def test_analyze_tool_recovers_the_true_settings():
    """tools/analyze.py on synthetic history: heat loss and band come back within 10 %."""
    from analyze import analyse
    h = week()
    r = analyse(as_fixture(h), thermostat=h.setpoint_c, room=h.room_c, tank=h.volume_l)
    assert r["band_kwh"] == pytest.approx(h.band_k * h.c, rel=0.1)
    assert r["heat_loss_w_per_k"] == pytest.approx(h.ua_w_per_k, rel=0.1)
    assert r["element_w"] == pytest.approx(h.element_w, rel=0.01)


PRIVATE = Path(__file__).parent / "fixtures" / "private" / "master_bath.json"


@pytest.mark.skipif(not PRIVATE.exists(), reason="private household fixture not present")
def test_private_master_bath_backtest():
    from backtest import run
    s = Settings(tank_volume_l=35, thermostat_c=65, band_kwh=0.239, heat_loss_w_per_k=1.79, room_temp_c=30,
                 cold_temp_c=30, warm_temp_c=38, shower_flow_lpm=9.5, shortest_shower_min=6)
    m, showers, snaps = run(str(PRIVATE), s)
    assert len(showers) >= 9                              # 9 up to 23 Nov; the fixture grows
    # user-timed shower on 23 Nov 10:07 lasted 12-13 minutes
    import datetime as dt
    sgt = dt.timezone(dt.timedelta(hours=8))
    lo, hi = (dt.datetime(2026, 11, 23, h, 0, tzinfo=sgt).timestamp() for h in (10, 12))
    nov23 = [x for x in showers if lo < x[0] < hi]
    assert nov23 and 12.0 <= nov23[0][2] <= 13.5
    t = m.totals
    open_heat = (m._seg.heat - m._seg.loss_settled - m._seg.water_booked) if m._burst else 0.0
    assert t.water_kwh + t.loss_kwh - m._loss_debt + m._seg.pending_kwh == pytest.approx(
        t.energy_kwh - open_heat, abs=1e-6)
