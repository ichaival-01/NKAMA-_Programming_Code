# Urban Food Crisis Response and Routing System

Takes emergency food requests, decides which are most urgent, assigns food
and trucks, plans routes around road closures, and tracks every emergency
through a state machine, live on a dashboard. Works on any dataset in the
competition's JSON format without code changes.

## Run it

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn server:app --reload
```

Open http://127.0.0.1:8000, click **Load sample requests**, then **Play**.

Use a different dataset (e.g. Dataset 2) without changing code:

```powershell
$env:DATASET = "data/test-city.json"
python -m uvicorn server:app --reload
```

Run the tests: `python -m pytest`

## How it works

```
request → validate → score → priority queue → greedy match → A* route → track states
 (crisis)            (scoring) (priority_queue)  (matcher)     (router)   (states)
                 everything runs on a simulation clock in dispatcher.py
           and every change is published on the event bus (events.py)
```

| File | Job |
|---|---|
| `engine/loader.py` | Reads and validates the dataset JSON |
| `engine/graph.py` | Roads as a graph; closed roads are a set of edge ids |
| `engine/router.py` | A* (and the original Dijkstra, kept for comparison) |
| `engine/crisis.py` | Request format and validation; reports every mistake at once |
| `engine/scoring.py` | 0–100 triage score with a per-factor breakdown |
| `engine/priority_queue.py` | Heap queue with waiting-time aging and fair tie-breaks |
| `engine/matcher.py` | Greedy hub + truck choice; reservations prevent double-booking |
| `engine/states.py` | The state machine; illegal transitions raise an error |
| `engine/events.py` | Publish/subscribe event bus |
| `engine/dispatcher.py` | Ties it all together on a simulation clock |
| `server.py`, `static/` | FastAPI + WebSocket dashboard |
| `config.json` | Scoring weights, aging rate, thresholds |

## Design decisions

**Scoring.** Five parts, weights adding to 100 (all in `config.json`):
severity 30, deadline 25, people 20, location type 15, food needed 10.
The deadline part uses *spare time*: deadline − now − travel time from the
nearest hub, so a far-away site becomes urgent sooner. Location types a
dataset introduces (e.g. "school") get a default weight instead of breaking.

**Priority queue.** `heapq`, with priority = score + 0.2 × minutes waited.
Because everyone ages at the same rate, the heap key
`score − 0.2 × submitted_at` never changes as time passes, so aging needs no
re-sorting. The score itself does change (deadlines approach, roads close),
so queued requests are re-scored on every clock tick. Ties: earlier
submission first, then first come first served. Updates use lazy deletion,
the same stale-entry trick as Dijkstra.

**Matching (greedy).** The most urgent request picks first and gets the
fastest hub + truck combination that has enough of the right food
(cold or normal), enough capacity, refrigeration if needed, and an open
route. Ties prefer a regular truck over a refrigerated one, then the
smallest truck that fits. Reserved trucks and food can't be given to anyone
else until the delivery finishes or is cancelled.

**Routing (A\*).** Heuristic = straight-line distance ÷ the fastest
straight-line speed of any road, measured from the data. This never
overestimates, so A* returns exactly the same costs as Dijkstra (tested on
every pair of nodes, with random closures). Measuring speed from road *km*
would not be safe: some roads in Dataset 1 are slightly shorter than the
straight line between their ends.

**State machine.** Open → Prioritized → Resource Assigned → In Progress →
Delivered → Resolved. Delayed when no resources are free or a truck will
miss the deadline. Re-Routing only when a closed road is on a truck's
remaining route; if no route exists, the truck waits (Delayed) and resumes
when a road reopens. Failed when a request is still unserved
`failure_grace` minutes after its deadline. Every change is logged with
the simulation time and a reason.

**Breakdowns.** Before reaching the hub, the reserved food goes back into
stock. After loading, the load is written off and the replacement truck
collects fresh stock.

## Known limits

- One truck per request; a load bigger than any truck waits and fails.
  Splitting loads across trucks is a natural next step.
- Food must come from a single hub.
- Truck fuel is loaded but not used (the dataset doesn't define its unit).
- Trucks re-route from the last intersection they passed.

## Datasets

- `data/toronto-downtown.json`: Dataset 1.
- `data/toronto-downtown-requests.json`: sample requests for the demo.
- `data/test-city.json`: a made-up city that differs on purpose
  (non-sequential ids, new location kinds, highways and hills, a river with
  two bridges, fewer hubs and trucks) to prove the system adapts.
