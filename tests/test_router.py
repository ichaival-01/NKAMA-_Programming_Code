"""Tests for engine/router.py (shortest_path) using the real dataset.

Run from the project root with:  python -m pytest
"""
from pathlib import Path

import pytest

from engine.loader import load_dataset
from engine.graph import Graph
from engine.router import shortest_path

DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


@pytest.fixture
def graph():
    """A fresh graph for every test, so closed roads don't leak between tests."""
    return Graph(load_dataset(DATA_FILE))


# ---------- normal routes ----------

def test_33_to_22(graph):
    route = shortest_path(graph, 33, 22)
    assert route.cost == pytest.approx(8.324, abs=0.001)
    assert route.nodes == [33, 34, 35, 36, 37, 38, 30, 22]


def test_33_to_44(graph):
    route = shortest_path(graph, 33, 44)
    assert route.cost == pytest.approx(6.804, abs=0.001)
    assert route.nodes == [33, 41, 42, 43, 44]


def test_8_to_23_cost_only(graph):
    # Several routes tie or nearly tie here, so only the cost is checked.
    route = shortest_path(graph, 8, 23)
    assert route.cost == pytest.approx(11.859, abs=0.001)
    assert len(route.edge_ids) == 8


def test_same_start_and_goal(graph):
    route = shortest_path(graph, 4, 4)
    assert route.cost == 0
    assert route.nodes == [4]
    assert route.edge_ids == []


def test_route_is_consistent(graph):
    # Each edge in the route should really connect the nodes on either side,
    # and the costs should add up to the total.
    route = shortest_path(graph, 33, 22)
    assert len(route.edge_ids) == len(route.nodes) - 1
    total = 0.0
    for i, eid in enumerate(route.edge_ids):
        edge = graph.edges[eid]
        assert {edge.a, edge.b} == {route.nodes[i], route.nodes[i + 1]}
        total += edge.cost
    assert total == pytest.approx(route.cost)


# ---------- closures and re-routing ----------

def test_all_roads_into_44_closed_returns_none(graph):
    for eid in (69, 78, 79):          # every edge touching node 44
        graph.close_edge(eid)
    assert shortest_path(graph, 33, 44) is None


def test_reroute_around_closed_road(graph):
    first = shortest_path(graph, 8, 23)
    graph.close_edge(first.edge_ids[0])
    second = shortest_path(graph, 8, 23)
    assert second is not None
    assert first.edge_ids[0] not in second.edge_ids
    assert second.cost == pytest.approx(11.859, abs=0.001)


def test_reopening_restores_route(graph):
    for eid in (69, 78, 79):
        graph.close_edge(eid)
    for eid in (69, 78, 79):
        graph.open_edge(eid)
    route = shortest_path(graph, 33, 44)
    assert route is not None
    assert route.cost == pytest.approx(6.804, abs=0.001)


def test_reopening_an_open_road_does_not_crash(graph):
    graph.open_edge(0)                # was never closed


# ---------- bad input ----------

def test_unknown_node_raises(graph):
    with pytest.raises(ValueError):
        shortest_path(graph, 33, 999)


def test_closing_unknown_edge_raises(graph):
    with pytest.raises(ValueError):
        graph.close_edge(999)