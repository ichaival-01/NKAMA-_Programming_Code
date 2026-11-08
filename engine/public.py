#=======================================================================================================================
from engine import states as st

#Imports the state names so the public page can translate them
#=======================================================================================================================

#=======================================================================================================================
# The progress bar on the public page: a real sequence, in order.
STEPS = ["Received", "In the queue", "Truck assigned", "On the way", "Delivered"]

TITLES = {
    st.OPEN: "Received",
    st.PRIORITIZED: "In the queue",
    st.RESOURCE_ASSIGNED: "Truck assigned",
    st.IN_PROGRESS: "On the way",
    st.RE_ROUTING: "Finding another route",
    st.DELAYED: "Delayed",
    st.DELIVERED: "Delivered",
    st.RESOLVED: "Delivery confirmed",
    st.FAILED: "Could not be delivered",
}

#The five steps of the public progress bar, and the plain-language title shown for each internal state
#=======================================================================================================================

#=======================================================================================================================
def _explain(transition):
    """Turn an internal state change into a sentence for the public."""
    state, reason = transition.to_state, transition.reason
    low = reason.lower()
    if state == st.OPEN:
        return "We received your request."
    if state == st.PRIORITIZED:
        return ("Your request is in the queue. The most urgent requests go first, "
                "and every request moves up the longer it waits.")
    if state == st.RESOURCE_ASSIGNED:
        return f"Assigned: {reason.split(':')[0]}."
    if state == st.IN_PROGRESS:
        if "departed" in reason:
            return f"{reason.split(' departed')[0]} is on the way to you."
        return "The truck found another route and is on the way to you."
    if state == st.RE_ROUTING:
        return "A road on the truck's route closed. Finding another way."
    if state == st.DELAYED:
        if "no open route" in low or "no route" in low:
            return ("Roads to your location are closed right now. A truck will go "
                    "as soon as one reopens.")
        if "broke down" in low:
            return "The truck broke down. A replacement is being arranged."
        if "no hub has" in low:
            return "The food you need is running low. You'll get the next available supply."
        if "truck" in low:
            return "All suitable trucks are busy. You'll get the next one available."
        if "after the deadline" in low:
            return "The delivery will arrive later than you needed. It is still on its way."
        return "Your delivery is delayed."
    if state == st.DELIVERED:
        return reason.rstrip(".") + "."
    if state == st.RESOLVED:
        return "Delivery confirmed."
    if state == st.FAILED:
        return ("We couldn't complete this delivery. If you still need help, "
                "contact local emergency services.")
    return reason

#Turns an internal state change into a friendly sentence for the public, picked using keywords in the reason (no scores
#or internal details)
#=======================================================================================================================

#=======================================================================================================================
def _step_index(tracker):
    """How far along the progress bar this request has got."""
    reached = 0
    for h in tracker.history:
        if h.to_state == st.PRIORITIZED:
            reached = max(reached, 1)
        elif h.to_state == st.RESOURCE_ASSIGNED:
            reached = max(reached, 2)
        elif h.to_state in (st.IN_PROGRESS, st.RE_ROUTING):
            reached = max(reached, 3)
        elif h.to_state in (st.DELIVERED, st.RESOLVED):
            reached = 4
    return reached

#How far along the progress bar the request has got, based on the furthest state it has reached
#=======================================================================================================================

#=======================================================================================================================
def public_status(d, request_id, code):
    """Plain-language status of one request, or None if it doesn't exist."""
    ds = d.dataset
    scheduled = next((r for r in d.scheduled if r.id == request_id), None)
    req = d.requests.get(request_id) or scheduled
    if req is None:
        return None

    base = {
        "code": code,
        "id": req.id,
        "now": round(d.now, 2),
        "location": req.location,
        "location_name": ds.nodes[req.location].name,
        "crisis_type": req.crisis_type,
        "people_affected": req.people_affected,
        "food_needed": req.food_needed,
        "needs_refrigeration": req.needs_refrigeration,
        "severity": req.severity,
        "submitted_at": req.submitted_at,
        "deadline": req.deadline,
        "steps": STEPS,
    }
    if scheduled is not None:
        return {**base, "state": "Scheduled", "title": "Scheduled", "step": 0,
                "message": f"Your request will be sent at {req.submitted_at:.1f} min.",
                "timeline": [], "eta": None, "minutes_away": None, "truck": None, "route": [],
                "queue_position": None, "finished": False, "problem": False}

    tracker = d.trackers[request_id]
    last = tracker.history[-1]
    message = _explain(last)

    queue = d.queue.ordered()
    position = queue.index(request_id) + 1 if request_id in queue else None
    if position is not None and tracker.state in (st.PRIORITIZED, st.DELAYED):
        message += f" Position in queue: {position} of {len(queue)}."

    trip = d.trips.get(request_id)
    a = d.assignments.get(request_id)
    truck = None
    if a is not None and a.truck_id in d.pool.busy and d.pool.busy[a.truck_id] == request_id:
        lat, lng = d.truck_position(a.truck_id)
        truck = {"name": ds.trucks[a.truck_id].name, "lat": lat, "lng": lng}

    return {
        **base,
        "state": tracker.state,
        "title": TITLES[tracker.state],
        "step": _step_index(tracker),
        "message": message,
        "timeline": [{"time": h.time, "title": TITLES[h.to_state],
                      "detail": _explain(h), "logged_at": h.logged_at}
                     for h in tracker.history],
        "eta": round(trip.arrival, 2) if trip else None,
        "minutes_away": round(max(0.0, trip.arrival - d.now), 1) if trip else None,
        "truck": truck,
        "route": trip.nodes[trip.index_at(d.now):] if trip else [],
        "queue_position": position,
        "finished": tracker.is_finished,
        "problem": tracker.state in (st.DELAYED, st.RE_ROUTING, st.FAILED),
    }

#Builds everything the public page shows for one request: step, message, queue position, truck location, arrival time
#and timeline. None if the request doesn't exist
#=======================================================================================================================