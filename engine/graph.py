from __future__ import annotations


class Graph:
    def __init__(self, dataset):
        self.nodes = dataset.nodes          # reuse the loader's dicts
        self.edges = dataset.edges
        self.closed_edges = set()

        # adjacency list: node id -> [(neighbor id, edge id), ...]
        self._adj = {node_id: [] for node_id in self.nodes}
        for edge in self.edges.values():
            self._adj[edge.a].append((edge.b, edge.id))
            self._adj[edge.b].append((edge.a, edge.id))   # roads are two-way

    def neighbors(self, node_id):
        return self._adj[node_id]

    def close_edge(self, edge_id):
        if edge_id not in self.edges:
            raise ValueError(f"Unknown edge: {edge_id}")
        self.closed_edges.add(edge_id)

    def open_edge(self, edge_id):
        self.closed_edges.discard(edge_id)   # hint: a set method that won't crash if it's not there