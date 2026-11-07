from __future__ import annotations  # lets newer type hints work on Python 3.8

from dataclasses import dataclass

REQUIRED_FIELDS = [
    "id", "location", "crisis_type", "location_category", "people_affected",
    "food_needed", "needs_refrigeration", "severity", "deadline", "submitted_at",
]


@dataclass
class CrisisRequest:
    id: str                    # e.g. "R1"
    location: int              # node id where food is needed
    crisis_type: str           # e.g. "flood", "fire", "power outage"
    location_category: str     # e.g. "hospital", "shelter", "community"
    people_affected: int
    food_needed: int           # units of food
    needs_refrigeration: bool
    severity: int              # 1 (low) to 5 (critical)
    deadline: float            # minutes on the simulation clock
    submitted_at: float        # minutes on the simulation clock


class RequestError(Exception):
    """Holds a list of every problem found, so the UI can show them all."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


def _is_int(value):
    """True for whole numbers. Excludes True/False, which Python counts as ints."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value):
    """True for ints and decimals, but not True/False."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_request(data, dataset):
    """Check a request dict. Return a CrisisRequest, or raise RequestError."""
    # 1. Missing fields: stop early, since later checks need every field.
    missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] is None]
    if missing:
        raise RequestError([f"Missing field: {f}" for f in missing])

    # 2. Collect every problem instead of stopping at the first one.
    errors = []

    # Worked example: severity must be a whole number from 1 to 5.
    if not _is_int(data["severity"]) or not 1 <= data["severity"] <= 5:
        errors.append("severity must be a whole number from 1 to 5")

    if not isinstance(data["id"], str) or not data["id"].strip():
        errors.append("id must not be empty (empty string thrown)")

    if not _is_int(data["location"]) or data["location"] not in dataset.nodes: 
        errors.append(f"location {data['location']!r} is not registered...")

    for field in ("crisis_type", "location_category"):
        if not isinstance (data[field], str) or not data[field].strip():
            errors.append(f"{field} cannot be empty!")

    for field in ("people_affected", "food_needed"):
        if not _is_int(data[field]) or data[field] <= 0:
            errors.append(f"{field} must be given a value higher than zero!")

    if not isinstance(data["needs_refrigeration"], bool):
        errors.append("needs_refrigeration is required for proper delivery!")

    for field in ("deadline", "submitted_at"):
        if not _is_num(data[field]) or data[field] < 0:
            errors.append(f"{field} Must provide a deadline in minutes. This is crucial")

    if not errors and data["deadline"] <= data["submitted_at"]:
        errors.append("deadline must be after the current time please...")

    if errors:
        raise RequestError(errors)

    # 3. Everything checked out.
    return CrisisRequest(**{f: data[f] for f in REQUIRED_FIELDS})