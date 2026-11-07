"""Web server: the dispatch dashboard and its live updates.

Run from the project root with:
    python -m uvicorn server:app --reload
then open http://127.0.0.1:8000 in your browser.

Load a different dataset (e.g. Dataset 2 on Day 2) without changing code:
    $env:DATASET = "data/other-city.json"
    python -m uvicorn server:app --reload

Sample requests are loaded from a file next to the dataset with the same name
plus "-requests", e.g. data/toronto-downtown-requests.json, or from the path
in $env:REQUESTS. Scoring weights come from config.json.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from engine.crisis import RequestError
from engine.dispatcher import Dispatcher
from engine.loader import load_dataset
from engine.router import shortest_path
from engine.scoring import load_config

ROOT = Path(__file__).parent
DATA_PATH = Path(os.environ.get("DATASET", ROOT / "data" / "toronto-downtown.json"))
CONFIG_PATH = ROOT / "config.json"
REQUESTS_PATH = Path(os.environ.get(
    "REQUESTS", DATA_PATH.with_name(DATA_PATH.stem + "-requests.json")))

dataset = load_dataset(DATA_PATH)


def new_dispatcher():
    config = load_config(CONFIG_PATH if CONFIG_PATH.exists() else None)
    return Dispatcher(dataset, config)


dispatcher = new_dispatcher()
clients: List[WebSocket] = []

app = FastAPI(title="Food Crisis Routing")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


async def broadcast():
    """Send the latest state to every open dashboard."""
    state = dispatcher.snapshot()
    for ws in list(clients):
        try:
            await ws.send_json({"kind": "state", "state": state})
        except Exception:
            clients.remove(ws)


async def respond():
    await broadcast()
    return dispatcher.snapshot()


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    await ws.send_json({"kind": "state", "state": dispatcher.snapshot()})
    try:
        while True:
            await ws.receive_text()       # we only send; this keeps it open
    except WebSocketDisconnect:
        if ws in clients:
            clients.remove(ws)


# ---------------------------------------------------------------- reading
@app.get("/api/map")
def get_map():
    """The static city: everything the browser needs to draw the map."""
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


@app.get("/api/state")
def get_state():
    return dispatcher.snapshot()


@app.get("/api/route")
def get_route(start: int, goal: int):
    try:
        route = shortest_path(dispatcher.graph, start, goal)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if route is None:
        return {"found": False}
    return {"found": True, "nodes": route.nodes, "edge_ids": route.edge_ids,
            "cost": route.cost}


# ---------------------------------------------------------------- actions
@app.post("/api/requests")
async def submit_request(data: Dict[str, Any] = Body(...)):
    try:
        req = dispatcher.submit(data)
    except RequestError as err:
        return JSONResponse(status_code=422, content={"errors": err.errors})
    await broadcast()
    return {"id": req.id}


@app.post("/api/requests/sample")
async def load_sample():
    if not REQUESTS_PATH.exists():
        raise HTTPException(status_code=404, detail="No sample requests file for this dataset")
    accepted, errors = dispatcher.load_requests_file(REQUESTS_PATH)
    await broadcast()
    return {"accepted": accepted, "errors": errors}


@app.post("/api/clock")
async def advance_clock(minutes: float = Body(..., embed=True)):
    try:
        dispatcher.advance(minutes)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@app.post("/api/edges/{edge_id}/close")
async def close_edge(edge_id: int):
    try:
        dispatcher.close_road(edge_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))
    return await respond()


@app.post("/api/edges/{edge_id}/open")
async def open_edge(edge_id: int):
    try:
        dispatcher.open_road(edge_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))
    return await respond()


@app.post("/api/trucks/{truck_id}/breakdown")
async def breakdown(truck_id: str):
    try:
        dispatcher.break_down(truck_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@app.post("/api/trucks/{truck_id}/repair")
async def repair(truck_id: str):
    try:
        dispatcher.repair(truck_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return await respond()


@app.post("/api/reset")
async def reset():
    global dispatcher
    dispatcher = new_dispatcher()
    return await respond()
