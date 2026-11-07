"""End-to-end tests for engine/dispatcher.py on the real Toronto data.

Run from the project root with:  python -m pytest tests/test_dispatcher.py -v
"""
import json
import random
from pathlib import Path

import pytest

from engine import events as ev
from engine import states as st
from engine.crisis import RequestError
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset

DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


@pytest.fixture
def d():
    return Dispatcher(load_dataset(DATA_FILE))


def req(**changes):
    data = dict(location=22, crisis_type="flood", location_category="shelter",
                people_affected=100, food_needed=30, needs_refrigeration=False,
                severity=3, deadline=90)
    data.update(changes)
    return data


def states(d, rid):
    return [h.to_state for h in d.trackers[rid].history]


def event_types(d):
    return [e.type for e in d.bus.history]


# ---------- intake ----------

def test_submit_assigns_and_starts_delivery(d):
    r = d.submit(req())
    assert r.id == "R1"                       # id filled in automatically
    assert r.submitted_at == 0                # submitted "now"
    assert states(d, "R1") == [st.OPEN, st.PRIORITIZED, st.RESOURCE_ASSIGNED, st.IN_PROGRESS]
    assert d.assignments["R1"].truck_id == "V10"
    for e in (ev.CRISIS_CREATED, ev.PRIORITY_CHANGED, ev.ROUTE_UPDATED):
        assert e in event_types(d)


def test_every_transition_has_time_and_reason(d):
    d.submit(req())
    d.advance(30)
    history = d.trackers["R1"].history
    assert history[0].to_state == st.OPEN          # arrival is logged too
    for h in history:
        assert h.reason
        assert h.time >= 0
        assert h.logged_at                         # real date and time as well


def test_invalid_request_is_rejected_and_not_stored(d):
    with pytest.raises(RequestError) as info:
        d.submit(req(severity=9, location=999))
    assert len(info.value.errors) == 2
    assert d.requests == {}


def test_duplicate_id_rejected(d):
    d.submit(req(id="X"))
    with pytest.raises(RequestError):
        d.submit(req(id="X"))


def test_load_requests_reports_bad_ones(d):
    accepted, errors = d.load_requests([req(id="A"), req(id="B", food_needed=-1)])
    assert accepted == ["A"]
    assert "B" in errors


# ---------- delivery over time ----------

def test_delivery_resolves_and_frees_truck(d):
    d.submit(req())
    eta = d.trips["R1"].arrival
    d.advance(eta + 1)
    assert d.trackers["R1"].state == st.RESOLVED
    assert states(d, "R1")[-2:] == [st.DELIVERED, st.RESOLVED]
    assert d.pool.is_free("V10")
    assert d.pool.truck_location["V10"] == 22
    assert d.pool.stock["P2"]["normal"] == 270
    assert ev.CRISIS_RESOLVED in event_types(d)


def test_not_delivered_before_arrival(d):
    d.submit(req())
    d.advance(d.trips["R1"].arrival - 0.5)
    assert d.trackers["R1"].state == st.IN_PROGRESS


def test_scheduled_request_waits_for_its_time(d):
    d.submit(req(id="LATER", submitted_at=20, deadline=120))
    assert "LATER" not in d.requests
    d.advance(19)
    assert "LATER" not in d.requests
    d.advance(2)
    assert d.trackers["LATER"].state in (st.IN_PROGRESS, st.DELAYED)


# ---------- priority and greedy matching ----------

def test_most_urgent_gets_the_scarce_truck(d):
    # Only 3 refrigerated trucks exist; 4 cold requests arrive together.
    items = [req(id=f"C{s}", needs_refrigeration=True, severity=s, food_needed=30)
             for s in (1, 5, 3, 4)]
    d.load_requests(items)
    waiting = [rid for rid in d.requests if d.trackers[rid].state == st.DELAYED]
    assert waiting == ["C1"]                  # the least severe one waits
    assert "truck" in d.waiting["C1"]


def test_waiting_request_gets_next_free_truck(d):
    items = [req(id=f"C{s}", needs_refrigeration=True, severity=s) for s in (1, 5, 3, 4)]
    d.load_requests(items)
    d.advance(200)
    assert d.trackers["C1"].state == st.RESOLVED
    assert st.DELAYED in states(d, "C1")


def test_no_truck_is_ever_double_booked(d):
    rng = random.Random(3)
    nodes = list(d.dataset.nodes)
    for i in range(25):
        d.submit(req(id=f"Q{i}", location=rng.choice(nodes), severity=rng.randint(1, 5),
                     food_needed=rng.randint(5, 40),
                     needs_refrigeration=rng.random() < 0.3,
                     deadline=d.now + rng.randint(20, 200)))
        busy = list(d.pool.busy.values())
        assert len(busy) == len(set(busy))
        active = [rid for rid in d.trips]
        trucks = [d.assignments[rid].truck_id for rid in active]
        assert len(trucks) == len(set(trucks))
        d.advance(rng.randint(0, 6))


# ---------- delays and failures ----------

def test_late_delivery_is_flagged_delayed(d):
    d.submit(req(location=44, deadline=1))
    assert d.trackers["R1"].state == st.DELAYED
    d.advance(60)
    assert d.trackers["R1"].state == st.RESOLVED
    assert "late" in d.trackers["R1"].history[-2].reason


def test_impossible_request_eventually_fails(d):
    d.submit(req(food_needed=70, deadline=30))     # no truck holds 70
    assert d.trackers["R1"].state == st.DELAYED
    d.advance(30 + d.config["failure_grace"] + 1)
    assert d.trackers["R1"].state == st.FAILED
    assert "R1" not in d.queue


# ---------- road closures ----------

def test_closure_on_route_triggers_rerouting(d):
    d.submit(req(location=44))                     # V2 from node 33 via Bloor
    route_edges = list(d.trips["R1"].edge_ids)
    blocked = route_edges[-1]
    d.close_road(blocked)
    assert states(d, "R1")[-2:] == [st.RE_ROUTING, st.IN_PROGRESS]
    assert blocked not in d.trips["R1"].edge_ids
    assert ev.ROAD_CLOSED in event_types(d)
    d.advance(60)
    assert d.trackers["R1"].state == st.RESOLVED


def test_closure_elsewhere_does_not_reroute(d):
    d.submit(req(location=44))
    route_edges = set(d.trips["R1"].edge_ids)
    other = next(e for e in d.graph.edges if e not in route_edges)
    d.close_road(other)
    assert st.RE_ROUTING not in states(d, "R1")


def test_cut_off_waits_then_resumes_when_road_reopens(d):
    d.submit(req(location=44))
    for eid in (69, 78, 79):                       # every road into node 44
        d.close_road(eid)
    assert d.trackers["R1"].state == st.DELAYED
    assert "R1" in d.stuck
    d.open_road(78)
    assert d.trackers["R1"].state == st.IN_PROGRESS
    d.advance(60)
    assert d.trackers["R1"].state == st.RESOLVED


def test_new_request_to_unreachable_site_waits(d):
    for eid in (69, 78, 79):
        d.close_road(eid)
    d.submit(req(location=44))
    assert d.trackers["R1"].state == st.DELAYED
    assert "route" in d.waiting["R1"]
    crisis = next(c for c in d.snapshot()["crises"] if c["request"]["id"] == "R1")
    assert crisis["route_unavailable"] is True     # shown plainly on the dashboard


# ---------- breakdowns ----------

def test_breakdown_before_loading_returns_food(d):
    d.submit(req(id="A", location=44, needs_refrigeration=True))   # V1 from 33
    first = d.assignments["A"].truck_id
    hub = d.assignments["A"].hub_id
    before = d.pool.stock[hub]["cold"]
    d.trips["A"].hub_index = 5                      # pretend the hub is still ahead
    d.break_down(first)
    assert first in d.pool.broken
    assert ev.VEHICLE_BREAKDOWN in event_types(d)
    assert d.assignments["A"].truck_id != first     # replacement found
    assert st.DELAYED in states(d, "A")
    # food went back, then the replacement took fresh stock
    assert sum(s["cold"] for s in d.pool.stock.values()) == 300 - 30
    assert d.pool.stock[hub]["cold"] <= before + 30


def test_breakdown_after_loading_writes_off_the_load(d):
    d.submit(req(id="A", location=44))              # V2 loads at node 33 at t=0
    d.advance(1)
    d.break_down(d.assignments["A"].truck_id)
    total_normal = sum(s["normal"] for s in d.pool.stock.values())
    assert total_normal == 900 - 60                 # lost load + replacement load
    d.advance(60)
    assert d.trackers["A"].state == st.RESOLVED


def test_broken_truck_is_never_assigned(d):
    d.break_down("V10")
    d.submit(req())
    assert d.assignments["R1"].truck_id != "V10"


def test_unknown_truck_rejected(d):
    with pytest.raises(ValueError):
        d.break_down("V99")


# ---------- resources ----------

def test_low_stock_warning(d):
    d.pool.stock["P2"]["normal"] = 60
    d.submit(req(food_needed=30))
    assert any(e.type == ev.RESOURCE_DEPLETED and e.data.get("hub_id") == "P2"
               for e in d.bus.history)


def test_out_of_stock_everywhere(d):
    for s in d.pool.stock.values():
        s["cold"] = 0
    d.submit(req(needs_refrigeration=True))
    assert d.trackers["R1"].state == st.DELAYED
    assert ev.RESOURCE_DEPLETED in event_types(d)


# ---------- dashboard data ----------

def test_snapshot_is_json_ready(d):
    d.submit(req())
    d.submit(req(food_needed=70))
    d.advance(3)
    snap = d.snapshot()
    json.dumps(snap)                                # must not raise
    assert snap["now"] == 3
    assert {c["request"]["id"] for c in snap["crises"]} == {"R1", "R2"}
    assert snap["queue"][0]["id"] == "R2"
    assert set(snap["queue"][0]["parts"]) == {"severity", "people", "food",
                                              "deadline", "location"}


def test_clock_cannot_go_backwards(d):
    with pytest.raises(ValueError):
        d.advance(-1)
