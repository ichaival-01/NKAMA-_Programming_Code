import heapq
from dataclasses import dataclass
 
 
@dataclass
class Route:
    nodes: list      # node ids from start to goal
    edge_ids: list   # edges used, in order
    cost: float      # total cost (travel time from "w")
 
 
def shortest_path(graph, start, goal):
    """
    Find the cheapest route from start to goal.
 
    graph must provide:
      graph.nodes          dict: node id -> Node
      graph.edges          dict: edge id -> Edge (with .cost)
      graph.neighbors(n)   list of (neighbor_id, edge_id)
      graph.closed_edges   set of closed edge ids
 
    Returns a Route, or None if no route exists (e.g. roads closed).
    """
    if start not in graph.nodes or goal not in graph.nodes:
        raise ValueError(f"Unknown node: start={start}, goal={goal}")
 
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
 
    # Walk backwards from the goal to the start using prev
    nodes = []
    edge_ids = []
    current = goal
    while current != start:
        previous_node, edge_id = prev[current]   # "I got here from previous_node via edge_id"
        nodes.append(current)
        edge_ids.append(edge_id)
        current = previous_node                  # step one node back
    nodes.append(start)
 
    # We collected them goal-first, so flip them around
    nodes.reverse()
    edge_ids.reverse()
 
    return Route(nodes, edge_ids, dist[goal])