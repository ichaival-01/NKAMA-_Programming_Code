#=======================================================================================================================
from dataclasses import dataclass, field
from datetime import datetime

#Imports dataclass/field for the records and datetime for real timestamps
#=======================================================================================================================

#=======================================================================================================================
def _wall_clock():
    """Real date and time, logged alongside the simulation time."""
    return datetime.now().isoformat(timespec="seconds")

#Real date and time, logged next to the simulation time on every change
#=======================================================================================================================

#=======================================================================================================================
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

#The nine states a request can be in, and a list of all of them
#=======================================================================================================================

#=======================================================================================================================
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

#The state machine: which states each state may move to. Resolved and Failed are final
#=======================================================================================================================

#=======================================================================================================================
class InvalidTransition(Exception):
    pass

#Error raised for a move the state machine doesn't allow
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Transition:
    time: float          # simulation minutes
    from_state: str      # "" for the first entry, when the request arrives
    to_state: str
    reason: str
    logged_at: str = field(default_factory=_wall_clock)   # real date and time

#One logged state change: sim time, from, to, reason and real date/time
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class CrisisTracker:
    request_id: str
    state: str = OPEN
    history: list = field(default_factory=list)   # list of Transition

#Tracks one request's current state and its full history of changes
#=======================================================================================================================

#=======================================================================================================================
    def record_open(self, time, reason):
        """Log the starting Open state, so arrival has a timestamp and reason too."""
        if self.history:
            raise InvalidTransition(f"{self.request_id} is already being tracked")
        self.history.append(Transition(time, "", OPEN, reason))

#Logs the starting Open state, so arrival also has a time and reason
#=======================================================================================================================

#=======================================================================================================================
    def can_move_to(self, new_state):
        return new_state in ALLOWED[self.state]

#True if the move is allowed from the current state
#=======================================================================================================================

#=======================================================================================================================
    def move_to(self, new_state, time, reason):
        if new_state not in ALLOWED:
            raise InvalidTransition(f"Unknown state: {new_state!r}")
        if not self.can_move_to(new_state):
            raise InvalidTransition(
                f"{self.request_id}: can't go from {self.state} to {new_state}"
            )
        self.history.append(Transition(time, self.state, new_state, reason))
        self.state = new_state

#Checks the move is allowed, logs it with its time and reason, then changes the state
#=======================================================================================================================

#=======================================================================================================================
    @property
    def is_finished(self):
        return self.state in FINAL_STATES

#True once the request is Resolved or Failed
#=======================================================================================================================