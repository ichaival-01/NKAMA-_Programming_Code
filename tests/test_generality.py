"""Goal 6: the same code must handle any dataset in the same format.

test-city.json is a made-up city that differs from Toronto on purpose:
non-sequential node ids (100, 107, ...), edge ids starting at 500, POI ids
like "H-A", location kinds Toronto doesn't have ("school", "clinic"),
highways and hill roads, only 2 hubs and 5 trucks, and a river that only two
bridges (edges 509 and 527) cross.

Run from the project root with:  python -m pytest tests/test_generality.py -v
"""
import random
from pathlib import Path

import pytest

from engine import states as st
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset

DATA_DIR = Path(__file__).parent.parent / "data"
DATASETS = ["toronto-downtown.json", "test-city.json"]


@pytest.fixture(params=DATASETS)
def d(request):
    return Dispatcher(load_dataset(DATA_DIR / request.param))


def site_requests(d):
    """One request at every non-hub point of interest, using its own kind."""
    items = []
    for i, poi in enumerate(p for p in d.dataset.pois.values() if p.kind != "hub"):
        items.append(dict(id=f"S{i}", location=poi.node, crisis_type="storm",
                          location_category=poi.kind, people_affected=50 + 40 * i,
                          food_needed=20, needs_refrigeration=(i % 2 == 0),
                          severity=1 + i % 5, deadline=120))
    return items


def test_every_site_gets_served(d):
    accepted, errors = d.load_requests(site_requests(d))
    assert errors == {}
    d.advance(300)
    for rid in accepted:
        assert d.trackers[rid].state == st.RESOLVED, d.trackers[rid].history


def test_unknown_location_kinds_still_score(d):
    d.load_requests(site_requests(d))
    for rid, score in d.scores.items():
        assert 0 <= score.total <= 100


def test_random_chaos_never_breaks_the_rules(d):
    """Lots of requests, closures, reopenings and breakdowns at random times.

    Every transition goes through CrisisTracker.move_to, which raises on any
    illegal move, so simply finishing without an exception proves every
    crisis followed the state machine. We also check no truck is ever on two
    jobs and every crisis reaches a final state once things calm down.
    """
    rng = random.Random(11)
    nodes = list(d.dataset.nodes)
    edges = list(d.dataset.edges)
    trucks = list(d.dataset.trucks)
    for step in range(120):
        roll = rng.random()
        if roll < 0.45:
            d.submit(dict(location=rng.choice(nodes), crisis_type="flood",
                          location_category=rng.choice(["hospital", "shelter", "school"]),
                          people_affected=rng.randint(1, 600),
                          food_needed=rng.randint(5, 60),
                          needs_refrigeration=rng.random() < 0.3,
                          severity=rng.randint(1, 5),
                          deadline=d.now + rng.randint(15, 240)))
        elif roll < 0.6:
            d.close_road(rng.choice(edges))
        elif roll < 0.75 and d.graph.closed_edges:
            d.open_road(rng.choice(sorted(d.graph.closed_edges)))
        elif roll < 0.8:
            healthy = [t for t in trucks if t not in d.pool.broken]
            if len(healthy) > 2:
                d.break_down(rng.choice(healthy))
        else:
            broken = sorted(d.pool.broken)
            if broken:
                d.repair(rng.choice(broken))
        d.advance(rng.uniform(0, 8))

        jobs = list(d.pool.busy.values())
        assert len(jobs) == len(set(jobs))
        for rid in d.trips:
            assert d.pool.busy.get(d.assignments[rid].truck_id) == rid

    # Calm down: reopen everything, fix every truck, let time run out.
    for eid in sorted(d.graph.closed_edges):
        d.open_road(eid)
    for tid in sorted(d.pool.broken):
        d.repair(tid)
    d.advance(2000)
    for rid, tracker in d.trackers.items():
        assert tracker.is_finished, (rid, tracker.state)
    assert not d.pool.busy


def test_bridges_matter_in_test_city():
    d = Dispatcher(load_dataset(DATA_DIR / "test-city.json"))
    # West hub H-A sits at node 128, the hospital S-1 at node 121 across the river.
    d.close_road(509)
    d.close_road(527)                       # both bridges closed
    d.pool.stock["H-B"]["normal"] = 0       # the east hub has nothing to give
    d.submit(dict(location=121, crisis_type="fire", location_category="hospital",
                  people_affected=300, food_needed=30, needs_refrigeration=False,
                  severity=5, deadline=60))
    assert d.trackers["R1"].state == st.DELAYED
    assert "route" in d.waiting["R1"]
    d.open_road(509)                        # one bridge reopens
    assert d.trackers["R1"].state == st.IN_PROGRESS
    d.advance(120)
    assert d.trackers["R1"].state == st.RESOLVED
