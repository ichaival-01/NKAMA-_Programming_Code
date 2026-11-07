"""Tests for engine/states.py and engine/events.py.

Run from the project root with:  python -m pytest tests/test_states.py -v
"""
import pytest

from engine import events as ev
from engine import states as st
from engine.events import EventBus
from engine.states import CrisisTracker, InvalidTransition


# ---------- state machine ----------

def test_starts_open():
    t = CrisisTracker("R1")
    assert t.state == st.OPEN
    assert t.history == []


def test_happy_path_is_allowed_and_logged():
    t = CrisisTracker("R1")
    path = [st.PRIORITIZED, st.RESOURCE_ASSIGNED, st.IN_PROGRESS, st.DELIVERED, st.RESOLVED]
    for minute, state in enumerate(path):
        t.move_to(state, minute, f"step {minute}")
    assert t.state == st.RESOLVED
    assert t.is_finished
    assert [h.to_state for h in t.history] == path
    assert t.history[2].from_state == st.RESOURCE_ASSIGNED
    assert t.history[2].time == 2
    assert t.history[2].reason == "step 2"


def test_reroute_and_back():
    t = CrisisTracker("R1")
    for s in (st.PRIORITIZED, st.RESOURCE_ASSIGNED, st.IN_PROGRESS,
              st.RE_ROUTING, st.IN_PROGRESS, st.DELIVERED):
        t.move_to(s, 0, "")
    assert t.state == st.DELIVERED


@pytest.mark.parametrize("bad_path", [
    [st.DELIVERED],                                  # skip everything
    [st.IN_PROGRESS],                                # no assignment yet
    [st.PRIORITIZED, st.RE_ROUTING],                 # nothing to re-route
    [st.PRIORITIZED, st.FAILED, st.PRIORITIZED],     # failed is final
])
def test_illegal_transitions_raise(bad_path):
    t = CrisisTracker("R1")
    with pytest.raises(InvalidTransition):
        for s in bad_path:
            t.move_to(s, 0, "")


def test_unknown_state_raises():
    with pytest.raises(InvalidTransition):
        CrisisTracker("R1").move_to("Teleported", 0, "")


def test_every_state_has_rules():
    assert set(st.ALLOWED) == set(st.ALL_STATES)
    for targets in st.ALLOWED.values():
        assert targets <= set(st.ALL_STATES)


def test_resolved_and_failed_are_final():
    assert st.ALLOWED[st.RESOLVED] == set()
    assert st.ALLOWED[st.FAILED] == set()


# ---------- event bus ----------

def test_subscribers_receive_their_events():
    bus = EventBus()
    got = []
    bus.subscribe(ev.ROAD_CLOSED, got.append)
    bus.publish(ev.ROAD_CLOSED, 5, "Road closed", edge_id=3)
    bus.publish(ev.CRISIS_CREATED, 6, "New crisis")
    assert len(got) == 1
    assert got[0].data == {"edge_id": 3}
    assert got[0].time == 5


def test_subscribe_all_gets_everything_in_order():
    bus = EventBus()
    got = []
    bus.subscribe_all(lambda e: got.append(e.type))
    bus.publish(ev.CRISIS_CREATED, 0, "a")
    bus.publish(ev.CRISIS_RESOLVED, 1, "b")
    assert got == [ev.CRISIS_CREATED, ev.CRISIS_RESOLVED]
    assert [e.type for e in bus.history] == got


def test_unknown_event_types_rejected():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.publish("made-up", 0, "x")
    with pytest.raises(ValueError):
        bus.subscribe("made-up", print)


def test_all_required_event_types_exist():
    required = {"crisis-created", "priority-changed", "route-updated",
                "resource-depleted", "road-closed", "vehicle-breakdown",
                "crisis-resolved"}
    assert required <= set(ev.EVENT_TYPES)
