from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "power_metered_water_heater"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import HeaterModel, Learned, Settings, replay  # noqa: E402
from synth import week  # noqa: E402


def _run(h, **learned):
    m = HeaterModel(settings=Settings(thermostat_c=h.setpoint_c), learned=Learned(**learned))
    showers, snaps = [], []
    m.on_shower.append(lambda t, l, mins: showers.append((t, l, mins)))
    replay(m, h.events, until=h.t, snapshot=lambda t, mm: snaps.append(
        (mm.totals.shower_l, mm.totals.other_l, mm.totals.shower_kwh, mm.totals.other_kwh,
         mm.totals.loss_kwh, mm.totals.shower_count)))
    return m, showers, snaps


@pytest.fixture(scope="module")
def synthetic():
    h = week()
    return h, *_run(h)


def test_counts_every_shower(synthetic):
    h, m, showers, _ = synthetic
    assert m.totals.shower_count == h.showers


def test_shower_litres_close_to_truth(synthetic):
    h, m, *_ = synthetic
    assert m.totals.shower_l == pytest.approx(h.litres("shower"), rel=0.06)


def test_total_litres_close_to_truth(synthetic):
    h, m, *_ = synthetic
    truth = h.litres("shower") + h.litres("other")
    assert m.totals.total_l == pytest.approx(truth, rel=0.08)


def test_totals_never_decrease(synthetic):
    *_, snaps = synthetic
    for a, b in zip(snaps, snaps[1:]):
        assert all(y >= x - 1e-9 for x, y in zip(a, b))


def test_energy_parts_add_up_to_heater_energy(synthetic):
    h, m, *_ = synthetic
    pw = [(t, v) for t, k, v in h.events if k == "power"]
    first_full = next(pw[i + 1][0] for i, (t, v) in enumerate(pw[:-1]) if v > 0)  # tracking starts here
    heat = sum(v * (pw[i + 1][0] - t) for i, (t, v) in enumerate(pw[:-1]) if t >= first_full) / 3.6e6
    parts = m.totals.shower_kwh + m.totals.other_kwh + m.totals.loss_kwh
    # equal once the heat still missing from the tank at the end is allowed for
    assert parts - m.deficit == pytest.approx(heat, abs=0.1)


def test_learns_loss_rate(synthetic):
    h, m, *_ = synthetic
    true_full = h.ua_w_per_k * (h.setpoint_c - h.room_c)
    assert m.learned.loss_w_full == pytest.approx(true_full, rel=0.25)


def test_water_and_energy_align(synthetic):
    _, m, *_ = synthetic
    kpl = m.settings.kwh_per_litre
    assert m.totals.shower_kwh == pytest.approx(m.totals.shower_l * kpl)
    assert m.totals.other_kwh == pytest.approx(m.totals.other_l * kpl)


def test_ready_after_reheat():
    h = week()
    m = HeaterModel(settings=Settings(thermostat_c=h.setpoint_c))
    seen = []
    for ev in sorted(h.events, key=lambda e: e[0]):
        replay(m, [ev])
        seen.append(m.hot_water_ready)
    assert any(seen) and not all(seen)


def test_round_trip_persistence(synthetic):
    _, m, *_ = synthetic
    m2 = HeaterModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.totals == m.totals and m2.learned == m.learned and m2.deficit == m.deficit


PRIVATE = Path(__file__).parent / "fixtures" / "private" / "master_bath.json"


@pytest.mark.skipif(not PRIVATE.exists(), reason="private household fixture not present")
def test_private_master_bath_backtest():
    sys.path.insert(0, str(ROOT / "tools"))
    from backtest import run
    m, showers, _ = run(str(PRIVATE), thermostat_c=65.0)
    assert len(showers) == 9
    assert m.totals.total_l == pytest.approx(1310, rel=0.03)
    # user-timed shower on 23 Nov 10:07 lasted 12-13 minutes
    assert 12.0 <= showers[-1][2] <= 13.5
