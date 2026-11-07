"""Shortest routes: Dijkstra and A* (goal 4).

shortest_path() uses A*. dijkstra() is your teammate's original algorithm,
kept for comparison: the tests check both always give the same cost.

How A* differs from Dijkstra: Dijkstra explores outward in every direction.
A* adds a guess of the remaining time to each node's priority, so it
explores toward the goal first and usually visits far fewer nodes.

The guess (the "heuristic") must NEVER overestimate the real remaining time,
or A* can return a worse route. Ours is:

    straight-line distance to the goal / fastest straight-line speed on any road

No road can be faster than the fastest road, and no route can be shorter than
a straight line, so the guess is always at or below the truth. The speed is
measured from the data itself (straight-line length / travel time of each
road), so nothing is hardcoded and Dataset 2 works as-is. If a dataset had no
coordinates, the guess becomes 0 and A* behaves exactly like Dijkstra.
"""
import heapq
import math
from dataclasses import dataclass


@dataclass
class Route:
    nodes: list      # node ids from start to goal
    edge_ids: list   # edges used, in order
    cost: float      # total cost (travel time from "w")


def _straight_line_km(n1, n2):
    """Great-circle (haversine) distance in km between two nodes."""
    r = 6371.0
    p1, p2 = math.radians(n1.lat), math.radians(n2.lat)
    dp = p2 - p1
    dl = math.radians(n2.lng - n1.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _max_speed(graph):
    """Fastest straight-line km per cost unit over all roads (cached on the graph)."""
    speed = getattr(graph, "_astar_max_speed", None)
    if speed is None:
        speed = 0.0
        for e in graph.edges.values():
            km = _straight_line_km(graph.nodes[e.a], graph.nodes[e.b])
            speed = max(speed, km / e.cost)
        graph._astar_max_speed = speed
    return speed


def _heuristic(graph, node, goal):
    speed = _max_speed(graph)
    if speed <= 0:
        return 0.0
    return _straight_line_km(graph.nodes[node], graph.nodes[goal]) / speed


def _check_nodes(graph, start, goal):
    if start not in graph.nodes or goal not in graph.nodes:
        raise ValueError(f"Unknown node: start={start}, goal={goal}")


def _rebuild(prev, start, goal, cost):
    """Walk backwards from the goal to the start using prev."""
    nodes = []
    edge_ids = []
    current = goal
    while current != start:
        previous_node, edge_id = prev[current]   # "I got here from previous_node via edge_id"
        nodes.append(current)
        edge_ids.append(edge_id)
        current = previous_node
    nodes.append(start)
    nodes.reverse()
    edge_ids.reverse()
    return Route(nodes, edge_ids, cost)


def dijkstra(graph, start, goal):
    """Your teammate's Dijkstra. Returns a Route, or None if no route exists."""
    _check_nodes(graph, start, goal)

    dist = {start: 0.0}          # best known cost to each node
    prev = {}                    # node -> (previous node, edge id used)
    heap = [(0.0, start)]        # (cost so far, node)

    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist[node]:    # stale entry: we already found a cheaper way
            continue
        if node == goal:         # cheapest way to the goal is now final
            break
        for neighbor, eid in graph.neighbors(node):
            if eid in graph.closed_edges:
                continue         # closed road: pretend it doesn't exist
            new_cost = cost + graph.edges[eid].cost
            if new_cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_cost
                prev[neighbor] = (node, eid)
                heapq.heappush(heap, (new_cost, neighbor))

    if goal not in dist:
        return None              # route unavailable
    return _rebuild(prev, start, goal, dist[goal])


def astar(graph, start, goal):
    """A*: Dijkstra plus a guess of the remaining time. Same result, less searching."""
    _check_nodes(graph, start, goal)

    dist = {start: 0.0}
    prev = {}
    # Heap priority = cost so far + guess of the rest. The cost so far is kept
    # second so stale entries can still be detected exactly like Dijkstra.
    heap = [(_heuristic(graph, start, goal), 0.0, start)]

    while heap:
        _, cost, node = heapq.heappop(heap)
        if cost > dist[node]:
            continue
        if node == goal:
            break
        for neighbor, eid in graph.neighbors(node):
            if eid in graph.closed_edges:
                continue
            new_cost = cost + graph.edges[eid].cost
            if new_cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_cost
                prev[neighbor] = (node, eid)
                guess = new_cost + _heuristic(graph, neighbor, goal)
                heapq.heappush(heap, (guess, new_cost, neighbor))

    if goal not in dist:
        return None
    return _rebuild(prev, start, goal, dist[goal])


def shortest_path(graph, start, goal):
    """The route everything else uses. Returns a Route, or None if unreachable."""
    return astar(graph, start, goal)
