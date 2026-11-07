"""Fuel: trucks burn fuel per km, never take a trip they can't finish, and
are out of service while refuelling.

Run from the project root with:  python -m pytest tests/test_fuel.py -v
"""
from pathlib import Path

import pytest

from engine import events as ev
from engine import states as st
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset
from engine.matcher import NoMatch, ResourcePool, find_match, route_km
from engine.graph import Graph
from engine.crisis import CrisisRequest
from engine.scoring import load_config

DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


def config(per_km=6.0, reserve=25, refuel_minutes=10):
    c = load_config()
    c["fuel"] = {"per_km": per_km, "reserve": reserve, "refuel_minutes": refuel_minutes}
    return c


def req(**changes):
    data = dict(location=44, crisis_type="flood", location_category="shelter",
                people_affected=100, food_needed=30, needs_refrigeration=False,
                severity=3, deadline=90)
    data.update(changes)
    return data


def test_fuel_burns_per_km_driven():
    d = Dispatcher(load_dataset(DATA_FILE), config(per_km=6))
    d.submit(req())
    a = d.assignments["R1"]
    d.advance(60)
    assert d.trackers["R1"].state == st.RESOLVED
    assert d.pool.fuel[a.truck_id] == pytest.approx(100 - 6 * a.km)


def test_fuel_burns_gradually_along_the_route():
    d = Dispatcher(load_dataset(DATA_FILE), config(per_km=6))
    d.submit(req())
    tid = d.assignments["R1"].truck_id
    d.advance(d.trips["R1"].arrival / 2)
    assert 100 - 6 * d.assignments["R1"].km < d.pool.fuel[tid] < 100


def test_low_tank_refuels_for_ten_minutes():
    d = Dispatcher(load_dataset(DATA_FILE), config(per_km=6, reserve=95))
    d.submit(req())
    tid = d.assignments["R1"].truck_id
    eta = d.trips["R1"].arrival
    d.advance(eta)
    assert tid in d.pool.refuelling                    # below the reserve after delivery
    assert not d.pool.is_free(tid)
    assert d.pool.refuelling[tid] == pytest.approx(eta + 10)
    assert any(e.type == ev.VEHICLE_REFUELLING for e in d.bus.history)
    d.advance(9.9)
    assert tid in d.pool.refuelling                    # still out of service
    d.advance(0.2)
    assert d.pool.is_free(tid)
    assert d.pool.fuel[tid] == d.pool.max_fuel[tid]    # full again
    assert any(e.type == ev.VEHICLE_READY for e in d.bus.history)


def test_refuelling_truck_is_not_dispatched():
    d = Dispatcher(load_dataset(DATA_FILE), config())
    d.pool.refuelling["V10"] = 50
    d.submit(req(location=22))
    assert d.assignments["R1"].truck_id != "V10"


def test_matcher_skips_trucks_that_cannot_finish():
    ds = load_dataset(DATA_FILE)
    pool, graph = ResourcePool(ds, fuel_per_km=6), Graph(ds)
    for tid in pool.fuel:
        pool.fuel[tid] = 1.0                           # nearly empty everywhere
    r = CrisisRequest(id="R1", location=44, crisis_type="x", location_category="shelter",
                      people_affected=1, food_needed=10, needs_refrigeration=False,
                      severity=3, deadline=60, submitted_at=0)
    result = find_match(r, pool, graph, now=0)
    assert isinstance(result, NoMatch) and result.kind == "fuel"
    pool.fuel["V2"] = 100                              # one truck refilled
    assert find_match(r, pool, graph, now=0).truck_id == "V2"


def test_no_fuel_anywhere_sends_trucks_to_refuel_then_delivers():
    d = Dispatcher(load_dataset(DATA_FILE), config(per_km=6))
    for tid in d.pool.fuel:
        d.pool.fuel[tid] = 1.0
    d.submit(req())
    assert d.trackers["R1"].state == st.DELAYED
    assert "fuel" in d.waiting["R1"]
    assert d.pool.refuelling                           # idle suitable trucks went to refuel
    d.advance(10.5)
    assert d.trackers["R1"].state in (st.IN_PROGRESS, st.DELAYED)
    assert "R1" in d.assignments
    d.advance(60)
    assert d.trackers["R1"].state == st.RESOLVED


def test_fuel_can_be_switched_off():
    d = Dispatcher(load_dataset(DATA_FILE), config(per_km=0))
    d.submit(req())
    tid = d.assignments["R1"].truck_id
    d.advance(60)
    assert d.pool.fuel[tid] == d.pool.max_fuel[tid]
    assert d.snapshot()["fuel_on"] is False


def test_route_km_adds_up():
    ds = load_dataset(DATA_FILE)
    graph = Graph(ds)
    from engine.router import shortest_path
    route = shortest_path(graph, 33, 44)
    assert route_km(graph, route) == pytest.approx(sum(ds.edges[e].km for e in route.edge_ids))
