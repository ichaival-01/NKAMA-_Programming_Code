#=======================================================================================================================
from dataclasses import dataclass, field
from datetime import datetime

#Imports dataclass/field for Event and datetime for real timestamps
#=======================================================================================================================

#=======================================================================================================================
CRISIS_CREATED = "crisis-created"
PRIORITY_CHANGED = "priority-changed"
ROUTE_UPDATED = "route-updated"
RESOURCE_DEPLETED = "resource-depleted"
ROAD_CLOSED = "road-closed"
ROAD_OPENED = "road-opened"
VEHICLE_BREAKDOWN = "vehicle-breakdown"
CRISIS_RESOLVED = "crisis-resolved"
STATE_CHANGED = "state-changed"
VEHICLE_REFUELLING = "vehicle-refuelling"
VEHICLE_READY = "vehicle-ready"
CHAOS_TOGGLED = "chaos-toggled"

EVENT_TYPES = [CRISIS_CREATED, PRIORITY_CHANGED, ROUTE_UPDATED, RESOURCE_DEPLETED,
               ROAD_CLOSED, ROAD_OPENED, VEHICLE_BREAKDOWN, CRISIS_RESOLVED,
               STATE_CHANGED, VEHICLE_REFUELLING, VEHICLE_READY, CHAOS_TOGGLED]

#Every event type the bus can carry, and a list of all of them
#=======================================================================================================================

#=======================================================================================================================
@dataclass
class Event:
    type: str
    time: float          # simulation minutes
    message: str         # plain-language summary for the event feed
    data: dict
    logged_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

#One published event: type, sim time, message for the feed, extra data and real date/time
#=======================================================================================================================

#=======================================================================================================================
class EventBus:
    def __init__(self):
        self._subscribers = {}   # event type -> list of handlers
        self._all = []           # handlers that want every event
        self.history = []        # every event ever published, in order

#Publish/subscribe event bus: handlers for one event type, handlers for every event, and a history of all events
#=======================================================================================================================

#=======================================================================================================================
    def subscribe(self, event_type, handler):
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        self._subscribers.setdefault(event_type, []).append(handler)

#Registers a handler for one event type; unknown types raise an error
#=======================================================================================================================

#=======================================================================================================================
    def subscribe_all(self, handler):
        self._all.append(handler)

#Registers a handler for every event (the website uses this)
#=======================================================================================================================

#=======================================================================================================================
    def publish(self, event_type, time, message, **data):
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        event = Event(event_type, time, message, data)
        self.history.append(event)
        for handler in self._subscribers.get(event_type, []) + self._all:
            handler(event)
        return event

#Creates an event, saves it to history and calls every handler subscribed to it
#=======================================================================================================================