"""The public page and the dispatcher console must stay separate.

Public visitors can submit requests and follow ONLY the ones whose tracking
codes they hold. Everything else needs the dispatcher login.

Needs the httpx package (pip install httpx); skipped if it isn't installed.
Run from the project root with:  python -m pytest tests/test_public.py -v
"""
import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def request(**changes):
    data = dict(location=22, crisis_type="flood", location_category="shelter",
                people_affected=100, food_needed=30, needs_refrigeration=False,
                severity=3, deadline_in=60)
    data.update(changes)
    return data


@pytest.fixture
def dispatcher():
    c = TestClient(server.app)
    c.post("/api/login", json={"username": "dispatcher", "password": server.DISPATCH_PASSWORD})
    c.post("/api/reset")
    return c


@pytest.fixture
def public(dispatcher):          # depends on `dispatcher` so the reset runs first
    return TestClient(server.app)


# ---------- pages ----------

def test_home_is_the_public_page(public):
    assert "Request food" in public.get("/").text


def test_console_asks_for_login(public):
    page = public.get("/dispatch").text
    assert "Password" in page and "Dispatcher console" in page
    assert "Load sample requests" not in page


def test_console_after_login(dispatcher):
    assert "Load sample requests" in dispatcher.get("/dispatch").text


def test_wrong_password(public):
    assert public.post("/api/login", json={"username": "dispatcher", "password": "nope"}).status_code == 401


def test_logout_ends_the_session(dispatcher):
    dispatcher.post("/api/logout")
    assert dispatcher.get("/api/state").status_code == 401


# ---------- dispatcher APIs are locked ----------

@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/state", None),
    ("get", "/api/route?start=0&goal=5", None),
    ("post", "/api/requests", {"location": 22}),
    ("post", "/api/requests/sample", None),
    ("post", "/api/clock", {"minutes": 5}),
    ("post", "/api/edges/3/close", None),
    ("post", "/api/edges/3/open", None),
    ("post", "/api/trucks/V1/breakdown", None),
    ("post", "/api/trucks/V1/repair", None),
    ("post", "/api/reset", None),
])
def test_dispatcher_apis_need_login(public, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(public, method)(url, **kwargs).status_code == 401


def test_live_feed_needs_login(public):
    with pytest.raises(Exception):
        with public.websocket_connect("/ws") as ws:
            ws.receive_json()


# ---------- public submission ----------

def test_public_submit_gives_a_tracking_code(public, dispatcher):
    code = public.post("/api/public/requests", json=request()).json()["code"]
    rid, secret = code.split("-")
    assert rid.startswith("R") and len(secret) == 6
    crisis = dispatcher.get("/api/state").json()["crises"][0]
    assert crisis["tracking_code"] == code              # dispatchers can read it out


def test_public_cannot_set_id_or_time(public, dispatcher):
    dispatcher.post("/api/clock", json={"minutes": 10})
    public.post("/api/public/requests", json=request(id="HACK", submitted_at=999, deadline_in=30))
    req = dispatcher.get("/api/state").json()["crises"][0]["request"]
    assert req["id"] != "HACK"
    assert req["submitted_at"] == 10                    # stamped by the server
    assert req["deadline"] == 40                        # now + 30


def test_public_form_errors(public):
    res = public.post("/api/public/requests", json=request(severity=9, people_affected=-1))
    assert res.status_code == 422
    assert len(res.json()["errors"]) == 2


# ---------- tracking: only your own ----------

def test_track_only_with_the_full_code(public):
    mine = public.post("/api/public/requests", json=request()).json()["code"]
    other = public.post("/api/public/requests", json=request(location=4)).json()["code"]
    got = public.post("/api/public/status", json={"tracking_codes": [mine]}).json()["requests"]
    assert [r["code"] for r in got] == [mine]
    assert other not in str(got)
    # The request id alone, or a wrong secret, shows nothing
    rid = mine.split("-")[0]
    for guess in (rid, f"{rid}-000000", other.split("-")[0] + "-" + mine.split("-")[1]):
        assert public.post("/api/public/status", json={"tracking_codes": [guess]}).json()["requests"] == []


def test_public_status_hides_internal_details(public):
    code = public.post("/api/public/requests", json=request()).json()["code"]
    r = public.post("/api/public/status", json={"tracking_codes": [code]}).json()["requests"][0]
    text = str(r).lower()
    for internal in ("score", "parts", "hub_id", "truck_id", "stock", "events", "closed_edges"):
        assert internal not in text, internal
    assert r["steps"] == ["Received", "In the queue", "Truck assigned", "On the way", "Delivered"]
    assert r["step"] == 3                               # a truck is already on the way
    assert r["eta"] is not None and r["truck"]["name"]


def test_progress_through_to_delivery(public, dispatcher):
    code = public.post("/api/public/requests", json=request()).json()["code"]
    dispatcher.post("/api/clock", json={"minutes": 30})
    r = public.post("/api/public/status", json={"tracking_codes": [code]}).json()["requests"][0]
    assert r["state"] == "Resolved" and r["step"] == 4 and r["finished"]
    assert [h["title"] for h in r["timeline"]][0] == "Received"


def test_plain_language_when_roads_are_closed(public, dispatcher):
    for eid in (69, 78, 79):                            # every road into node 44
        dispatcher.post(f"/api/edges/{eid}/close")
    code = public.post("/api/public/requests", json=request(location=44)).json()["code"]
    r = public.post("/api/public/status", json={"tracking_codes": [code]}).json()["requests"][0]
    assert r["problem"] is True
    assert "roads to your location are closed" in r["message"].lower()
    assert "position in queue" in r["message"].lower()


def test_reset_invalidates_codes(public, dispatcher):
    code = public.post("/api/public/requests", json=request()).json()["code"]
    dispatcher.post("/api/reset")
    assert public.post("/api/public/status", json={"tracking_codes": [code]}).json()["requests"] == []
