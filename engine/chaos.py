"""Chaos mode: random disruptions while the clock runs.

When switched on, every stretch of simulated time has a chance of random
incidents (a road closing, a truck breaking down, or a surprise request).
Closed roads reopen and broken trucks are repaired automatically after a
while, so the city recovers and the demo keeps going.

The dispatcher calls plan() when the clock moves and run_due() as time
passes, so incidents happen at their own moments, not all at once.
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

import random

LEVELS = {"low": 4, "medium": 10, "high": 20}     # incidents per simulated hour
KINDS = [("road", 5), ("breakdown", 3), ("request", 2)]
CRISIS_TYPES = ["flash flood", "building fire", "power outage", "gas leak",
                "water main break", "heat wave"]


class Chaos:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.enabled = False
        self.level = "medium"
        self.incidents = []       # times a random incident will happen
        self.recoveries = []      # (time, "reopen" | "repair", id)

    def set(self, enabled, level=None):
        if level is not None:
            if level not in LEVELS:
                raise ValueError(f"Level must be one of {', '.join(LEVELS)}")
            self.level = level
        self.enabled = enabled
        if not enabled:
            self.incidents = []   # cancel future incidents; recoveries still happen

    def plan(self, start, end):
        """Decide when incidents will happen between two clock times."""
        if not self.enabled or end <= start:
            return
        expected = LEVELS[self.level] * (end - start) / 60
        count = int(expected) + (1 if self.rng.random() < expected % 1 else 0)
        self.incidents += [self.rng.uniform(start, end) for _ in range(count)]

    def due_times(self, end):
        return [t for t in self.incidents if t <= end] + \
               [t for t, _, _ in self.recoveries if t <= end]

    def run_due(self, d):
        """Carry out everything scheduled up to the dispatcher's current time."""
        now = d.now + 1e-9
        for t, action, target in sorted([r for r in self.recoveries if r[0] <= now]):
            self.recoveries.remove((t, action, target))
            if action == "reopen" and target in d.graph.closed_edges:
                d.open_road(target, by="Chaos (recovered)")
            elif action == "repair" and target in d.pool.broken:
                d.repair(target, by="Chaos (recovered)")
        due = [t for t in self.incidents if t <= now]
        self.incidents = [t for t in self.incidents if t > now]
        for _ in due:
            self.trigger(d)

    def trigger(self, d, kind=None):
        """One random incident right now. Returns a short description."""
        if kind is None:
            kind = self.rng.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
        if kind == "breakdown":
            done = self._breakdown(d)
            if done:
                return done
            kind = "road"                     # nothing to break: close a road instead
        if kind == "road":
            done = self._close_road(d)
            if done:
                return done
            kind = "request"
        return self._surprise_request(d)

    # ---------------------------------------------------------------- incident types
    def _close_road(self, d):
        open_roads = [e for e in d.graph.edges if e not in d.graph.closed_edges]
        if len(d.graph.closed_edges) >= max(1, len(d.graph.edges) // 7) or not open_roads:
            return None                       # keep the city drivable
        # Often hit a road a truck is about to use, so re-routing is visible
        on_routes = [e for t in d.trips.values() for e in t.remaining_edges(d.now)
                     if e not in d.graph.closed_edges]
        pool = on_routes if on_routes and self.rng.random() < 0.6 else open_roads
        edge_id = self.rng.choice(pool)
        d.close_road(edge_id, by="Chaos")
        self.recoveries.append((d.now + self.rng.uniform(10, 40), "reopen", edge_id))
        return f"Closed road {edge_id}"

    def _breakdown(self, d):
        healthy = [t for t in d.dataset.trucks if t not in d.pool.broken]
        if len(healthy) <= 2:
            return None                       # always leave a couple of trucks
        busy = [t for t in healthy if t in d.pool.busy]
        pool = busy if busy and self.rng.random() < 0.6 else healthy
        truck_id = self.rng.choice(pool)
        d.break_down(truck_id, by="Chaos")
        self.recoveries.append((d.now + self.rng.uniform(15, 45), "repair", truck_id))
        return f"Broke down {truck_id}"

    def _surprise_request(self, d):
        ds = d.dataset
        hub_nodes = {ds.pois[h].node for h in ds.hubs}
        places = [p for p in ds.pois.values() if p.kind != "hub"]
        if places and self.rng.random() < 0.6:
            place = self.rng.choice(places)
            node, category = place.node, place.kind
        else:
            node = self.rng.choice([n for n in ds.nodes if n not in hub_nodes] or list(ds.nodes))
            category = self.rng.choice(["community", "shelter"])
        req = d.submit({
            "location": node,
            "crisis_type": self.rng.choice(CRISIS_TYPES),
            "location_category": category,
            "people_affected": self.rng.randint(20, 400),
            "food_needed": self.rng.randint(10, 40),
            "needs_refrigeration": self.rng.random() < 0.25,
            "severity": self.rng.randint(2, 5),
            "deadline": d.now + self.rng.randint(30, 120),
        })
        return f"Surprise request {req.id}"

    def status(self):
        return {"enabled": self.enabled, "level": self.level, "levels": list(LEVELS),
                "pending_recoveries": len(self.recoveries)}
