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
    DELAYED:           {RESOURCE_ASSIGNED, IN_PROGRESS, RE_ROUTING, DELIVERED, FAILED},
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
    from_state: str
    to_state: str
    reason: str


@dataclass
class CrisisTracker:
    request_id: str
    state: str = OPEN
    history: list = field(default_factory=list)   # list of Transition

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
