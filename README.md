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

There are two pages:

| Page | Who it's for | What it does |
|---|---|---|
| http://127.0.0.1:8000/ | The public | Send a food request and follow **only your own** requests: progress steps, plain-language updates, arrival time, and your truck on the map |
| http://127.0.0.1:8000/dispatch | Dispatch staff (password) | The full console: queue with score breakdowns, every crisis, the clock, road closures, breakdowns, fleet and stock, live event feed |

### Dispatcher accounts

Each teammate gets their own login. Run this once per person (it asks for
the password twice, minimum 8 characters):

```powershell
python manage_users.py add aashi "Aashi"
python manage_users.py list
python manage_users.py remove aashi
```

Accounts are saved in `dispatchers.json`, which holds only salted password
hashes (PBKDF2, 200,000 rounds), never the passwords. **Don't commit it**:
it's listed in `.gitignore`. Each laptop that runs the server needs its own
accounts (or a copy of the file passed around privately).

Until the first account exists, a shared fallback login works: username
`dispatcher`, password from `$env:DISPATCH_PASSWORD` (default `dispatch`).
It switches off automatically once you add personal accounts.

Other protections: sessions expire after 12 hours, and 5 wrong passwords
from one computer lock it out for a minute. Console actions are signed with
the person's name in the event feed, e.g. "Road closed: Bloor St by Aashi".

For a quick demo: log in at `/dispatch`, click **Load sample requests**, then **Play**.
Open `/` in another browser window to submit a request and watch it progress.

**How the two sides stay separate.** Each request gets a tracking code like
`R4-7F3A9C`. The public page only shows requests whose full code it holds
(saved in that browser, or typed in), so ids like `R4` can't be guessed to
spy on other requests. Every dispatcher API and the live WebSocket require
the login cookie. The public view (`engine/public.py`) only exposes that one
request's progress, never scores, other requests, stock or controls.
Dispatchers can read a request's tracking code to someone who phoned it in.

Use a different dataset (e.g. Dataset 2) without changing code:

```powershell
$env:DATASET = "data/test-city.json"
python -m uvicorn server:app --reload
```

Run the tests: `python -m pytest`

## Requirements map

| Requirement | Where it's met | Proven by |
|---|---|---|
| Crisis submission, all 9 fields incl. submission time, validation | `engine/crisis.py`, New request form | `test_crisis.py` |
| Priority scoring (severity, people, food, deadline, location type, travel time), explainable | `engine/scoring.py`, Queue tab breakdown bars, Prioritized reason in each timeline | `test_scoring.py` |
| Heap queue, updates on change, waiting-time aging, equal-priority rule | `engine/priority_queue.py`, re-scored every tick and on closures | `test_priority_queue.py`, `test_dispatcher.py` |
| Dijkstra/A*, closures, alternatives, "route unavailable" | `engine/router.py`, re-routing in `dispatcher.py`, red "No route available" on the dashboard | `test_router.py`, `test_astar.py`, `test_dispatcher.py` |
| Greedy matching (stock, capacity, refrigeration, urgency, route) without double-booking | `engine/matcher.py` + reservations | `test_matcher.py`, chaos test in `test_generality.py` |
| All 9 states, valid transitions, Re-Routing only when needed, timestamp + reason on every change | `engine/states.py` (simulation time and real date-time logged) | `test_states.py`, `test_dispatcher.py` |
| Pub/sub bus for all 7 events, shown live over WebSocket | `engine/events.py`; `server.py` subscribes the website and a terminal logger | `test_server.py` |
| Works on a new dataset without code changes | `$env:DATASET`, `data/test-city.json` | `test_generality.py` |

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
| `engine/chaos.py` | Random closures, breakdowns and surprise requests, with recovery |
| `engine/public.py` | Plain-language progress of one request, for the public page |
| `auth.py`, `manage_users.py` | Dispatcher accounts, sessions, login lockout |
| `server.py` | FastAPI: public endpoints, dispatcher login, WebSocket |
| `static/public.*` | The public request and tracking page |
| `static/index.html`, `app.js`, `style.css` | The dispatcher console |
| `static/login.html` | Dispatcher login |
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

**Event bus.** The dispatcher only *publishes* events; it doesn't know who
listens. The server *subscribes* twice: the website subscribes to every
event and pushes each one to the browser over a WebSocket, and a terminal
logger subscribes only to road closures, breakdowns, low stock and
resolutions. Adding another listener (say, a text-message alert) means one
more `subscribe` call, with no change to the engine.

**Fuel.** Each truck's tank starts at the dataset's `fuel` value. Driving
burns `per_km` units per km (set in `config.json`), charged road by road as
the truck actually drives, including detours. The matcher never sends a
truck that can't finish the whole trip. After a delivery, a truck below the
`reserve` refuels where it is and is out of service for `refuel_minutes`
(10 by default). If no truck can afford a trip, the suitable idle trucks go
to refuel. Set `per_km` to 0 to switch fuel off. To see refuelling quickly
in a demo, raise `per_km` (e.g. 15).

**Chaos mode.** A toggle on the console (Low / Medium / High = 4 / 10 / 20
incidents per simulated hour). While the clock runs, it randomly closes a
road (often one a truck is about to use), breaks down a truck (often a busy
one), or adds a surprise request. Roads reopen after 10–40 minutes and
trucks are repaired after 15–45, so the city recovers. It never closes more
than a seventh of the roads or leaves fewer than two working trucks.
**Trigger incident** causes one immediately. Chaos actions appear in the
feed as "by Chaos".

**Breakdowns.** Before reaching the hub, the reserved food goes back into
stock. After loading, the load is written off and the replacement truck
collects fresh stock.

## Known limits

- One truck per request; a load bigger than any truck waits and fails.
  Splitting loads across trucks is a natural next step.
- Food must come from a single hub.
- The dataset doesn't define a fuel unit, so `per_km` is our assumption;
  confirm with the coordinators. Trucks refuel where they stand.
- Trucks re-route from the last intersection they passed.

## Datasets

- `data/toronto-downtown.json`: Dataset 1.
- `data/toronto-downtown-requests.json`: sample requests for the demo.
- `data/test-city.json`: a made-up city that differs on purpose
  (non-sequential ids, new location kinds, highways and hills, a river with
  two bridges, fewer hubs and trucks) to prove the system adapts.

- To input new data sets, the process goes: 

1. Input the data set into the /data folder
2. $env:DATASET = "data/dataset-2.json"
3. Start server


