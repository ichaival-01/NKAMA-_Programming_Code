"""Tests for engine/matcher.py (greedy resource matching, goal 3).

Run from the project root with:  python -m pytest tests/test_matcher.py -v
"""
from pathlib import Path

import pytest

from engine.loader import load_dataset
from engine.graph import Graph
from engine.crisis import CrisisRequest
from engine.matcher import Assignment, NoMatch, ResourcePool, find_match

DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


@pytest.fixture
def dataset():
    return load_dataset(DATA_FILE)


@pytest.fixture
def graph(dataset):
    return Graph(dataset)


@pytest.fixture
def pool(dataset):
    return ResourcePool(dataset)


def make_request(**changes):
    fields = dict(
        id="R1", location=22, crisis_type="flood", location_category="shelter",
        people_affected=100, food_needed=30, needs_refrigeration=False,
        severity=3, deadline=90, submitted_at=0,
    )
    fields.update(changes)
    return CrisisRequest(**fields)


# ---------- picking the best option ----------

def test_picks_fastest_hub_and_truck(pool, graph):
    # Node 22 (Moss Park) is one road from the East depot (P2, node 23),
    # and trucks V9-V12 start there, so no driving to the hub is needed.
    a = find_match(make_request(), pool, graph, now=0)
    assert isinstance(a, Assignment)
    assert a.hub_id == "P2"
    assert a.travel_time == pytest.approx(2.1945, abs=0.001)


def test_saves_refrigerated_trucks_when_not_needed(pool, graph):
    # V9 (refrigerated) and V10 (normal) are both at node 23; use V10.
    a = find_match(make_request(), pool, graph, now=0)
    assert a.truck_id == "V10"


def test_refrigerated_request_gets_refrigerated_truck_and_cold_stock(pool, graph):
    a = find_match(make_request(needs_refrigeration=True), pool, graph, now=0)
    assert a.truck_id == "V9"
    assert a.stock_type == "cold"


def test_assignment_has_both_route_legs(pool, graph):
    a = find_match(make_request(location=44), pool, graph, now=0)
    assert a.to_hub.nodes[-1] == a.to_site.nodes[0]       # legs meet at the hub
    assert a.to_site.nodes[-1] == 44
    assert a.travel_time == pytest.approx(a.to_hub.cost + a.to_site.cost)


def test_arrival_time_and_on_time_flag(pool, graph):
    a = find_match(make_request(deadline=90), pool, graph, now=10)
    assert a.arrival_time == pytest.approx(10 + a.travel_time)
    assert a.on_time is True
    late = find_match(make_request(deadline=1), pool, graph, now=0)
    assert late.on_time is False      # still assigned, but flagged as late


def test_assignment_explains_itself(pool, graph):
    a = find_match(make_request(), pool, graph, now=0)
    assert a.reason                                   # non-empty explanation
    assert "Truck 10" in a.reason


# ---------- no suitable option ----------

def test_no_truck_big_enough(pool, graph):
    result = find_match(make_request(food_needed=70), pool, graph, now=0)
    assert isinstance(result, NoMatch)
    assert "truck" in result.reason.lower()


def test_no_refrigerated_truck_big_enough(pool, graph):
    # Refrigerated trucks hold 40 units at most.
    result = find_match(make_request(food_needed=50, needs_refrigeration=True), pool, graph, now=0)
    assert isinstance(result, NoMatch)


def test_no_hub_has_enough_stock(pool, graph):
    for hub_id in pool.stock:
        pool.stock[hub_id]["cold"] = 5
    result = find_match(make_request(needs_refrigeration=True), pool, graph, now=0)
    assert isinstance(result, NoMatch)
    assert "cold" in result.reason


def test_site_cut_off_by_closures(pool, graph):
    for eid in (69, 78, 79):          # every road into node 44
        graph.close_edge(eid)
    result = find_match(make_request(location=44), pool, graph, now=0)
    assert isinstance(result, NoMatch)
    assert "route" in result.reason.lower()


# ---------- reservations: no double assignment ----------

def test_reserve_takes_stock_and_truck(pool, graph):
    a = find_match(make_request(), pool, graph, now=0)
    before = pool.stock["P2"]["normal"]
    pool.reserve(a)
    assert pool.stock["P2"]["normal"] == before - 30
    assert not pool.is_free("V10")


def test_reserved_truck_is_not_offered_again(pool, graph):
    first = find_match(make_request(id="R1", needs_refrigeration=True), pool, graph, now=0)
    pool.reserve(first)                       # V9 is now busy
    second = find_match(make_request(id="R2", needs_refrigeration=True), pool, graph, now=0)
    assert second.truck_id != "V9"
    assert second.truck_id == "V1"            # next fastest reefer, from node 33
    assert second.travel_time == pytest.approx(8.3242, abs=0.001)


def test_reserving_same_truck_twice_raises(pool, graph):
    a = find_match(make_request(), pool, graph, now=0)
    pool.reserve(a)
    with pytest.raises(ValueError):
        pool.reserve(a)


def test_depleted_hub_is_skipped(pool, graph):
    pool.stock["P2"]["normal"] = 10           # not enough for 30 units
    a = find_match(make_request(), pool, graph, now=0)
    assert a.hub_id != "P2"


def test_release_returns_stock_and_truck(pool, graph):
    a = find_match(make_request(), pool, graph, now=0)
    before = pool.stock["P2"]["normal"]
    pool.reserve(a)
    pool.release(a)                           # e.g. delivery failed or cancelled
    assert pool.stock["P2"]["normal"] == before
    assert pool.is_free("V10")


def test_complete_frees_truck_at_the_site(pool, graph):
    a = find_match(make_request(), pool, graph, now=0)
    pool.reserve(a)
    pool.complete(a, site=22)
    assert pool.is_free("V10")
    assert pool.truck_location["V10"] == 22   # it starts its next job from here
    assert pool.stock["P2"]["normal"] == 300 - 30   # delivered food stays used


def test_pool_does_not_change_the_dataset(dataset, pool, graph):
    pool.reserve(find_match(make_request(), pool, graph, now=0))
    assert dataset.hubs["P2"].normal == 300
