"""A* must always agree with Dijkstra on cost, with or without closures.

Run from the project root with:  python -m pytest tests/test_astar.py -v
"""
import itertools
import random
from pathlib import Path

import pytest

from engine.graph import Graph
from engine.loader import load_dataset
from engine.router import _heuristic, astar, dijkstra

DATA_DIR = Path(__file__).parent.parent / "data"
DATASETS = ["toronto-downtown.json", "test-city.json"]


@pytest.fixture(params=DATASETS)
def graph(request):
    return Graph(load_dataset(DATA_DIR / request.param))


def test_same_cost_for_every_pair(graph):
    for start, goal in itertools.product(graph.nodes, repeat=2):
        d = dijkstra(graph, start, goal)
        a = astar(graph, start, goal)
        assert (d is None) == (a is None)
        if d is not None:
            assert a.cost == pytest.approx(d.cost)


def test_same_cost_with_random_closures(graph):
    rng = random.Random(7)
    edge_ids = list(graph.edges)
    nodes = list(graph.nodes)
    for _ in range(30):
        graph.closed_edges = set(rng.sample(edge_ids, k=max(1, len(edge_ids) // 6)))
        for _ in range(20):
            start, goal = rng.choice(nodes), rng.choice(nodes)
            d = dijkstra(graph, start, goal)
            a = astar(graph, start, goal)
            assert (d is None) == (a is None)
            if d is not None:
                assert a.cost == pytest.approx(d.cost)


def test_heuristic_never_overestimates(graph):
    # The guess from any node must be <= the real cheapest cost.
    goal = next(iter(graph.nodes))
    for node in graph.nodes:
        route = dijkstra(graph, node, goal)
        if route is not None:
            assert _heuristic(graph, node, goal) <= route.cost + 1e-9
