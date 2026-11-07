"""The dispatcher: ties every goal together on a simulation clock.

    submit()  -> validate (goal 1) -> score + queue (goal 2)
    dispatch() -> pop most urgent -> greedy match (goal 3) -> route (goal 4)
    advance() -> trucks drive; deliveries, lateness and failures (goal 5)
    close_road() / open_road() / break_down() -> disruptions and re-routing

Every state change and notable event is published on the event bus, which
the website forwards to the browser live.

Simplifications worth knowing (and saying out loud to judges):
- A truck's position is tracked node by node. If a road closes while a truck
  is partway along a road, it re-routes from the last intersection it passed.
- One truck per request (no splitting loads across trucks).
- If a truck breaks down before reaching the hub, its reserved food goes back
  into the hub's stock. If it already loaded the food, that load is written
  off and the replacement truck collects fresh stock.
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

import json
from dataclasses import asdict, dataclass, field

from engine import events as ev
from engine import states as st
from engine.crisis import RequestError, validate_request
from engine.events import EventBus
from engine.graph import Graph
from engine.matcher import Assignment, ResourcePool, find_match
from engine.priority_queue import CrisisQueue
from engine.router import shortest_path
from engine.scoring import load_config, score_request


@dataclass
class Trip:
    """A truck's journey: current spot -> hub -> site, with a time for each node."""
    truck_id: str
    nodes: list            # every node on the journey, in order
    edge_ids: list         # roads between them
    node_times: list       # simulation time the truck reaches each node
    hub_index: int         # position of the hub in `nodes` (-1 once already loaded)
    late_flagged: bool = False

    @property
    def arrival(self):
        return self.node_times[-1]

    def index_at(self, now):
        """Index of the last node the truck has reached by time `now`."""
        i = 0
        for j, t in enumerate(self.node_times):
            if t <= now + 1e-9:
                i = j
        return i

    def remaining_edges(self, now):
        return self.edge_ids[self.index_at(now):]

    def has_loaded(self, now):
        return self.hub_index < 0 or self.index_at(now) >= self.hub_index


def _make_trip(truck_id, start_time, graph, to_hub=None, to_site=None):
    """Join one or two Routes into a Trip with arrival times at each node."""
    nodes, edge_ids, hub_index = [], [], -1
    if to_hub is not None:
        nodes += to_hub.nodes
        edge_ids += to_hub.edge_ids
        hub_index = len(nodes) - 1
        nodes += to_site.nodes[1:]
    else:
        nodes += to_site.nodes
    edge_ids += to_site.edge_ids
    times = [start_time]
    for eid in edge_ids:
        times.append(times[-1] + graph.edges[eid].cost)
    return Trip(truck_id, nodes, edge_ids, times, hub_index)


class Dispatcher:
    def __init__(self, dataset, config=None, bus=None):
        self.dataset = dataset
        self.config = config or load_config()
        self.bus = bus or EventBus()
        self.graph = Graph(dataset)
        self.pool = ResourcePool(dataset)
        self.queue = CrisisQueue(self.config["aging_rate"])
        self.now = 0.0

        self.requests = {}        # id -> CrisisRequest (active or finished)
        self.trackers = {}        # id -> CrisisTracker
        self.scores = {}          # id -> latest Score
        self.travel = {}          # id -> estimated minutes from the nearest hub
        self.assignments = {}     # id -> Assignment (current or last)
        self.trips = {}           # id -> Trip, for trucks on the road
        self.stuck = {}           # id -> node where a truck is waiting for roads
        self.waiting = {}         # id -> why it hasn't been assigned yet
        self.scheduled = []       # requests submitted for a future time
        self._warned_stock = set()
        self._last_order = []
        self._next_number = 1

    # ------------------------------------------------------------ helpers
    def _publish(self, event_type, message, **data):
        return self.bus.publish(event_type, self.now, message, **data)

    def _move(self, rid, new_state, reason):
        tracker = self.trackers[rid]
        old = tracker.state
        tracker.move_to(new_state, self.now, reason)
        self._publish(ev.STATE_CHANGED, f"{rid}: {old} → {new_state}. {reason}",
                      request_id=rid, from_state=old, to_state=new_state, reason=reason)

    def _node_name(self, node_id):
        return self.dataset.nodes[node_id].name

    def _truck_name(self, truck_id):
        return self.dataset.trucks[truck_id].name

    def _estimate_travel(self, req):
        """Minutes from the nearest hub to the site, or None if unreachable."""
        best = None
        for hub_id in self.dataset.hubs:
            route = shortest_path(self.graph, self.dataset.pois[hub_id].node, req.location)
            if route is not None and (best is None or route.cost < best):
                best = route.cost
        return best

    def _score(self, rid):
        score = score_request(self.requests[rid], self.now, self.travel[rid], self.config)
        self.scores[rid] = score
        return score

    def _explain(self, score):
        parts = ", ".join(f"{k} {v:.1f}" for k, v in score.parts.items())
        return f"Scored {score.total:.1f}/100 ({parts})"

    # ------------------------------------------------------------ goal 1: intake
    def submit(self, data):
        """Validate and accept one request. Returns the CrisisRequest."""
        req = self._accept(data)
        if req.submitted_at <= self.now:
            self._activate(req)
            self.dispatch()
        return req

    def load_requests(self, items):
        """Accept many requests (e.g. from a file). Returns (accepted ids, errors)."""
        accepted, errors = [], {}
        for i, data in enumerate(items):
            try:
                req = self._accept(data)
            except RequestError as err:
                errors[str(data.get("id", f"#{i + 1}"))] = err.errors
                continue
            accepted.append(req.id)
            if req.submitted_at <= self.now:
                self._activate(req)
        self.dispatch()
        return accepted, errors

    def load_requests_file(self, path):
        with open(path) as f:
            raw = json.load(f)
        items = raw.get("requests", []) if isinstance(raw, dict) else raw
        return self.load_requests(items)

    def _accept(self, data):
        data = dict(data)
        if not data.get("id"):
            while f"R{self._next_number}" in self.requests or any(
                    r.id == f"R{self._next_number}" for r in self.scheduled):
                self._next_number += 1
            data["id"] = f"R{self._next_number}"
        if data.get("submitted_at") is None:
            data["submitted_at"] = self.now
        if data["id"] in self.requests or any(r.id == data["id"] for r in self.scheduled):
            raise RequestError([f"id {data['id']} is already used"])
        req = validate_request(data, self.dataset)
        if req.submitted_at > self.now:
            self.scheduled.append(req)
        return req

    def _activate(self, req):
        rid = req.id
        self.requests[rid] = req
        self.trackers[rid] = st.CrisisTracker(rid)
        self._publish(ev.CRISIS_CREATED,
                      f"{rid}: {req.crisis_type} at {self._node_name(req.location)}, "
                      f"{req.people_affected} people, {req.food_needed} units"
                      f"{' (refrigerated)' if req.needs_refrigeration else ''}",
                      request_id=rid)
        self.travel[rid] = self._estimate_travel(req)
        score = self._score(rid)
        self.queue.push(rid, score.total, req.submitted_at)
        self._move(rid, st.PRIORITIZED, self._explain(score))
        self._publish(ev.PRIORITY_CHANGED, f"{rid} queued with score {score.total:.1f}",
                      request_id=rid, score=score.total, order=self.queue.ordered())

    # ------------------------------------------------------------ goals 2-4: dispatch
    def dispatch(self):
        """Greedy: most urgent first, give each the best available truck and food."""
        deferred = []
        while True:
            rid = self.queue.pop()
            if rid is None:
                break
            req = self.requests[rid]
            result = find_match(req, self.pool, self.graph, self.now)
            if isinstance(result, Assignment):
                self._start(rid, result)
            else:
                deferred.append(rid)
                first_time = self.waiting.get(rid) != result.reason
                self.waiting[rid] = result.reason
                if self.trackers[rid].state != st.DELAYED:
                    self._move(rid, st.DELAYED, f"Waiting: {result.reason}")
                if first_time and result.kind == "stock":
                    self._publish(ev.RESOURCE_DEPLETED, f"{rid}: {result.reason}",
                                  request_id=rid)
        for rid in deferred:
            self.queue.push(rid, self.scores[rid].total, self.requests[rid].submitted_at)
        self._last_order = self.queue.ordered()

    def _start(self, rid, a):
        self.pool.reserve(a)
        self.assignments[rid] = a
        self.waiting.pop(rid, None)
        self._move(rid, st.RESOURCE_ASSIGNED, a.reason)
        trip = _make_trip(a.truck_id, self.now, self.graph, a.to_hub, a.to_site)
        self.trips[rid] = trip
        note = "" if a.on_time else " — will miss the deadline"
        self._move(rid, st.IN_PROGRESS,
                   f"{self._truck_name(a.truck_id)} departed; arrives at "
                   f"{trip.arrival:.1f} min{note}")
        self._publish(ev.ROUTE_UPDATED,
                      f"{rid}: {self._truck_name(a.truck_id)} on its way, "
                      f"{a.travel_time:.1f} min", request_id=rid, nodes=trip.nodes)
        self._check_stock(a.hub_id, a.stock_type)
        self._flag_late(rid)

    def _check_stock(self, hub_id, stock_type):
        left = self.pool.stock[hub_id][stock_type]
        key = (hub_id, stock_type)
        if left < self.config["low_stock_threshold"] and key not in self._warned_stock:
            self._warned_stock.add(key)
            self._publish(ev.RESOURCE_DEPLETED,
                          f"{self.dataset.pois[hub_id].name} is low on {stock_type} "
                          f"food: {left} units left", hub_id=hub_id,
                          stock_type=stock_type, left=left)

    def _flag_late(self, rid):
        trip = self.trips.get(rid)
        req = self.requests[rid]
        if trip and not trip.late_flagged and trip.arrival > req.deadline:
            if self.trackers[rid].state == st.IN_PROGRESS:
                trip.late_flagged = True
                self._move(rid, st.DELAYED,
                           f"Expected {trip.arrival - req.deadline:.1f} min after the deadline")

    # ------------------------------------------------------------ goal 5: time passes
    def advance(self, minutes):
        """Move the simulation clock forward, handling everything that happens."""
        if minutes < 0:
            raise ValueError("Can't move the clock backwards")
        target = self.now + minutes
        while True:
            due = [t.arrival for t in self.trips.values() if t.arrival <= target]
            due += [r.submitted_at for r in self.scheduled if r.submitted_at <= target]
            due = [t for t in due if t > self.now - 1e-9]
            if not due:
                break
            self.now = max(self.now, min(due))
            self._tick()
        self.now = target
        self._tick()

    def _tick(self):
        self._deliver_due()
        self._release_scheduled()
        for rid in list(self.trips):
            self._flag_late(rid)
        self._fail_overdue()
        self._rescore()
        self.dispatch()

    def _deliver_due(self):
        for rid, trip in list(self.trips.items()):
            if trip.arrival > self.now + 1e-9:
                continue
            del self.trips[rid]
            a = self.assignments[rid]
            req = self.requests[rid]
            self.pool.complete(a, req.location)
            late = trip.arrival - req.deadline
            timing = "on time" if late <= 0 else f"{late:.1f} min late"
            self._move(rid, st.DELIVERED,
                       f"{self._truck_name(a.truck_id)} delivered {a.amount} units "
                       f"at {trip.arrival:.1f} min ({timing})")
            self._move(rid, st.RESOLVED,
                       f"Delivery confirmed for {req.people_affected} people")
            self._publish(ev.CRISIS_RESOLVED, f"{rid} resolved ({timing})",
                          request_id=rid, late_by=max(0.0, late))

    def _release_scheduled(self):
        due = [r for r in self.scheduled if r.submitted_at <= self.now + 1e-9]
        self.scheduled = [r for r in self.scheduled if r not in due]
        for req in sorted(due, key=lambda r: r.submitted_at):
            self._activate(req)

    def _fail_overdue(self):
        grace = self.config["failure_grace"]
        for rid in list(self.queue.ordered()):
            req = self.requests[rid]
            if self.now > req.deadline + grace:
                self.queue.remove(rid)
                reason = self.waiting.pop(rid, "no resources")
                self._move(rid, st.FAILED,
                           f"Still unassigned {self.now - req.deadline:.0f} min after "
                           f"the deadline ({reason})")
        for rid, (node, _loaded) in list(self.stuck.items()):
            req = self.requests[rid]
            if self.now > req.deadline + grace:
                del self.stuck[rid]
                a = self.assignments[rid]
                self.pool.release(a)
                self.pool.truck_location[a.truck_id] = node
                self._move(rid, st.FAILED,
                           f"{self._truck_name(a.truck_id)} couldn't reach the site "
                           f"before giving up; roads still closed")

    def _rescore(self):
        for rid in self.queue.ordered():
            self.travel[rid] = self._estimate_travel(self.requests[rid])
        self.queue.rescore_all(lambda rid: self._score(rid).total)
        order = self.queue.ordered()
        if order != self._last_order and len(order) > 1:
            self._publish(ev.PRIORITY_CHANGED, "Queue order changed: " + ", ".join(order),
                          order=order)
        self._last_order = order

    # ------------------------------------------------------------ disruptions
    def close_road(self, edge_id):
        if edge_id in self.graph.closed_edges:
            return
        self.graph.close_edge(edge_id)                    # raises for unknown roads
        edge = self.graph.edges[edge_id]
        self._publish(ev.ROAD_CLOSED, f"Road closed: {edge.street or 'road'} "
                      f"(edge {edge_id})", edge_id=edge_id)
        for rid, trip in list(self.trips.items()):
            if edge_id in trip.remaining_edges(self.now):
                self._reroute(rid, edge_id)
        self._rescore()
        self.dispatch()

    def open_road(self, edge_id):
        if edge_id not in self.graph.edges:
            raise ValueError(f"Unknown edge: {edge_id}")
        if edge_id not in self.graph.closed_edges:
            return
        self.graph.open_edge(edge_id)
        edge = self.graph.edges[edge_id]
        self._publish(ev.ROAD_OPENED, f"Road reopened: {edge.street or 'road'} "
                      f"(edge {edge_id})", edge_id=edge_id)
        for rid in list(self.stuck):
            self._resume(rid)
        self._rescore()
        self.dispatch()

    def _new_legs(self, rid, here, loaded):
        """Routes from `here` to finish the job, or None if impossible."""
        a = self.assignments[rid]
        site = self.requests[rid].location
        if loaded:
            to_site = shortest_path(self.graph, here, site)
            return None if to_site is None else (None, to_site)
        hub_node = self.dataset.pois[a.hub_id].node
        to_hub = shortest_path(self.graph, here, hub_node)
        to_site = shortest_path(self.graph, hub_node, site)
        if to_hub is None or to_site is None:
            return None
        return (to_hub, to_site)

    def _reroute(self, rid, edge_id):
        trip = self.trips.pop(rid)
        here = trip.nodes[trip.index_at(self.now)]
        loaded = trip.has_loaded(self.now)
        a = self.assignments[rid]
        street = self.graph.edges[edge_id].street or f"edge {edge_id}"
        self._move(rid, st.RE_ROUTING,
                   f"{street} closed ahead of {self._truck_name(a.truck_id)} "
                   f"near {self._node_name(here)}")
        self._continue_from(rid, here, loaded, old_arrival=trip.arrival)

    def _continue_from(self, rid, here, loaded, old_arrival=None):
        a = self.assignments[rid]
        legs = self._new_legs(rid, here, loaded)
        if legs is None:
            self.stuck[rid] = (here, loaded)
            self.pool.truck_location[a.truck_id] = here
            self._move(rid, st.DELAYED,
                       f"No open route from {self._node_name(here)}; "
                       f"waiting for a road to reopen")
            self._publish(ev.ROUTE_UPDATED, f"{rid}: no route available right now",
                          request_id=rid, nodes=[])
            return
        to_hub, to_site = legs
        trip = _make_trip(a.truck_id, self.now, self.graph, to_hub, to_site)
        self.trips[rid] = trip
        change = "" if old_arrival is None else f" ({trip.arrival - old_arrival:+.1f} min)"
        self._move(rid, st.IN_PROGRESS,
                   f"New route found; arrives at {trip.arrival:.1f} min{change}")
        self._publish(ev.ROUTE_UPDATED,
                      f"{rid}: re-routed, arrives at {trip.arrival:.1f} min{change}",
                      request_id=rid, nodes=trip.nodes)
        self._flag_late(rid)

    def _resume(self, rid):
        here, loaded = self.stuck[rid]
        if self._new_legs(rid, here, loaded) is None:
            return
        del self.stuck[rid]
        self._continue_from(rid, here, loaded)

    def break_down(self, truck_id):
        if truck_id not in self.dataset.trucks:
            raise ValueError(f"Unknown truck: {truck_id}")
        if truck_id in self.pool.broken:
            raise ValueError(f"{truck_id} is already broken down")
        rid = self.pool.busy.get(truck_id)
        here = self.pool.truck_location[truck_id]
        loaded = False
        if rid in self.trips:
            trip = self.trips.pop(rid)
            here = trip.nodes[trip.index_at(self.now)]
            loaded = trip.has_loaded(self.now)
        elif rid in self.stuck:
            here, loaded = self.stuck.pop(rid)
        self.pool.break_down(truck_id, here)
        self._publish(ev.VEHICLE_BREAKDOWN,
                      f"{self._truck_name(truck_id)} broke down at {self._node_name(here)}",
                      truck_id=truck_id, node=here)
        if rid is not None:
            a = self.assignments.pop(rid)
            if not loaded:
                self.pool.stock[a.hub_id][a.stock_type] += a.amount
            what = "load lost; needs fresh stock" if loaded else "food returned to the hub"
            self._move(rid, st.DELAYED,
                       f"{self._truck_name(truck_id)} broke down ({what}); "
                       f"finding a replacement")
            self.queue.push(rid, self._score(rid).total, self.requests[rid].submitted_at)
        self.dispatch()

    def repair(self, truck_id):
        if truck_id not in self.dataset.trucks:
            raise ValueError(f"Unknown truck: {truck_id}")
        self.pool.repair(truck_id)
        self.dispatch()

    # ------------------------------------------------------------ for the dashboard
    def truck_position(self, truck_id):
        """(lat, lng) of a truck, sliding smoothly along its current road."""
        rid = self.pool.busy.get(truck_id)
        trip = self.trips.get(rid) if rid else None
        if trip is None:
            n = self.dataset.nodes[self.pool.truck_location[truck_id]]
            return n.lat, n.lng
        i = trip.index_at(self.now)
        a = self.dataset.nodes[trip.nodes[i]]
        if i >= len(trip.nodes) - 1:
            return a.lat, a.lng
        b = self.dataset.nodes[trip.nodes[i + 1]]
        t0, t1 = trip.node_times[i], trip.node_times[i + 1]
        f = 0 if t1 <= t0 else min(1, max(0, (self.now - t0) / (t1 - t0)))
        return a.lat + (b.lat - a.lat) * f, a.lng + (b.lng - a.lng) * f

    def snapshot(self):
        ds = self.dataset
        queue = []
        for rid in self.queue.ordered():
            req = self.requests[rid]
            queue.append({
                "id": rid,
                "priority": round(self.queue.priority(rid, self.now), 2),
                "score": round(self.scores[rid].total, 2),
                "parts": {k: round(v, 2) for k, v in self.scores[rid].parts.items()},
                "aging": round(self.queue.priority(rid, self.now) - self.scores[rid].total, 2),
                "waiting": self.waiting.get(rid, ""),
                "location": ds.nodes[req.location].name,
            })
        crises = []
        for rid, req in self.requests.items():
            tracker = self.trackers[rid]
            a = self.assignments.get(rid)
            trip = self.trips.get(rid)
            crises.append({
                "request": asdict(req),
                "location_name": ds.nodes[req.location].name,
                "state": tracker.state,
                "history": [asdict(h) for h in tracker.history],
                "score": round(self.scores[rid].total, 2) if rid in self.scores else None,
                "assignment": None if a is None else {
                    "truck_id": a.truck_id, "truck": self._truck_name(a.truck_id),
                    "hub_id": a.hub_id, "hub": ds.pois[a.hub_id].name,
                    "stock_type": a.stock_type, "amount": a.amount,
                },
                "route": trip.nodes[trip.index_at(self.now):] if trip else [],
                "eta": round(trip.arrival, 2) if trip else None,
                "waiting": self.waiting.get(rid, ""),
            })
        trucks = []
        for tid, t in ds.trucks.items():
            lat, lng = self.truck_position(tid)
            status = ("broken" if tid in self.pool.broken
                      else "busy" if tid in self.pool.busy else "free")
            trucks.append({
                "id": tid, "name": t.name, "fridge": t.fridge, "capacity": t.capacity,
                "status": status, "request_id": self.pool.busy.get(tid),
                "lat": lat, "lng": lng,
            })
        return {
            "now": round(self.now, 2),
            "queue": queue,
            "crises": crises,
            "trucks": trucks,
            "stock": [{"hub_id": h, "name": ds.pois[h].name, **s}
                      for h, s in self.pool.stock.items()],
            "closed_edges": sorted(self.graph.closed_edges),
            "scheduled": len(self.scheduled),
            "events": [asdict(e) for e in self.bus.history[-60:]],
        }
