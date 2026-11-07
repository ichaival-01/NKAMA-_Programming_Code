"""Triage scoring (goal 2, part 1).

Every request gets a score from 0 to 100, made of five parts. Each part is a
fraction from 0 to 1, multiplied by that part's weight. The weights add up to
100, so the score reads like a percentage, and the breakdown answers the
judges' question "why is this request ahead of that one?".

The score does not do any routing itself: the caller passes in travel_time
(minutes from the nearest suitable hub), which keeps this easy to test.
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

import copy
import json
from dataclasses import dataclass

# Defaults. config.json can override any of these without changing code.
DEFAULT_CONFIG = {
    "weights": {               # max points per part; these add up to 100
        "severity": 30,
        "people": 20,
        "food": 10,
        "deadline": 25,
        "location": 15,
    },
    "caps": {
        "people": 500,             # this many people or more = full points
        "food": 100,               # this many units or more = full points
        "deadline_horizon": 240,   # minutes; more spare time than this = 0 points
    },
    "location_weights": {      # fraction of the location points per category
        "hospital": 1.0,
        "shelter": 0.8,
        "community": 0.6,
    },
    "default_location_weight": 0.5,   # any category not listed (Dataset 2)
    "aging_rate": 0.2,                # queue: extra points per minute waited
    "low_stock_threshold": 50,        # warn when a hub's stock drops below this
    "failure_grace": 120,             # minutes past the deadline before giving up
    "fuel": {
        "per_km": 6.0,                # fuel units burned per km driven (0 = off)
        "reserve": 25,                # refuel after a delivery when below this
        "refuel_minutes": 10,         # how long a truck is out of service refuelling
    },
}


def load_config(path=None):
    """Return the defaults, with any sections from a JSON file merged on top."""
    config = copy.deepcopy(DEFAULT_CONFIG)    # never change the defaults themselves
    if path is None:
        return config
    with open(path) as f:
        user = json.load(f)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)         # merge, e.g. just one weight
        else:
            config[key] = value
    return config


@dataclass
class Score:
    total: float
    parts: dict        # part name -> points, e.g. {"severity": 22.5, ...}


def score_request(req, now, travel_time, config):
    """Score a CrisisRequest at simulation time `now` (minutes)."""
    weights = config["weights"]
    caps = config["caps"]
    parts = {}

    # Severity 1 -> 0 points, severity 5 -> full points.
    parts["severity"] = weights["severity"] * (req.severity - 1) / 4

    # More people and more food needed -> more points, capped at full.
    parts["people"] = weights["people"] * min(req.people_affected / caps["people"], 1)
    parts["food"] = weights["food"] * min(req.food_needed / caps["food"], 1)

    # Deadline: how much spare time is left once you account for driving?
    # No spare time (or already late) -> full points.
    slack = req.deadline - now - (travel_time or 0)
    if slack <= 0:
        fraction = 1.0
    else:
        fraction = max(0.0, 1 - slack / caps["deadline_horizon"])
    parts["deadline"] = weights["deadline"] * fraction

    # Location type: unknown categories get the default, so new datasets work.
    location_weight = config["location_weights"].get(
        req.location_category, config["default_location_weight"]
    )
    parts["location"] = weights["location"] * location_weight

    return Score(total=sum(parts.values()), parts=parts)
