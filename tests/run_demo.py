from engine.loader import load_dataset
from engine.graph import Graph
from engine.router import shortest_path

ds = load_dataset("data/toronto-downtown.json")
graph = Graph(ds)
print(f"Loaded {len(ds.nodes)} nodes, {len(ds.edges)} edges")

for start, goal in [(33, 22), (33, 44), (4, 4)]:
    route = shortest_path(graph, start, goal)
    print(f"{start} -> {goal}: cost {route.cost:.3f}, path {route.nodes}")

# close every road into node 44 (Yonge & Bloor)
for eid in (69, 78, 79):
    graph.close_edge(eid)
print("33 -> 44 with roads closed:", shortest_path(graph, 33, 44))