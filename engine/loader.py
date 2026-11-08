#=======================================================================================================================
import json
from dataclasses import dataclass, field

#Imports json to read the dataset file, and dataclass for the record types below
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Node:
    id: int
    name: str
    lat: float
    lng: float

#An intersection on the map: id, name and coordinates
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Edge:
    id: int
    a: int
    b: int
    km: float
    cost: float          # from "w" in the JSON (travel time, minutes)
    street: str = ""
    hwy: bool = False
    terrain: str = "urban"

#A road between two nodes: length, travel time (cost, from "w"), street name, highway flag and terrain
#=======================================================================================================================

#=======================================================================================================================
@dataclass  
class POI:
    id: str
    name: str
    kind: str
    node: int
    lat: float
    lng: float
    address: str = ""

#A named place on the map (hub, hospital, shelter...) and the node it sits on
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Truck:
    id: str
    name: str
    home: int
    fridge: bool
    capacity: int
    fuel: float

#A delivery truck: home node, refrigeration, capacity and fuel tank
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Hub:
    poi_id: str
    normal: int
    cold: int

#Food stock held at a hub: normal and cold units
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Dataset:
    name: str
    nodes: dict          # id -> Node
    edges: dict          # id -> Edge
    pois: dict           # P0 -> POIs'
    hubs: dict           # poi id (P0) -> Hubs
    trucks: dict         # V1 -> trucks

#The whole loaded city, with each record type stored in a dict by id
#=======================================================================================================================

#=======================================================================================================================
class DatasetError(Exception):
    pass

#Error raised when the dataset file is missing data or has bad values
#=======================================================================================================================

#=======================================================================================================================
def load_dataset(path):
    with open(path) as f:
        raw = json.load(f)

    REQUIRED =  ["nodes", "edges", "pois", "hubs", "trucks"]

    for key in REQUIRED:
        if key not in raw: 
            raise DatasetError(f"Missing required Data:  '{key}'")


    nodes = {}
    for item in raw["nodes"]:
        if item["id"] in nodes: 
            raise DatasetError(f"Duplicate Node found, with ID: {item['id']}")
        node = Node(id=item["id"], name = item["name"], lat =item["lat"], lng = item["lng"])
        nodes[node.id] = node

    edges = {}
    for item in raw["edges"]:
        eid = item["id"]
        if eid in edges:
            raise DatasetError(f"Duplicate edge id: {eid}")

        # both ends must be real nodes (no going to jail)
        if item["a"] not in nodes or item["b"] not in nodes:
            raise DatasetError(f"Edge {eid} points to a missing node")

        # cost must be a real, positive number (duh)
        w = item.get("w")
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            raise DatasetError(f"Edge {eid} has an invalid cost: {w!r}")
        if w <= 0:
            raise DatasetError(f"Edge {eid} has a non-positive cost: {w}")

        edges[eid] = Edge(
            id=eid,
            a=item["a"],
            b=item["b"],
            km=item["km"],
            cost=w,
            street=item.get("street", ""),
            hwy=item.get("hwy", False),  # optional dataset extra; not used yet
            terrain=item.get("terrain", "urban"),  # optional dataset extra; not used yet
        )

    pois = {}
    for item in raw["pois"]:
        if item["id"] in pois:
            raise DatasetError(f"Duplicate POI id: {item['id']}")
        if item["node"] not in nodes:  # the POI's node must exist
            raise DatasetError(f"POI {item['id']} is on missing node {item['node']}")
        pois[item["id"]] = POI(
            id=item["id"], name=item["name"], kind=item["kind"],
            node=item["node"], lat=item["lat"], lng=item["lng"],
            address=item.get("address", ""),
        )

    hubs = {}
    for item in raw["hubs"]:
        if item["poiId"] not in pois:
            raise DatasetError(f"Hub refers to unknown POI {item['poiId']}")
        hubs[item["poiId"]] = Hub(poi_id=item["poiId"], normal= item["normal"], cold= item["cold"])       

    trucks = {}
    for item in raw["trucks"]:
        if item["id"] in trucks:
            raise DatasetError(f"Duplicate truck id: {item['id']}")
        if item["home"] not in nodes:   # home must be a real node
            raise DatasetError(f"Truck {item['id']} has unknown home node {item['home']}")
        trucks[item["id"]] = Truck(
            id=item["id"], name=item["name"], home=item["home"],
            fridge=item["refrigerated"], capacity=item["capacity"], fuel=item["fuel"],
        )


    return Dataset(
        name=raw.get("name", ""),
        nodes=nodes, edges=edges, pois=pois, hubs=hubs, trucks=trucks,
    )

#Reads the JSON file, checks for missing sections, duplicate ids, unknown nodes and invalid costs, then builds every
#record into one Dataset
#=======================================================================================================================