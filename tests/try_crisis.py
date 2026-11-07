from engine.loader import load_dataset
from engine.crisis import validate_request, RequestError

ds = load_dataset("data/toronto-downtown.json")

good = {
    "id": "R1", "location": 22, "crisis_type": "flood",
    "location_category": "shelter", "people_affected": 120,
    "food_needed": 30, "needs_refrigeration": False,
    "severity": 4, "deadline": 90, "submitted_at": 0,
}

def check(label, data):
    try:
        req = validate_request(data, ds)
        print(f"{label}: OK -> {req}")
    except RequestError as e:
        print(f"{label}: REJECTED -> {e.errors}")

check("valid request", good)
check("bad severity", {**good, "severity": 9})
check("unknown location", {**good, "location": 999})
check("three mistakes", {**good, "severity": 9, "people_affected": -3,
                         "needs_refrigeration": "yes"})
check("missing field", {k: v for k, v in good.items() if k != "deadline"})