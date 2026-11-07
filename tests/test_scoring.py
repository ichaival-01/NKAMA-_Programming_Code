"""Tests for engine/scoring.py (triage score with a breakdown).

Run from the project root with:  python -m pytest tests/test_scoring.py -v
"""
import json

import pytest

from engine.crisis import CrisisRequest
from engine.scoring import DEFAULT_CONFIG, load_config, score_request


def make_request(**changes):
    """A baseline request; pass keyword arguments to change single fields."""
    fields = dict(
        id="R1", location=22, crisis_type="flood", location_category="shelter",
        people_affected=120, food_needed=30, needs_refrigeration=False,
        severity=4, deadline=90, submitted_at=0,
    )
    fields.update(changes)
    return CrisisRequest(**fields)


@pytest.fixture
def config():
    return load_config()          # the defaults, no file


def total(config, now=0, travel=10, **changes):
    return score_request(make_request(**changes), now, travel, config).total


# ---------- the breakdown ----------

def test_known_score_matches_hand_calculation(config):
    # severity 4  -> (4-1)/4 * 30          = 22.5
    # people 120  -> 120/500 * 20          =  4.8
    # food 30     -> 30/100 * 10           =  3.0
    # deadline    -> slack 90-0-10 = 80 min, (1 - 80/240) * 25 = 16.667
    # shelter     -> 0.8 * 15              = 12.0
    score = score_request(make_request(), now=0, travel_time=10, config=config)
    assert score.parts["severity"] == pytest.approx(22.5)
    assert score.parts["people"] == pytest.approx(4.8)
    assert score.parts["food"] == pytest.approx(3.0)
    assert score.parts["deadline"] == pytest.approx(16.667, abs=0.001)
    assert score.parts["location"] == pytest.approx(12.0)
    assert score.total == pytest.approx(58.967, abs=0.001)


def test_total_is_sum_of_parts(config):
    score = score_request(make_request(), 0, 10, config)
    assert score.total == pytest.approx(sum(score.parts.values()))


def test_each_part_stays_within_its_weight(config):
    extreme = make_request(severity=5, people_affected=10_000, food_needed=10_000,
                           deadline=1, location_category="hospital")
    score = score_request(extreme, now=500, travel_time=60, config=config)
    for name, points in score.parts.items():
        assert 0 <= points <= config["weights"][name] + 1e-9
    assert score.total == pytest.approx(100)


# ---------- each factor pushes the right way ----------

def test_higher_severity_scores_higher(config):
    assert total(config, severity=5) > total(config, severity=2)


def test_more_people_scores_higher(config):
    assert total(config, people_affected=400) > total(config, people_affected=50)


def test_more_food_needed_scores_higher(config):
    assert total(config, food_needed=80) > total(config, food_needed=10)


def test_hospital_beats_community(config):
    assert total(config, location_category="hospital") > total(config, location_category="community")


def test_unknown_category_uses_default_weight(config):
    score = score_request(make_request(location_category="school"), 0, 10, config)
    assert score.parts["location"] == pytest.approx(0.5 * 15)


def test_closer_deadline_scores_higher(config):
    assert total(config, deadline=30) > total(config, deadline=200)


def test_longer_travel_scores_higher(config):
    # Same deadline, but a far-away site has less spare time, so it's more urgent.
    assert total(config, travel=60) > total(config, travel=5)


def test_time_passing_raises_deadline_urgency(config):
    assert total(config, now=60) > total(config, now=0)


def test_missed_deadline_gets_full_deadline_points(config):
    score = score_request(make_request(deadline=30), now=100, travel_time=10, config=config)
    assert score.parts["deadline"] == pytest.approx(25)


def test_unknown_travel_time_counts_as_zero(config):
    # e.g. no route exists yet; the request should still get a score.
    assert total(config, travel=None) == pytest.approx(total(config, travel=0))


# ---------- config ----------

def test_config_file_overrides_only_what_it_sets(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"weights": {"severity": 50}, "aging_rate": 1.0}))
    cfg = load_config(path)
    assert cfg["weights"]["severity"] == 50
    assert cfg["weights"]["people"] == DEFAULT_CONFIG["weights"]["people"]
    assert cfg["aging_rate"] == 1.0


def test_load_config_does_not_change_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"weights": {"severity": 99}}))
    load_config(path)
    assert DEFAULT_CONFIG["weights"]["severity"] == 30
