"""Chaos mode: random closures, breakdowns and surprise requests that
recover on their own.

Run from the project root with:  python -m pytest tests/test_chaos.py -v
"""
from pathlib import Path

import pytest

from engine import events as ev
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset
from engine.scoring import load_config

DATA_DIR = Path(__file__).parent.parent / "data"


def make(dataset="toronto-downtown.json", seed=5):
    config = load_config()
    config["chaos_seed"] = seed
    return Dispatcher(load_dataset(DATA_DIR / dataset), config)


def kinds(d):
    return [e.type for e in d.bus.history]


def test_off_by_default_nothing_happens():
    d = make()
    d.advance(300)
    assert ev.ROAD_CLOSED not in kinds(d)
    assert ev.VEHICLE_BREAKDOWN not in kinds(d)
    assert d.requests == {}


def test_on_causes_incidents_over_time():
    d = make()
    d.set_chaos(True, "high")
    d.advance(120)
    assert ev.CHAOS_TOGGLED in kinds(d)
    happened = {ev.ROAD_CLOSED, ev.VEHICLE_BREAKDOWN} & set(kinds(d))
    assert happened or d.requests                      # something chaotic occurred
    assert any("by Chaos" in e.message for e in d.bus.history)


def test_city_recovers_on_its_own():
    d = make()
    d.set_chaos(True, "high")
    d.advance(60)
    d.set_chaos(False)
    d.advance(60)                                      # recoveries take at most 45 min
    assert d.graph.closed_edges == set()
    assert d.pool.broken == set()
    assert "recovered" in " ".join(e.message for e in d.bus.history)


def test_turning_off_stops_new_incidents():
    d = make()
    d.set_chaos(True, "high")
    d.advance(30)
    d.set_chaos(False)
    d.advance(60)
    before = len([k for k in kinds(d) if k in (ev.ROAD_CLOSED, ev.VEHICLE_BREAKDOWN)])
    d.advance(300)
    after = len([k for k in kinds(d) if k in (ev.ROAD_CLOSED, ev.VEHICLE_BREAKDOWN)])
    assert after == before


@pytest.mark.parametrize("kind,event", [("road", ev.ROAD_CLOSED),
                                        ("breakdown", ev.VEHICLE_BREAKDOWN),
                                        ("request", ev.CRISIS_CREATED)])
def test_trigger_each_kind_now(kind, event):
    d = make()
    d.submit(dict(location=44, crisis_type="x", location_category="shelter",
                  people_affected=10, food_needed=10, needs_refrigeration=False,
                  severity=3, deadline=90))
    before = kinds(d).count(event)
    assert d.chaos.trigger(d, kind)
    assert kinds(d).count(event) == before + 1


def test_keeps_the_city_usable():
    d = make()
    for _ in range(60):
        d.chaos.trigger(d, "road")
        d.chaos.trigger(d, "breakdown")
    assert len(d.graph.closed_edges) <= len(d.graph.edges) // 7
    assert len(d.dataset.trucks) - len(d.pool.broken) >= 2


def test_bad_level_rejected():
    with pytest.raises(ValueError):
        make().set_chaos(True, "apocalyptic")


@pytest.mark.parametrize("dataset", ["toronto-downtown.json", "test-city.json"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_long_chaos_run_stays_consistent(dataset, seed):
    d = make(dataset, seed)
    d.set_chaos(True, "high")
    for _ in range(40):
        d.advance(5)
        jobs = list(d.pool.busy.values())
        assert len(jobs) == len(set(jobs))             # no truck on two jobs
    d.set_chaos(False)
    d.advance(3000)
    assert all(t.is_finished for t in d.trackers.values())
    assert not d.pool.busy
