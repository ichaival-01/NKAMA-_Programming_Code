#=======================================================================================================================
import json
from dataclasses import asdict, dataclass, field

from engine import events as ev
from engine import states as st
from engine.chaos import Chaos
from engine.crisis import RequestError, validate_request
from engine.events import EventBus
from engine.graph import Graph
from engine.matcher import Assignment, ResourcePool, find_match
from engine.priority_queue import CrisisQueue
from engine.router import shortest_path
from engine.scoring import load_config, score_request

#Imports the tools from every other engine file that the dispatcher ties together
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Trip:
    """A truck's journey: current spot -> hub -> site, with a time for each node."""
    truck_id: str
    nodes: list            # every node on the journey, in order
    edge_ids: list         # roads between them
    node_times: list       # simulation time the truck reaches each node
    hub_index: int         # position of the hub in `nodes` (-1 once already loaded)
    late_flagged: bool = False
    fuel_charged: int = 0  # how many roads of this trip have been paid for in fuel

#A truck's journey (start -> hub -> site) with the time it reaches each node, plus flags for lateness and how many
#roads have already been charged for fuel
#=======================================================================================================================

#=======================================================================================================================
    @property
    def arrival(self):
        return self.node_times[-1]

#Time the truck reaches the site (the last arrival time on the trip)
#=======================================================================================================================

#=======================================================================================================================
    def index_at(self, now):
        """Index of the last node the truck has reached by time `now`."""
        i = 0
        for j, t in enumerate(self.node_times):
            if t <= now + 1e-9:
                i = j
        return i

#Finds the last node the truck has passed by `now` by scanning the arrival times in order
#=======================================================================================================================

#=======================================================================================================================
    def remaining_edges(self, now):
        return self.edge_ids[self.index_at(now):]

#Lists the roads still ahead of the truck
#=======================================================================================================================

#=======================================================================================================================
    def has_loaded(self, now):
        return self.hub_index < 0 or self.index_at(now) >= self.hub_index

#True once the truck has passed the hub and picked up its food
#=======================================================================================================================

#=======================================================================================================================
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

#Joins the hub leg and site leg into one Trip, adding each road's travel time to get the arrival time at every node
#=======================================================================================================================

#=======================================================================================================================
class Dispatcher:
    def __init__(self, dataset, config=None, bus=None):
        self.dataset = dataset
        self.config = config or load_config()
        self.bus = bus or EventBus()
        self.graph = Graph(dataset)
        self.fuel_config = self.config.get("fuel", {})
        self.pool = ResourcePool(dataset, self.fuel_config.get("per_km", 0.0))
        self.chaos = Chaos(self.config.get("chaos_seed"))
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

#Runs the whole system on a simulation clock: intake, triage, matching, routing and tracking. Initialization sets up
#the road graph, truck/food pool, priority queue, clock, chaos and the records kept per request
#=======================================================================================================================

    # ------------------------------------------------------------ helpers

#=======================================================================================================================
    def _publish(self, event_type, message, **data):
        return self.bus.publish(event_type, self.now, message, **data)

#Sends an event on the bus, stamped with the current simulation time
#=======================================================================================================================

#=======================================================================================================================
    def _move(self, rid, new_state, reason):
        tracker = self.trackers[rid]
        old = tracker.state
        tracker.move_to(new_state, self.now, reason)
        self._publish(ev.STATE_CHANGED, f"{rid}: {old} → {new_state}. {reason}",
                      request_id=rid, from_state=old, to_state=new_state, reason=reason)

#Changes a request's state with a reason and announces the change on the bus
#=======================================================================================================================

#=======================================================================================================================
    @staticmethod
    def _by(by):
        return f" by {by}" if by else ""

#Adds ' by <name>' to messages so the feed shows who did something (nothing if no name)
#=======================================================================================================================

#=======================================================================================================================
    def _node_name(self, node_id):
        return self.dataset.nodes[node_id].name

#Readable name of an intersection
#=======================================================================================================================

#=======================================================================================================================
    def _truck_name(self, truck_id):
        return self.dataset.trucks[truck_id].name

#Readable name of a truck
#=======================================================================================================================

#=======================================================================================================================
    def _estimate_travel(self, req):
        """Minutes from the nearest hub to the site, or None if unreachable."""
        best = None
        for hub_id in self.dataset.hubs:
            route = shortest_path(self.graph, self.dataset.pois[hub_id].node, req.location)
            if route is not None and (best is None or route.cost < best):
                best = route.cost
        return best

#Shortest drive (A*) from any hub to the site, used for the deadline score. None if unreachable
#=======================================================================================================================

#=======================================================================================================================
    def _score(self, rid):
        score = score_request(self.requests[rid], self.now, self.travel[rid], self.config)
        self.scores[rid] = score
        return score

#Recalculates and stores a request's urgency score
#=======================================================================================================================

#=======================================================================================================================
    def _explain(self, score):
        parts = ", ".join(f"{k} {v:.1f}" for k, v in score.parts.items())
        return f"Scored {score.total:.1f}/100 ({parts})"

#Turns a score breakdown into text for the request's timeline
#=======================================================================================================================

    # ------------------------------------------------------------ goal 1: intake

#=======================================================================================================================
    def submit(self, data):
        """Validate and accept one request. Returns the CrisisRequest."""
        req = self._accept(data)
        if req.submitted_at <= self.now:
            self._activate(req)
            self.dispatch()
        return req

#Takes one request; activates it now unless it's scheduled for later, then runs dispatch
#=======================================================================================================================

#=======================================================================================================================
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

#Takes a batch of requests, collecting errors for bad ones instead of stopping at the first
#=======================================================================================================================

#=======================================================================================================================
    def load_requests_file(self, path):
        with open(path) as f:
            raw = json.load(f)
        items = raw.get("requests", []) if isinstance(raw, dict) else raw
        return self.load_requests(items)

#Reads requests from a JSON file (either a plain list or {"requests": [...]})
#=======================================================================================================================

#=======================================================================================================================
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

#Gives a request an id and submit time if missing, rejects duplicate ids, validates it, and holds requests submitted
#for a future time
#=======================================================================================================================

#=======================================================================================================================
    def _activate(self, req):
        rid = req.id
        self.requests[rid] = req
        self.trackers[rid] = st.CrisisTracker(rid)
        self.trackers[rid].record_open(
            self.now, f"Request received (submitted at {req.submitted_at:.1f} min) "
                      f"and passed validation")
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

#Opens a tracker, estimates travel, scores the request, adds it to the queue and marks it Prioritized
#=======================================================================================================================

    # ------------------------------------------------------------ goals 2-4: dispatch

#=======================================================================================================================
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
                if result.kind == "fuel":
                    self._refuel_for(req)
        for rid in deferred:
            self.queue.push(rid, self.scores[rid].total, self.requests[rid].submitted_at)
        self._last_order = self.queue.ordered()

#Greedy matching: pops the most urgent request first and gives it the best truck and food, or marks it Delayed
#(refuelling trucks if fuel is the problem). Unmatched requests go back in the queue
#=======================================================================================================================

#=======================================================================================================================
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

#Reserves the truck and food, builds the trip, and marks the request Resource Assigned then In Progress
#=======================================================================================================================

#=======================================================================================================================
    def _check_stock(self, hub_id, stock_type):
        left = self.pool.stock[hub_id][stock_type]
        key = (hub_id, stock_type)
        if left < self.config["low_stock_threshold"] and key not in self._warned_stock:
            self._warned_stock.add(key)
            self._publish(ev.RESOURCE_DEPLETED,
                          f"{self.dataset.pois[hub_id].name} is low on {stock_type} "
                          f"food: {left} units left", hub_id=hub_id,
                          stock_type=stock_type, left=left)

#Warns once when a hub's stock of a food type drops below the low-stock threshold
#=======================================================================================================================

#=======================================================================================================================
    def _flag_late(self, rid):
        trip = self.trips.get(rid)
        req = self.requests[rid]
        if trip and not trip.late_flagged and trip.arrival > req.deadline:
            if self.trackers[rid].state == st.IN_PROGRESS:
                trip.late_flagged = True
                self._move(rid, st.DELAYED,
                           f"Expected {trip.arrival - req.deadline:.1f} min after the deadline")

#Marks a request Delayed (once) if its truck will arrive after the deadline
#=======================================================================================================================

    # ------------------------------------------------------------ goal 5: time passes

#=======================================================================================================================
    def advance(self, minutes):
        """Move the simulation clock forward, handling everything that happens."""
        if minutes < 0:
            raise ValueError("Can't move the clock backwards")
        target = self.now + minutes
        self.chaos.plan(self.now, target)
        while True:
            due = [t.arrival for t in self.trips.values() if t.arrival <= target]
            due += [r.submitted_at for r in self.scheduled if r.submitted_at <= target]
            due += [t for t in self.pool.refuelling.values() if t <= target]
            due += self.chaos.due_times(target)
            due = [t for t in due if t > self.now - 1e-9]
            if not due:
                break
            self.now = max(self.now, min(due))
            self.chaos.run_due(self)
            self._tick()
        self.now = target
        self.chaos.run_due(self)
        self._tick()

#Moves the clock forward, stopping at every arrival, scheduled request, refuel or chaos event on the way so each one
#happens at the right time
#=======================================================================================================================

#=======================================================================================================================
    def _tick(self):
        self._finish_refuelling()
        for trip in self.trips.values():
            self._burn_fuel(trip)
        self._deliver_due()
        self._release_scheduled()
        for rid in list(self.trips):
            self._flag_late(rid)
        self._fail_overdue()
        self._rescore()
        self.dispatch()

#One clock step: finish refuels, burn fuel, deliver, release scheduled requests, check late/overdue requests, rescore
#and dispatch
#=======================================================================================================================

#=======================================================================================================================
    def _deliver_due(self):
        for rid, trip in list(self.trips.items()):
            if trip.arrival > self.now + 1e-9:
                continue
            del self.trips[rid]
            self._burn_fuel(trip)
            a = self.assignments[rid]
            req = self.requests[rid]
            self.pool.complete(a, req.location)
            self._maybe_refuel(a.truck_id)
            late = trip.arrival - req.deadline
            timing = "on time" if late <= 0 else f"{late:.1f} min late"
            self._move(rid, st.DELIVERED,
                       f"{self._truck_name(a.truck_id)} delivered {a.amount} units "
                       f"at {trip.arrival:.1f} min ({timing})")
            self._move(rid, st.RESOLVED,
                       f"Delivery confirmed for {req.people_affected} people")
            self._publish(ev.CRISIS_RESOLVED, f"{rid} resolved ({timing})",
                          request_id=rid, late_by=max(0.0, late))

#Finishes trips that have arrived: frees the truck, marks Delivered then Resolved, refuels if the tank is low
#=======================================================================================================================

#=======================================================================================================================
    def _release_scheduled(self):
        due = [r for r in self.scheduled if r.submitted_at <= self.now + 1e-9]
        self.scheduled = [r for r in self.scheduled if r not in due]
        for req in sorted(due, key=lambda r: r.submitted_at):
            self._activate(req)

#Activates scheduled requests whose submit time has come, in time order
#=======================================================================================================================

#=======================================================================================================================
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

#Marks requests Failed when they're still unserved (queued or stuck) past the deadline plus the grace period
#=======================================================================================================================

#=======================================================================================================================
    def _rescore(self):
        for rid in self.queue.ordered():
            self.travel[rid] = self._estimate_travel(self.requests[rid])
        self.queue.rescore_all(lambda rid: self._score(rid).total)
        order = self.queue.ordered()
        if order != self._last_order and len(order) > 1:
            self._publish(ev.PRIORITY_CHANGED, "Queue order changed: " + ", ".join(order),
                          order=order)
        self._last_order = order

#Re-estimates travel and re-scores the whole queue; announces if the order changed
#=======================================================================================================================

    # ------------------------------------------------------------ disruptions

#=======================================================================================================================
    def close_road(self, edge_id, by=None):
        if edge_id in self.graph.closed_edges:
            return
        self.graph.close_edge(edge_id)                    # raises for unknown roads
        edge = self.graph.edges[edge_id]
        self._publish(ev.ROAD_CLOSED, f"Road closed: {edge.street or 'road'} "
                      f"(edge {edge_id}){self._by(by)}", edge_id=edge_id, by=by)
        for rid, trip in list(self.trips.items()):
            if edge_id in trip.remaining_edges(self.now):
                self._reroute(rid, edge_id)
        self._rescore()
        self.dispatch()

#Closes a road, re-routes any truck that needed it, then rescores and dispatches
#=======================================================================================================================

#=======================================================================================================================
    def open_road(self, edge_id, by=None):
        if edge_id not in self.graph.edges:
            raise ValueError(f"Unknown edge: {edge_id}")
        if edge_id not in self.graph.closed_edges:
            return
        self.graph.open_edge(edge_id)
        edge = self.graph.edges[edge_id]
        self._publish(ev.ROAD_OPENED, f"Road reopened: {edge.street or 'road'} "
                      f"(edge {edge_id}){self._by(by)}", edge_id=edge_id, by=by)
        for rid in list(self.stuck):
            self._resume(rid)
        self._rescore()
        self.dispatch()

#Reopens a road, resumes stuck trucks, then rescores and dispatches
#=======================================================================================================================

#=======================================================================================================================
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

#Plans A* routes to finish the job from where the truck is (via the hub if it hasn't loaded yet). None if impossible
#=======================================================================================================================

#=======================================================================================================================
    def _reroute(self, rid, edge_id):
        trip = self.trips.pop(rid)
        self._burn_fuel(trip)
        here = trip.nodes[trip.index_at(self.now)]
        loaded = trip.has_loaded(self.now)
        a = self.assignments[rid]
        street = self.graph.edges[edge_id].street or f"edge {edge_id}"
        self._move(rid, st.RE_ROUTING,
                   f"{street} closed ahead of {self._truck_name(a.truck_id)} "
                   f"near {self._node_name(here)}")
        self._continue_from(rid, here, loaded, old_arrival=trip.arrival)

#Marks a request Re-Routing from the truck's current spot and plans the rest of the trip
#=======================================================================================================================

#=======================================================================================================================
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

#Starts the new trip, or marks Delayed and waits if no open route exists
#=======================================================================================================================

#=======================================================================================================================
    def _resume(self, rid):
        here, loaded = self.stuck[rid]
        if self._new_legs(rid, here, loaded) is None:
            return
        del self.stuck[rid]
        self._continue_from(rid, here, loaded)

#Restarts a stuck truck once a route exists again
#=======================================================================================================================

#=======================================================================================================================
    def break_down(self, truck_id, by=None):
        if truck_id not in self.dataset.trucks:
            raise ValueError(f"Unknown truck: {truck_id}")
        if truck_id in self.pool.broken:
            raise ValueError(f"{truck_id} is already broken down")
        rid = self.pool.busy.get(truck_id)
        here = self.pool.truck_location[truck_id]
        loaded = False
        if rid in self.trips:
            trip = self.trips.pop(rid)
            self._burn_fuel(trip)
            here = trip.nodes[trip.index_at(self.now)]
            loaded = trip.has_loaded(self.now)
        elif rid in self.stuck:
            here, loaded = self.stuck.pop(rid)
        self.pool.break_down(truck_id, here)
        self._publish(ev.VEHICLE_BREAKDOWN,
                      f"{self._truck_name(truck_id)} broke down at "
                      f"{self._node_name(here)}{self._by(by)}",
                      truck_id=truck_id, node=here, by=by)
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

#Takes a truck out of service; returns its food to the hub (or writes off a loaded truck's food) and puts its request
#back in the queue
#=======================================================================================================================

#=======================================================================================================================
    def repair(self, truck_id, by=None):
        if truck_id not in self.dataset.trucks:
            raise ValueError(f"Unknown truck: {truck_id}")
        if truck_id in self.pool.broken:
            self.pool.repair(truck_id)
            self._publish(ev.VEHICLE_READY,
                          f"{self._truck_name(truck_id)} repaired{self._by(by)}",
                          truck_id=truck_id, by=by)
        self.dispatch()

#Puts a broken truck back in service and runs dispatch
#=======================================================================================================================

    # ------------------------------------------------------------ fuel

#=======================================================================================================================
    def _burn_fuel(self, trip):
        """Charge fuel for the roads this trip has driven since the last charge."""
        done = trip.index_at(self.now)
        km = sum(self.graph.edges[e].km for e in trip.edge_ids[trip.fuel_charged:done])
        trip.fuel_charged = max(trip.fuel_charged, done)
        tid = trip.truck_id
        self.pool.fuel[tid] = max(0.0, self.pool.fuel[tid] - self.pool.fuel_needed(km))

#Charges fuel for the roads driven since the last charge (km x fuel per km)
#=======================================================================================================================

#=======================================================================================================================
    def _start_refuel(self, truck_id, why):
        minutes = self.fuel_config.get("refuel_minutes", 10)
        ready = self.now + minutes
        self.pool.refuelling[truck_id] = ready
        self._publish(ev.VEHICLE_REFUELLING,
                      f"{self._truck_name(truck_id)} is refuelling ({why}); "
                      f"back at {ready:.1f} min",
                      truck_id=truck_id, ready_at=ready,
                      fuel=round(self.pool.fuel[truck_id], 1))

#Takes a truck out of service for refuel_minutes and announces it
#=======================================================================================================================

#=======================================================================================================================
    def _maybe_refuel(self, truck_id):
        """After a delivery, refuel if the tank is below the reserve."""
        if self.pool.fuel_per_km <= 0:
            return
        if self.pool.fuel[truck_id] < self.fuel_config.get("reserve", 25):
            self._start_refuel(truck_id, f"{self.pool.fuel[truck_id]:.0f} fuel left")

#After a delivery, refuels the truck if its tank is below the reserve
#=======================================================================================================================

#=======================================================================================================================
    def _refuel_for(self, req):
        """No truck can afford this trip: refuel the idle trucks that would suit it."""
        for tid, truck in self.dataset.trucks.items():
            if (self.pool.is_free(tid) and truck.capacity >= req.food_needed
                    and (truck.fridge or not req.needs_refrigeration)
                    and self.pool.fuel[tid] < self.pool.max_fuel[tid]):
                self._start_refuel(tid, f"needed for {req.id}")

#When no truck has enough fuel for a trip, refuels the idle trucks that would suit it
#=======================================================================================================================

#=======================================================================================================================
    def _finish_refuelling(self):
        for tid, ready in list(self.pool.refuelling.items()):
            if ready <= self.now + 1e-9:
                del self.pool.refuelling[tid]
                self.pool.fuel[tid] = self.pool.max_fuel[tid]
                self._publish(ev.VEHICLE_READY,
                              f"{self._truck_name(tid)} refuelled and ready",
                              truck_id=tid)

#Fills the tank and frees trucks whose refuel time is up
#=======================================================================================================================

    # ------------------------------------------------------------ chaos

#=======================================================================================================================
    def set_chaos(self, enabled, level=None, by=None):
        self.chaos.set(enabled, level)
        state = f"on ({self.chaos.level})" if enabled else "off"
        self._publish(ev.CHAOS_TOGGLED, f"Chaos mode {state}{self._by(by)}",
                      enabled=enabled, level=self.chaos.level, by=by)

#Turns chaos mode on/off (and sets its level) and announces who did it
#=======================================================================================================================

#=======================================================================================================================
    def trigger_chaos(self):
        return self.chaos.trigger(self)

#Causes one random incident right now (the Trigger incident button)
#=======================================================================================================================

    # ------------------------------------------------------------ for the dashboard

#=======================================================================================================================
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

#Truck's map position, sliding smoothly between nodes based on time elapsed on the current road
#=======================================================================================================================

#=======================================================================================================================
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
                # True when roads make delivery impossible right now
                "route_unavailable": (rid in self.stuck or
                                      "route" in self.waiting.get(rid, "").lower()),
            })
        trucks = []
        for tid, t in ds.trucks.items():
            lat, lng = self.truck_position(tid)
            status = ("broken" if tid in self.pool.broken
                      else "refuelling" if tid in self.pool.refuelling
                      else "busy" if tid in self.pool.busy else "free")
            trucks.append({
                "id": tid, "name": t.name, "fridge": t.fridge, "capacity": t.capacity,
                "status": status, "request_id": self.pool.busy.get(tid),
                "lat": lat, "lng": lng,
                "fuel": round(self.pool.fuel[tid], 1),
                "max_fuel": self.pool.max_fuel[tid],
                "ready_at": self.pool.refuelling.get(tid),
            })
        return {
            "now": round(self.now, 2),
            "queue": queue,
            "crises": crises,
            "trucks": trucks,
            "stock": [{"hub_id": h, "name": ds.pois[h].name, **s}
                      for h, s in self.pool.stock.items()],
            "closed_edges": sorted(self.graph.closed_edges),
            "chaos": self.chaos.status(),
            "fuel_on": self.pool.fuel_per_km > 0,
            "scheduled": [{"id": r.id, "submitted_at": r.submitted_at,
                           "location_name": ds.nodes[r.location].name}
                          for r in sorted(self.scheduled, key=lambda r: r.submitted_at)],
            "events": [asdict(e) for e in self.bus.history[-60:]],
        }

#Everything the dashboard shows (queue, crises, trucks, stock, closures, chaos, events) packed into a dict
#=======================================================================================================================