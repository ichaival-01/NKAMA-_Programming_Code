"""Crisis state machine (goal 5).

Every emergency moves through these states. Only the transitions listed in
ALLOWED are legal; anything else raises InvalidTransition, which catches
logic bugs early instead of letting a request end up in a nonsense state.
Every change is logged with the simulation time and a plain-language reason.

    OPEN -> PRIORITIZED -> RESOURCE_ASSIGNED -> IN_PROGRESS -> DELIVERED -> RESOLVED
                 |                 |                |  ^
                 v                 v                v  |
              DELAYED  <------  (no resources,   RE_ROUTING  (only when a road
                 |               running late)                on the route closes)
                 v
              FAILED   (deadline long gone, or the site can't be reached)
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

from dataclasses import dataclass, field
from datetime import datetime


def _wall_clock():
    """Real date and time, logged alongside the simulation time."""
    return datetime.now().isoformat(timespec="seconds")

OPEN = "Open"
PRIORITIZED = "Prioritized"
RESOURCE_ASSIGNED = "Resource Assigned"
IN_PROGRESS = "In Progress"
RE_ROUTING = "Re-Routing"
DELIVERED = "Delivered"
RESOLVED = "Resolved"
DELAYED = "Delayed"
FAILED = "Failed"

ALL_STATES = [OPEN, PRIORITIZED, RESOURCE_ASSIGNED, IN_PROGRESS, RE_ROUTING,
              DELIVERED, RESOLVED, DELAYED, FAILED]

ALLOWED = {
    OPEN:              {PRIORITIZED, FAILED},
    PRIORITIZED:       {RESOURCE_ASSIGNED, DELAYED, FAILED},
    # Delayed -> Delayed: delayed again for a NEW reason (e.g. a late truck then
    # breaks down). Logged as its own entry so the new reason has a timestamp.
    DELAYED:           {DELAYED, RESOURCE_ASSIGNED, IN_PROGRESS, RE_ROUTING, DELIVERED, FAILED},
    RESOURCE_ASSIGNED: {IN_PROGRESS, RE_ROUTING, DELAYED, FAILED},
    IN_PROGRESS:       {DELIVERED, RE_ROUTING, DELAYED, FAILED},
    RE_ROUTING:        {IN_PROGRESS, DELAYED, FAILED},
    DELIVERED:         {RESOLVED},
    RESOLVED:          set(),     # finished
    FAILED:            set(),     # finished
}

FINAL_STATES = {RESOLVED, FAILED}


class InvalidTransition(Exception):
    pass


@dataclass
class Transition:
    time: float          # simulation minutes
    from_state: str      # "" for the first entry, when the request arrives
    to_state: str
    reason: str
    logged_at: str = field(default_factory=_wall_clock)   # real date and time


@dataclass
class CrisisTracker:
    request_id: str
    state: str = OPEN
    history: list = field(default_factory=list)   # list of Transition

    def record_open(self, time, reason):
        """Log the starting Open state, so arrival has a timestamp and reason too."""
        if self.history:
            raise InvalidTransition(f"{self.request_id} is already being tracked")
        self.history.append(Transition(time, "", OPEN, reason))

    def can_move_to(self, new_state):
        return new_state in ALLOWED[self.state]

    def move_to(self, new_state, time, reason):
        if new_state not in ALLOWED:
            raise InvalidTransition(f"Unknown state: {new_state!r}")
        if not self.can_move_to(new_state):
            raise InvalidTransition(
                f"{self.request_id}: can't go from {self.state} to {new_state}"
            )
        self.history.append(Transition(time, self.state, new_state, reason))
        self.state = new_state

    @property
    def is_finished(self):
        return self.state in FINAL_STATES
