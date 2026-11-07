"""Web server: a public request page and a password-protected dispatcher console.

Run from the project root with:
    python -m uvicorn server:app --reload

    http://127.0.0.1:8000/          public: submit a request, track your own
    http://127.0.0.1:8000/dispatch  dispatcher console (password protected)

Dispatcher logins: each teammate gets their own account (see manage_users.py).
Until you create one, a single fallback account works: username "dispatcher",
password from $env:DISPATCH_PASSWORD (default "dispatch").

Load a different dataset (e.g. Dataset 2 on Day 2) without changing code:
    $env:DATASET = "data/other-city.json"
    python -m uvicorn server:app --reload

Who can see what:
- The public side only ever gets ONE request at a time, and only with that
  request's tracking code (e.g. "R4-7F3A9C"). Request ids alone (R1, R2, ...)
  are guessable, so the random part of the code is what keeps them private.
- Every dispatcher API and the live WebSocket check for a login cookie.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from fastapi import (APIRouter, Body, Depends, FastAPI, HTTPException, Request,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from auth import Accounts, LoginLimiter, Sessions
from engine import events as ev
from engine.crisis import RequestError
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset
from engine.public import public_status
from engine.router import shortest_path
from engine.scoring import load_config

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
DATA_PATH = Path(os.environ.get("DATASET", ROOT / "data" / "toronto-downtown.json"))
CONFIG_PATH = ROOT / "config.json"
REQUESTS_PATH = Path(os.environ.get(
    "REQUESTS", DATA_PATH.with_name(DATA_PATH.stem + "-requests.json")))
DISPATCH_PASSWORD = os.environ.get("DISPATCH_PASSWORD", "dispatch")
USERS_PATH = Path(os.environ.get("DISPATCH_USERS", ROOT / "dispatchers.json"))
SESSION_COOKIE = "dispatch_session"

dataset = load_dataset(DATA_PATH)

clients: List[WebSocket] = []
pending_events: list = []     # filled by the event bus, emptied by broadcast()
accounts = Accounts(USERS_PATH, DISPATCH_PASSWORD)
sessions = Sessions()         # logged-in dispatchers, with expiry
limiter = LoginLimiter()      # slows down password guessing
codes = {}                    # tracking code -> request id
code_for = {}                 # request id -> tracking code


def log_to_terminal(event):
    """A second subscriber: important events are also printed in the terminal."""
    print(f"[{event.time:7.1f} min] {event.type}: {event.message}", flush=True)


def new_dispatcher():
    config = load_config(CONFIG_PATH if CONFIG_PATH.exists() else None)
    d = Dispatcher(dataset, config)
    # The dispatcher console subscribes to every event on the bus...
    d.bus.subscribe_all(pending_events.append)
    # ...and the terminal logger only to the ones an operator must notice.
    for event_type in (ev.ROAD_CLOSED, ev.VEHICLE_BREAKDOWN,
                       ev.RESOURCE_DEPLETED, ev.CRISIS_RESOLVED):
        d.bus.subscribe(event_type, log_to_terminal)
    return d


dispatcher = new_dispatcher()


def issue_code(request_id):
    """Give a request a private tracking code (once)."""
    if request_id not in code_for:
        code = f"{request_id}-{secrets.token_hex(3).upper()}"
        codes[code] = request_id
        code_for[request_id] = code
    return code_for[request_id]


def dispatcher_snapshot():
    """The full state, plus each request's tracking code (dispatchers only)."""
    snap = dispatcher.snapshot()
    for c in snap["crises"]:
        c["tracking_code"] = code_for.get(c["request"]["id"])
    for s in snap["scheduled"]:
        s["tracking_code"] = code_for.get(s["id"])
    return snap


async def broadcast():
    """Push the events the bus just published, plus the new state, to dispatchers."""
    events = [asdict(e) for e in pending_events]
    pending_events.clear()
    state = dispatcher_snapshot()
    for ws in list(clients):
        try:
            await ws.send_json({"kind": "update", "events": events, "state": state})
        except Exception:
            clients.remove(ws)


async def respond():
    await broadcast()
    return dispatcher_snapshot()


def dispatcher_name(cookies):
    """The signed-in dispatcher's name, or None."""
    return sessions.user(cookies.get(SESSION_COOKIE))


def is_dispatcher(cookies):
    return dispatcher_name(cookies) is not None


async def require_dispatcher(request: Request):
    if not is_dispatcher(request.cookies):
        raise HTTPException(status_code=401, detail="Dispatcher login required")


def current_user(request: Request):
    return dispatcher_name(request.cookies)


app = FastAPI(title="Food Crisis Routing")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ================================================================ pages
@app.get("/")
def public_page():
    return FileResponse(STATIC / "public.html")


@app.get("/dispatch")
def dispatch_page(request: Request):
    if not is_dispatcher(request.cookies):
        return FileResponse(STATIC / "login.html")
    return FileResponse(STATIC / "index.html")


# ================================================================ shared
@app.get("/api/map")
def get_map():
    """The city itself (roads and named places). Same for everyone."""
    return {
        "name": dataset.name,
        "nodes": [{"id": n.id, "name": n.name, "lat": n.lat, "lng": n.lng}
                  for n in dataset.nodes.values()],
        "edges": [{"id": e.id, "a": e.a, "b": e.b, "street": e.street, "km": e.km,
                   "cost": e.cost, "hwy": e.hwy, "terrain": e.terrain}
                  for e in dataset.edges.values()],
        "pois": [{"id": p.id, "name": p.name, "kind": p.kind, "node": p.node,
                  "lat": p.lat, "lng": p.lng, "address": p.address}
                 for p in dataset.pois.values()],
        "has_sample_requests": REQUESTS_PATH.exists(),
    }


@app.post("/api/login")
def login(request: Request, username: str = Body("dispatcher"), password: str = Body(...)):
    who = request.client.host if request.client else "unknown"
    wait = limiter.locked_for(who)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"Too many wrong passwords. Try again in {wait} seconds.")
    name = accounts.check(username, password)
    if name is None:
        limiter.failed(who)
        raise HTTPException(status_code=401, detail="Wrong username or password")
    limiter.succeeded(who)
    token = sessions.start(name)
    response = JSONResponse({"ok": True, "name": name})
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict",
                        max_age=sessions.seconds)
    return response


@app.post("/api/logout")
def logout(request: Request):
    sessions.end(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


# ================================================================ public side
public = APIRouter(prefix="/api/public")


@public.post("/requests")
async def public_submit(data: Dict[str, Any] = Body(...)):
    """Anyone can submit. The time is stamped by the server, not the browser.

    The public form gives the deadline as "needed within N minutes"; it's
    turned into a clock time here, since only the server knows the time.
    """
    data = {k: v for k, v in data.items() if k not in ("id", "submitted_at")}
    within = data.pop("deadline_in", None)
    if "deadline" not in data and isinstance(within, (int, float)) and not isinstance(within, bool):
        data["deadline"] = dispatcher.now + within
    try:
        req = dispatcher.submit(data)
    except RequestError as err:
        return JSONResponse(status_code=422, content={"errors": err.errors})
    code = issue_code(req.id)
    await broadcast()                       # dispatchers see it arrive live
    return {"code": code}


@public.post("/status")
def public_status_many(tracking_codes: List[str] = Body(..., embed=True)):
    """Status of the requests whose codes you hold. Unknown codes are skipped."""
    results = []
    for code in tracking_codes[:20]:
        rid = codes.get(code)
        status = public_status(dispatcher, rid, code) if rid else None
        if status is not None:
            results.append(status)
    return {"requests": results}


app.include_router(public)


# ================================================================ dispatcher side
admin = APIRouter(prefix="/api", dependencies=[Depends(require_dispatcher)])


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    if not is_dispatcher(ws.cookies):
        await ws.close(code=1008)           # policy violation: not logged in
        return
    await ws.accept()
    clients.append(ws)
    await ws.send_json({"kind": "update", "events": [], "state": dispatcher_snapshot()})
    try:
        while True:
            await ws.receive_text()         # we only send; this keeps it open
    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)


@admin.get("/state")
def get_state():
    return dispatcher_snapshot()


@admin.get("/me")
def me(user: str = Depends(current_user)):
    return {"name": user, "fallback_account": accounts.using_fallback}


@admin.get("/route")
def get_route(start: int, goal: int):
    try:
        route = shortest_path(dispatcher.graph, start, goal)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if route is None:
        return {"found": False}
    return {"found": True, "nodes": route.nodes, "edge_ids": route.edge_ids,
            "cost": route.cost}


@admin.post("/requests")
async def submit_request(data: Dict[str, Any] = Body(...)):
    """Dispatchers can also enter requests (e.g. phoned in), including the time."""
    try:
        req = dispatcher.submit(data)
    except RequestError as err:
        return JSONResponse(status_code=422, content={"errors": err.errors})
    code = issue_code(req.id)
    await broadcast()
    return {"id": req.id, "code": code, "scheduled": req.submitted_at > dispatcher.now,
            "submitted_at": req.submitted_at}


@admin.post("/requests/sample")
async def load_sample():
    if not REQUESTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No sample requests file for this dataset")
    accepted, errors = dispatcher.load_requests_file(REQUESTS_PATH)
    for rid in accepted:
        issue_code(rid)
    await broadcast()
    return {"accepted": accepted, "errors": errors}


@admin.post("/clock")
async def advance_clock(minutes: float = Body(..., embed=True)):
    try:
        dispatcher.advance(minutes)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@admin.post("/edges/{edge_id}/close")
async def close_edge(edge_id: int, user: str = Depends(current_user)):
    try:
        dispatcher.close_road(edge_id, by=user)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))
    return await respond()


@admin.post("/edges/{edge_id}/open")
async def open_edge(edge_id: int, user: str = Depends(current_user)):
    try:
        dispatcher.open_road(edge_id, by=user)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))
    return await respond()


@admin.post("/trucks/{truck_id}/breakdown")
async def breakdown(truck_id: str, user: str = Depends(current_user)):
    try:
        dispatcher.break_down(truck_id, by=user)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@admin.post("/trucks/{truck_id}/repair")
async def repair(truck_id: str, user: str = Depends(current_user)):
    try:
        dispatcher.repair(truck_id, by=user)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@admin.post("/chaos")
async def set_chaos(enabled: bool = Body(...), level: str = Body(None),
                    user: str = Depends(current_user)):
    try:
        dispatcher.set_chaos(enabled, level, by=user)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@admin.post("/chaos/trigger")
async def trigger_chaos():
    what = dispatcher.trigger_chaos()
    state = await respond()
    return {"incident": what, "state": state}


@admin.post("/reset")
async def reset():
    global dispatcher
    dispatcher = new_dispatcher()
    pending_events.clear()
    codes.clear()
    code_for.clear()
    return await respond()


app.include_router(admin)

if accounts.using_fallback:
    note = " with the DEFAULT password 'dispatch'" if DISPATCH_PASSWORD == "dispatch" else ""
    print("No dispatcher accounts yet: log in as 'dispatcher'" + note + ". "
          "Create personal accounts with: python manage_users.py add <username> \"<Name>\"",
          flush=True)
