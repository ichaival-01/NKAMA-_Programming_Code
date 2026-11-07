"""Tests for server.py: the website, its WebSocket, and the event bus wiring.

Needs the httpx package (pip install httpx); skipped if it isn't installed.
Run from the project root with:  python -m pytest tests/test_server.py -v
"""
import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

REQUIRED_EVENTS = {"crisis-created", "priority-changed", "route-updated",
                   "resource-depleted", "road-closed", "vehicle-breakdown",
                   "crisis-resolved"}


@pytest.fixture
def client():
    c = TestClient(server.app)
    assert c.post("/api/login", json={"username": "dispatcher", "password": server.DISPATCH_PASSWORD}).status_code == 200
    c.post("/api/reset")
    return c


def request(**changes):
    data = dict(location=22, crisis_type="flood", location_category="shelter",
                people_affected=100, food_needed=30, needs_refrigeration=False,
                severity=3, deadline=90)
    data.update(changes)
    return data


def test_form_errors_come_back_as_a_list(client):
    res = client.post("/api/requests", json={"location": 999, "severity": 9})
    assert res.status_code == 422
    assert any("crisis_type" in e for e in res.json()["errors"])


def test_submission_time_can_be_set(client):
    res = client.post("/api/requests", json=request(submitted_at=20, deadline=80)).json()
    assert res["scheduled"] is True
    state = client.get("/api/state").json()
    assert state["scheduled"][0]["id"] == res["id"]
    client.post("/api/clock", json={"minutes": 21})
    state = client.get("/api/state").json()
    assert any(c["request"]["id"] == res["id"] for c in state["crises"])


def test_bus_events_are_pushed_over_the_websocket(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()                                   # initial state
        client.post("/api/requests", json=request())
        msg = ws.receive_json()
        assert msg["kind"] == "update"
        types = {e["type"] for e in msg["events"]}
        assert {"crisis-created", "priority-changed", "route-updated"} <= types
        assert all(e["logged_at"] for e in msg["events"])


def test_every_required_event_reaches_the_website(client):
    seen = set()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()

        def act(method, url, **kw):
            getattr(client, method)(url, **kw)
            seen.update(e["type"] for e in ws.receive_json()["events"])

        act("post", "/api/requests", json=request(location=44))
        trip_edge = 78                                      # on the Bloor St route to node 44
        act("post", f"/api/edges/{trip_edge}/close")
        act("post", "/api/trucks/V5/breakdown")
        for i in range(6):                                  # drain cold stock to trigger depletion
            act("post", "/api/requests",
                json=request(location=4, needs_refrigeration=True, food_needed=35, severity=2))
        act("post", "/api/clock", json={"minutes": 120})
    missing = REQUIRED_EVENTS - seen
    assert not missing, missing


def test_terminal_logger_is_a_second_subscriber(client, capsys):
    client.post("/api/edges/5/close")
    assert "road-closed" in capsys.readouterr().out
