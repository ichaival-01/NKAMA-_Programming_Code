"""Tests for engine/crisis.py (request validation).

Run from the project root with:  python -m pytest tests/test_crisis.py -v
"""
from pathlib import Path

import pytest

from engine.loader import load_dataset
from engine.crisis import CrisisRequest, RequestError, validate_request

DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


@pytest.fixture
def dataset():
    return load_dataset(DATA_FILE)


@pytest.fixture
def good():
    """A valid request. Each test copies it and breaks one thing."""
    return {
        "id": "R1",
        "location": 22,                 # node id (Moss Park shelter)
        "crisis_type": "flood",
        "location_category": "shelter",
        "people_affected": 120,
        "food_needed": 30,              # units of food
        "needs_refrigeration": False,
        "severity": 4,                  # 1 (low) to 5 (critical)
        "deadline": 90,                 # minutes on the simulation clock
        "submitted_at": 0,              # minutes on the simulation clock
    }


def errors_for(data, dataset):
    """Run validation and return the list of error messages."""
    with pytest.raises(RequestError) as info:
        validate_request(data, dataset)
    return info.value.errors


# ---------- valid requests ----------

def test_valid_request_returns_crisis_request(good, dataset):
    req = validate_request(good, dataset)
    assert isinstance(req, CrisisRequest)
    assert req.id == "R1"
    assert req.location == 22
    assert req.severity == 4
    assert req.needs_refrigeration is False


def test_decimal_times_are_allowed(good, dataset):
    good["submitted_at"] = 2.5
    good["deadline"] = 60.25
    req = validate_request(good, dataset)
    assert req.deadline == pytest.approx(60.25)


# ---------- missing fields ----------

@pytest.mark.parametrize("field", [
    "id", "location", "crisis_type", "location_category", "people_affected",
    "food_needed", "needs_refrigeration", "severity", "deadline", "submitted_at",
])
def test_missing_field(good, dataset, field):
    del good[field]
    errors = errors_for(good, dataset)
    assert any(field in e for e in errors)


def test_none_counts_as_missing(good, dataset):
    good["severity"] = None
    errors = errors_for(good, dataset)
    assert any("severity" in e for e in errors)


# ---------- invalid values ----------

def test_location_not_in_dataset(good, dataset):
    good["location"] = 999
    errors = errors_for(good, dataset)
    assert any("location" in e for e in errors)


@pytest.mark.parametrize("bad", [0, 6, -1, "high", 3.5, True])
def test_bad_severity(good, dataset, bad):
    good["severity"] = bad
    errors = errors_for(good, dataset)
    assert any("severity" in e for e in errors)


@pytest.mark.parametrize("field", ["people_affected", "food_needed"])
@pytest.mark.parametrize("bad", [0, -5, "lots", 2.5, True])
def test_bad_counts(good, dataset, field, bad):
    good[field] = bad
    errors = errors_for(good, dataset)
    assert any(field in e for e in errors)


@pytest.mark.parametrize("bad", ["yes", 1, 0, "true"])
def test_refrigeration_must_be_true_or_false(good, dataset, bad):
    good["needs_refrigeration"] = bad
    errors = errors_for(good, dataset)
    assert any("needs_refrigeration" in e for e in errors)


@pytest.mark.parametrize("field", ["crisis_type", "location_category", "id"])
@pytest.mark.parametrize("bad", ["", "   ", 5])
def test_text_fields_must_be_non_empty(good, dataset, field, bad):
    good[field] = bad
    errors = errors_for(good, dataset)
    assert any(field in e for e in errors)


@pytest.mark.parametrize("field", ["deadline", "submitted_at"])
@pytest.mark.parametrize("bad", [-1, "noon", True])
def test_bad_times(good, dataset, field, bad):
    good[field] = bad
    errors = errors_for(good, dataset)
    assert any(field in e for e in errors)


def test_deadline_must_be_after_submission(good, dataset):
    good["submitted_at"] = 100
    good["deadline"] = 50
    errors = errors_for(good, dataset)
    assert any("deadline" in e for e in errors)


# ---------- helpful error reporting ----------

def test_reports_all_problems_at_once(good, dataset):
    # A form should show every mistake together, not one at a time.
    good["severity"] = 9
    good["people_affected"] = -3
    good["location"] = 999
    errors = errors_for(good, dataset)
    assert len(errors) == 3


def test_new_location_categories_are_accepted(good, dataset):
    # Dataset 2 may have categories we haven't seen, like "school".
    good["location_category"] = "school"
    assert validate_request(good, dataset).location_category == "school"