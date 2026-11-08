#=======================================================================================================================
from dataclasses import dataclass

from engine.router import shortest_path

#Imports dataclass for the result records and shortest_path (A*) for routing
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Assignment:
    request_id: str
    hub_id: str            # POI id of the hub, e.g. "P2"
    truck_id: str          # e.g. "V10"
    stock_type: str        # "normal" or "cold"
    amount: int
    to_hub: object         # Route: truck's location -> hub
    to_site: object        # Route: hub -> crisis site
    travel_time: float     # to_hub.cost + to_site.cost
    arrival_time: float    # now + travel_time
    on_time: bool          # arrival_time <= deadline
    reason: str            # plain-language explanation for the dashboard
    km: float = 0.0        # distance of both legs, for fuel

#A successful match: which hub, truck and food, both route legs, timing, distance and a reason for the dashboard
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class NoMatch:
    request_id: str
    reason: str            # why nothing could be assigned
    kind: str = "other"    # "stock", "truck", "fuel" or "route", so callers can react

#A failed match, with the reason and its kind (stock, truck, fuel or route) so the dispatcher can react
#=======================================================================================================================

#=======================================================================================================================
def route_km(graph, route):
    return sum(graph.edges[eid].km for eid in route.edge_ids)

#Total km of a route, used to work out fuel
#=======================================================================================================================

#=======================================================================================================================
class ResourcePool:
    def __init__(self, dataset, fuel_per_km=0.0):
        self.dataset = dataset
        # Fuel: a truck's tank starts at the dataset's "fuel" value. Driving
        # burns fuel_per_km for every km. 0 switches fuel off entirely.
        self.fuel_per_km = fuel_per_km
        self.max_fuel = {tid: float(t.fuel) for tid, t in dataset.trucks.items()}
        self.fuel = dict(self.max_fuel)
        self.refuelling = {}   # truck id -> simulation time it's ready again
        # Copy the stock, so the original dataset is never changed.
        self.stock = {
            hub_id: {"normal": hub.normal, "cold": hub.cold}
            for hub_id, hub in dataset.hubs.items()
        }
        # Trucks start at home; after a delivery they're wherever they finished.
        self.truck_location = {tid: t.home for tid, t in dataset.trucks.items()}
        self.busy = {}         # truck id -> request id it's working on
        self.broken = set()    # trucks out of service after a breakdown

#Live state of every truck and hub: stock (a copy of the dataset's), truck locations, busy, broken, refuelling and fuel
#levels
#=======================================================================================================================

#=======================================================================================================================
    def is_free(self, truck_id):
        return (truck_id not in self.busy and truck_id not in self.broken
                and truck_id not in self.refuelling)

#True if a truck isn't busy, broken or refuelling
#=======================================================================================================================

#=======================================================================================================================
    def fuel_needed(self, km):
        return km * self.fuel_per_km

#Fuel a trip of this many km needs
#=======================================================================================================================

#=======================================================================================================================
    def reserve(self, a):
        """Lock in an assignment: take the food, mark the truck busy."""
        if a.truck_id in self.busy:
            raise ValueError(f"Truck {a.truck_id} is already assigned to {self.busy[a.truck_id]}")
        if a.truck_id in self.broken:
            raise ValueError(f"Truck {a.truck_id} is broken down")
        if self.stock[a.hub_id][a.stock_type] < a.amount:
            raise ValueError(f"Hub {a.hub_id} no longer has {a.amount} {a.stock_type}")
        self.stock[a.hub_id][a.stock_type] -= a.amount
        self.busy[a.truck_id] = a.request_id

#Locks in an assignment: takes the food and marks the truck busy, so nothing is given out twice
#=======================================================================================================================

#=======================================================================================================================
    def release(self, a):
        """Undo a reservation (e.g. delivery failed or cancelled)."""
        self.stock[a.hub_id][a.stock_type] += a.amount
        del self.busy[a.truck_id]

#Undoes a reservation: returns the food and frees the truck
#=======================================================================================================================

#=======================================================================================================================
    def complete(self, a, site):
        """Delivery finished: the food stays used, the truck is free at the site."""
        del self.busy[a.truck_id]
        self.truck_location[a.truck_id] = site

#Finishes a delivery: the food stays used and the truck is free at the site
#=======================================================================================================================

#=======================================================================================================================
    def break_down(self, truck_id, location):
        """Take a truck out of service where it stopped."""
        self.busy.pop(truck_id, None)
        self.refuelling.pop(truck_id, None)
        self.broken.add(truck_id)
        self.truck_location[truck_id] = location

#Takes a truck out of service where it stopped
#=======================================================================================================================

#=======================================================================================================================
    def repair(self, truck_id):
        self.broken.discard(truck_id)

#Puts a broken truck back in service
#=======================================================================================================================

#=======================================================================================================================
def find_match(req, pool, graph, now):
    """Return the best Assignment for this request, or a NoMatch with a reason."""
    ds = pool.dataset
    stock_type = "cold" if req.needs_refrigeration else "normal"

    # 1. Hubs with enough of the right food
    hubs = [hub_id for hub_id, s in pool.stock.items() if s[stock_type] >= req.food_needed]
    if not hubs:
        return NoMatch(req.id, f"No hub has {req.food_needed} units of {stock_type} food",
                       kind="stock")

    # 2. Trucks that are free, big enough, and refrigerated if needed
    trucks = [
        truck_id for truck_id, truck in ds.trucks.items()
        if pool.is_free(truck_id)
        and truck.capacity >= req.food_needed
        and (truck.fridge or not req.needs_refrigeration)
    ]
    if not trucks:
        kind = "refrigerated " if req.needs_refrigeration else ""
        return NoMatch(req.id, f"No free {kind}truck can carry {req.food_needed} units",
                       kind="truck")

    # 3. Try every hub + truck pair and keep the best one
    truck_order = list(ds.trucks)        # for a stable final tie-break
    best = None                          # (sort_key, hub_id, truck_id, to_hub, to_site)
    short_of_fuel = False
    for hub_id in hubs:
        hub_node = ds.pois[hub_id].node
        to_site = shortest_path(graph, hub_node, req.location)
        if to_site is None:
            continue                     # road closures cut this hub off
        for truck_id in trucks:
            truck = ds.trucks[truck_id]
            to_hub = shortest_path(graph, pool.truck_location[truck_id], hub_node)
            if to_hub is None:
                continue
            # Never send a truck that can't finish the whole trip
            km = route_km(graph, to_hub) + route_km(graph, to_site)
            if pool.fuel[truck_id] + 1e-9 < pool.fuel_needed(km):
                short_of_fuel = True
                continue
            total = to_hub.cost + to_site.cost
            # Smaller is better: fastest, then don't waste a reefer on a
            # normal job, then the smallest truck that fits, then file order.
            wastes_reefer = truck.fridge and not req.needs_refrigeration
            key = (total, wastes_reefer, truck.capacity, truck_order.index(truck_id))
            if best is None or key < best[0]:
                best = (key, hub_id, truck_id, to_hub, to_site)

    if best is None:
        if short_of_fuel:
            return NoMatch(req.id, "Suitable trucks don't have enough fuel for this trip",
                           kind="fuel")
        return NoMatch(req.id, "No route from any suitable hub and truck to the site",
                       kind="route")

    _, hub_id, truck_id, to_hub, to_site = best
    travel_time = to_hub.cost + to_site.cost
    arrival = now + travel_time
    return Assignment(
        request_id=req.id, hub_id=hub_id, truck_id=truck_id,
        stock_type=stock_type, amount=req.food_needed,
        to_hub=to_hub, to_site=to_site,
        travel_time=travel_time, arrival_time=arrival,
        on_time=arrival <= req.deadline,
        reason=(f"{ds.trucks[truck_id].name} via {ds.pois[hub_id].name}: "
                f"fastest suitable option ({travel_time:.1f} min)"),
        km=route_km(graph, to_hub) + route_km(graph, to_site),
    )

#Greedy match: keeps hubs with enough food and free suitable trucks, then tries every hub + truck pair and keeps the
#fastest one with a route and enough fuel. Ties: don't waste a fridge truck, then the smallest truck, then file order
#=======================================================================================================================