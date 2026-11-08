# Urban Food Crisis Response and Routing System

## Administrative information

| | |
|---|---|
| **Team name** | _["NKAMA!!!"]_ |
| **Competitors** | _["Kosimasichukwu Ebegbulem"]_, _["Aashir Ahmed"]_, _["Mansur Hasan"]_ |
| **Project title** | Urban Food Crisis Response and Routing System |
| **Repository** | _[https://github.com/ichaival-01/NKAMA-_Programming_Code.git]_ |
| **AI usage log** | [AI_USAGE.md](AI_USAGE.md) |

---

## Overview

The program manages emergency food deliveries across a city. It is built
around the competition's six requirements and runs on any dataset in the
competition's JSON format without code changes.

1. **Takes emergency requests.** People submit requests through a public web
   page (location, crisis type, location type, people affected, food needed,
   refrigeration, severity, deadline; submission time is stamped
   automatically). Every field is validated, and all mistakes are reported at
   once.
2. **Triages them.** Each request gets a 0–100 urgency score from severity,
   deadline (including travel time from the nearest hub), people affected,
   location type and food needed. The score's breakdown is shown on screen.
3. **Queues them fairly.** A heap-based priority queue puts the most urgent
   first, adds a waiting-time boost so nobody waits forever, and re-scores
   requests as deadlines approach and roads close.
4. **Assigns food and trucks greedily.** The most urgent request gets the
   fastest hub and truck that has enough of the right food (normal or cold),
   enough capacity, refrigeration if needed, enough fuel, and an open route.
   Nothing can be assigned to two emergencies at once.
5. **Plans routes with A\*.** Trucks re-route around closed roads, wait when
   a site is cut off, and resume when a road reopens.
6. **Tracks every emergency** through nine states (Open, Prioritized,
   Resource Assigned, In Progress, Re-Routing, Delivered, Resolved, Delayed,
   Failed), logging the time and a reason for every change.

Everything runs on a **simulation clock** that dispatchers control. A
publish/subscribe **event bus** pushes every change to the dispatcher
console live over a WebSocket. Disruptions can be triggered by hand or by
**chaos mode** (random road closures, breakdowns and surprise requests), and
trucks burn **fuel** and must stop to refuel.

There are two web pages:

| Page | Address | Who it's for |
|---|---|---|
| Public request page | http://127.0.0.1:8000/ | Anyone: send a request and follow **only your own** requests with a tracking code |
| Dispatcher console | http://127.0.0.1:8000/dispatch | Dispatch staff (login required): every request, the queue, the fleet, the clock and the controls |

---

## How to install and run

Python is interpreted, so there is no separate compile step: installing the
dependencies and starting the server is all that's needed. These steps take
about two minutes. **An internet connection is needed** the first time (to
install packages) and while using the web pages (the map library, map tiles
and fonts load from the web).

### 1. Requirements

- **Python 3.8 or newer** (tested on 3.8.3 and 3.13). During installation on
  Windows, tick **"Add python.exe to PATH"**.
- **Git** (only to download the code).

### 2. Download the code

```powershell
git clone _[GitHub link]_
cd _[repository folder name]_
```

### 3. Create a virtual environment and install the dependencies

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
If PowerShell says *running scripts is disabled*, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`(.venv)` appears at the start of the prompt when the environment is active.
Activate it again in every new terminal.

### 4. (Optional) Check everything works

```powershell
python -m pytest
```
All tests should pass (242 at the time of writing).

### 5. Start the server

```powershell
python -m uvicorn server:app --reload
```

Leave this terminal open. Then open:
- **http://127.0.0.1:8000/** for the public page
- **http://127.0.0.1:8000/dispatch** for the dispatcher console (see
  [Dispatcher logins](#dispatcher-logins) below)

Stop the server with `Ctrl+C`.

### 6. Run with a different dataset (for example, Dataset 2)

Point the server at the file; no code changes are needed.

**Windows (PowerShell):**
```powershell
$env:DATASET = "data/dataset-2.json"
python -m uvicorn server:app --reload
```

**macOS / Linux:**
```bash
DATASET=data/dataset-2.json python -m uvicorn server:app --reload
```

To go back to Toronto in PowerShell, close the terminal or run
`Remove-Item Env:DATASET`.

If a file of sample requests sits next to the dataset with `-requests` added
to its name (for example `data/dataset-2-requests.json`), the console's
**Load sample requests** button loads it. A different file can be chosen
with `$env:REQUESTS = "path/to/requests.json"`.

---

## Dependencies

### Python packages (installed by `pip install -r requirements.txt`)

| Package | What it's used for |
|---|---|
| **FastAPI** | The web server: pages, the API and the WebSocket |
| **Uvicorn** (with `[standard]` extras, which include **websockets**) | Runs the FastAPI server; the extras enable live WebSocket updates |
| **pytest** | Runs the automated tests |
| **httpx** | Lets the tests call the web server (used by FastAPI's test client) |

### Python standard library (built in, nothing to install)

`heapq` (priority queue and route search), `dataclasses`, `json`, `math`,
`random` (chaos mode), `hashlib`, `hmac` and `secrets` (password hashing and
login sessions), `getpass`, `datetime`, `pathlib`, `itertools`, `copy`.

No machine-learning or data-analysis libraries (such as TensorFlow or
Pandas) are used.

### Web page libraries (loaded from the internet by the browser)

| Library | What it's used for |
|---|---|
| **Leaflet 1.9.4** (from unpkg.com) | The interactive map |
| **OpenStreetMap** map tiles | The street map behind the roads |
| **Barlow** fonts (from Google Fonts) | Typography; the pages fall back to system fonts if offline |

---

## Operating the app

### Dispatcher logins

Each team member has their own login to the dispatcher console.

**Create an account** (once per person, on the computer that runs the server):
```powershell
python manage_users.py add aashi "Aashi"
```
- `aashi` is the **username** used to log in (letters, numbers and `_`).
- `"Aashi"` is the **display name** shown in the console and in the event
  feed (for example "Road closed: Bloor St by Aashi").
- It asks for the password twice. Passwords need at least 8 characters.

**Other account commands:**
```powershell
python manage_users.py list            # see all usernames
python manage_users.py remove aashi    # delete an account
```

**Logging in:** go to http://127.0.0.1:8000/dispatch and enter the username
and password.

**Things to know:**
- Accounts are stored in `dispatchers.json` as salted password hashes; the
  passwords themselves are never stored. This file is in `.gitignore` and
  **must not be uploaded to GitHub**. Each computer that runs the server
  needs its own accounts.
- **Before any account exists**, a shared login works: username
  `dispatcher`, password `dispatch`. It switches off automatically once the
  first personal account is created. To give the shared login a different
  password, set it before starting the server:
  `$env:DISPATCH_PASSWORD = "your-password"`.
- **Five wrong passwords** lock that computer out for one minute.
- Sessions last **12 hours**, then the console asks for the login again.
- A **forgotten password** can't be recovered. Remove the account and add it
  again.

### Dispatcher console

| Control | What it does |
|---|---|
| **Play / Pause** | Runs the simulation clock (speed: 1, 2 or 5 simulated minutes per second) |
| **Skip 5 min / Skip 15 min** | Jumps the clock forward |
| **Load sample requests** | Loads the demo requests for the current dataset |
| **Chaos on / off**, with Low, Medium or High | Random road closures, truck breakdowns and surprise requests while the clock runs (about 4, 10 or 20 per simulated hour). Roads reopen and trucks are repaired on their own |
| **Trigger incident** | Causes one random incident immediately |
| **Reset** | Starts the simulation over (clears all requests and tracking codes) |
| **Log out** | Ends your session |
| **Click a road** on the map | Closes it (red dashed line). Click again to reopen it |
| **Click a crisis site** on the map | Opens its details and timeline |

**Tabs on the right:**
- **Queue:** waiting requests, most urgent first. The bar shows how the
  score breaks down (severity, deadline, people, location type, food,
  waiting boost) and why the request is waiting.
- **Crises:** every request with its state. Click one to see its full
  timeline (each change with its time and reason) and its tracking code.
- **New request:** enter a request by hand, for example one phoned in.
  Leave **Submission time** blank for "now", or enter a later simulated time
  to schedule it.
- **Fleet:** food stock at each hub, and each truck's status and fuel.
  **Break down** takes a truck out of service; **Repair** brings it back.

The **event feed** under the map shows every event as it happens. Road
closures, breakdowns, low stock and completed deliveries are also printed in
the server's terminal.

**A quick demonstration:** log in, click **Load sample requests**, press
**Play**, then click a road that a truck is about to use. The truck
re-routes and the request's timeline records it.

### Public request page

1. Open http://127.0.0.1:8000/, fill in the form and press **Send request**.
2. A **tracking code** appears (for example `R4-D90B30`). The page then
   shows that request's progress live: its step, a plain-language update,
   the expected arrival, and the truck on the map.
3. The browser remembers the code. To follow a request from another device,
   type the code into **Have a tracking code?**.

The public page never shows other people's requests or any dispatcher
controls. A request's id alone (like `R4`) is not enough to see it; the full
code is needed. Dispatchers can read the code from the **Crises** tab to give
to someone who phoned a request in.

### Tuning (`config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `weights` | severity 30, deadline 25, people 20, location 15, food 10 | Maximum points per part of the score (they add up to 100) |
| `location_weights` | hospital 1.0, shelter 0.8, community 0.6 | Share of the location points per type; other types get `default_location_weight` (0.5) |
| `aging_rate` | 0.2 | Extra priority points per minute a request waits |
| `fuel.per_km` | 6 | Fuel used per km driven (0 switches fuel off) |
| `fuel.reserve` | 25 | Trucks refuel after a delivery when below this |
| `fuel.refuel_minutes` | 10 | How long refuelling takes |

Restart the server after changing `config.json`.

### Troubleshooting

| Problem | Fix |
|---|---|
| `python` is not recognized | Reinstall Python with "Add python.exe to PATH" ticked, or use `py` instead of `python` |
| *Running scripts is disabled* when activating | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| `No module named 'engine'` | Run commands from the project's top folder |
| `Address already in use` | Another server is running. Stop it, or add `--port 8001` and use that port in the address |
| The map is blank | Check the internet connection; the map library and tiles load from the web |
| "Too many wrong passwords" | Wait one minute and try again |
| Live updates don't appear | Reinstall with `pip install -r requirements.txt` (the WebSocket support comes from `uvicorn[standard]`) |

---

## How it's built

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
| `engine/router.py` | A* (and Dijkstra, kept for comparison) |
| `engine/crisis.py` | Request format and validation |
| `engine/scoring.py` | Triage score with a per-factor breakdown |
| `engine/priority_queue.py` | Heap queue with aging and tie-breaks |
| `engine/matcher.py` | Greedy hub and truck choice; reservations; fuel checks |
| `engine/states.py` | The state machine |
| `engine/events.py` | Publish/subscribe event bus |
| `engine/dispatcher.py` | Ties everything together on the simulation clock |
| `engine/chaos.py` | Chaos mode |
| `engine/public.py` | Plain-language progress of one request for the public page |
| `auth.py`, `manage_users.py` | Dispatcher accounts, sessions and login lockout |
| `server.py` | Web server: public endpoints, dispatcher API, WebSocket |
| `static/public.*` | Public request page |
| `static/index.html`, `app.js`, `style.css`, `login.html` | Dispatcher console and login |
| `data/` | Datasets and sample requests |
| `tests/` | Automated tests |

### Requirements map

| Requirement | Where it's met | Tested in |
|---|---|---|
| Crisis submission with validation | `engine/crisis.py`, both request forms | `test_crisis.py` |
| Explainable priority scoring | `engine/scoring.py`, Queue tab | `test_scoring.py` |
| Heap queue, updates, aging, ties | `engine/priority_queue.py` | `test_priority_queue.py` |
| Dijkstra / A*, closures, "no route available" | `engine/router.py`, `engine/dispatcher.py` | `test_router.py`, `test_astar.py`, `test_dispatcher.py` |
| Greedy matching without double-booking | `engine/matcher.py` | `test_matcher.py`, `test_generality.py` |
| Nine-state machine with timestamps and reasons | `engine/states.py` | `test_states.py`, `test_dispatcher.py` |
| Pub/sub events shown live over WebSocket | `engine/events.py`, `server.py` | `test_server.py` |
| Works on any dataset | `$env:DATASET`, `data/test-city.json` | `test_generality.py` |

### Design decisions

**Scoring.** The deadline part uses *spare time*: deadline − now − travel
time from the nearest hub, so a far-away site becomes urgent sooner.

**Priority queue.** Priority = score + 0.2 × minutes waited. Because every
request ages at the same rate, the heap key `score − 0.2 × submitted_at`
never changes over time, so aging needs no re-sorting. Scores themselves do
change, so queued requests are re-scored on every clock tick. Ties go to the
earlier submission, then first come, first served.

**Matching.** Ties prefer a regular truck over a refrigerated one (saving
refrigerated trucks for cold food), then the smallest truck that fits.

**Routing.** A*'s estimate is the straight-line distance divided by the
fastest straight-line speed of any road, measured from the data. It never
overestimates, so A* always finds the same cost as Dijkstra while searching
about 55% fewer intersections on the Toronto map.

**State machine.** Re-Routing happens only when a closed road is on a
truck's remaining route. A request is Failed when it is still unserved two
hours (`failure_grace`) after its deadline.

**Breakdowns.** Before a truck reaches the hub, its reserved food goes back
into stock. After loading, the load is written off and the replacement truck
collects fresh stock.

### Known limits

- One truck and one hub per request; a load bigger than any truck waits and
  eventually fails.
- The datasets don't define a unit for fuel, so `fuel.per_km` is our
  assumption. Trucks refuel where they stand.
- A truck re-routes from the last intersection it passed.

### Datasets

- `data/toronto-downtown.json`: Dataset 1 (Downtown Toronto).
- `data/toronto-downtown-requests.json`: sample requests for the demo.
- `data/test-city.json`: a made-up city that differs on purpose
  (non-sequential ids, new location types, highways and hills, a river with
  two bridges, fewer hubs and trucks) to show the system adapts.


### AI DISCLAIMER

- The project was built using the assistance of Generative AI to help explain, debug and rebuild
  portions of the project. This disclaimer is to demonstrate full transparency with the 
  development life-cycle and credit Anthropic's Claude AI for assisting in the 
  completion of the project.s
