"""Publish/subscribe event bus.

Parts of the system announce what happened (publish) without knowing who's
listening. Other parts react (subscribe) without knowing who announced it.
The website subscribes to everything and pushes each event to the browser
over a WebSocket, so the dashboard updates live.
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

from dataclasses import dataclass

CRISIS_CREATED = "crisis-created"
PRIORITY_CHANGED = "priority-changed"
ROUTE_UPDATED = "route-updated"
RESOURCE_DEPLETED = "resource-depleted"
ROAD_CLOSED = "road-closed"
ROAD_OPENED = "road-opened"
VEHICLE_BREAKDOWN = "vehicle-breakdown"
CRISIS_RESOLVED = "crisis-resolved"
STATE_CHANGED = "state-changed"

EVENT_TYPES = [CRISIS_CREATED, PRIORITY_CHANGED, ROUTE_UPDATED, RESOURCE_DEPLETED,
               ROAD_CLOSED, ROAD_OPENED, VEHICLE_BREAKDOWN, CRISIS_RESOLVED,
               STATE_CHANGED]


@dataclass
class Event:
    type: str
    time: float          # simulation minutes
    message: str         # plain-language summary for the event feed
    data: dict


class EventBus:
    def __init__(self):
        self._subscribers = {}   # event type -> list of handlers
        self._all = []           # handlers that want every event
        self.history = []        # every event ever published, in order

    def subscribe(self, event_type, handler):
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        self._subscribers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler):
        self._all.append(handler)

    def publish(self, event_type, time, message, **data):
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        event = Event(event_type, time, message, data)
        self.history.append(event)
        for handler in self._subscribers.get(event_type, []) + self._all:
            handler(event)
        return event
