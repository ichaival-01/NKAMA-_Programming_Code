"""Tests for engine/loader.py.

Run from the project root with:  pytest
These tests are a starting point. Adjust attribute names if your Dataset
class ends up different, and finish the TODO tests yourselves.
"""
import copy
import json
from pathlib import Path

import pytest

from engine.loader import load_dataset, DatasetError

# Build the path from this file's location, so tests work no matter
# which folder you run pytest from.
DATA_FILE = Path(__file__).parent.parent / "data" / "toronto-downtown.json"


# ---------- helpers ----------

@pytest.fixture
def raw():
    """A fresh copy of the real JSON as a Python dict, safe to modify."""
    with open(DATA_FILE) as f:
        return json.load(f)


def write_json(tmp_path, data):
    """Save a (possibly broken) dataset to a temp file and return its path."""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(data))
    return path


# ---------- the real file loads correctly ----------

def test_real_file_counts():
    ds = load_dataset(DATA_FILE)
    assert len(ds.nodes) == 48
    assert len(ds.edges) == 82
    assert len(ds.pois) == 7
    assert len(ds.hubs) == 3
    assert len(ds.trucks) == 12


def test_node_fields():
    ds = load_dataset(DATA_FILE)
    node = ds.nodes[0]
    assert node.name == "Bathurst St & Front St"
    assert node.lat == pytest.approx(43.6432)
    assert node.lng == pytest.approx(-79.4096)


def test_edge_cost_comes_from_w():
    ds = load_dataset(DATA_FILE)
    edge = ds.edges[0]
    assert (edge.a, edge.b) == (0, 1)
    assert edge.cost == pytest.approx(2.4136)  # "w" in the file
    assert edge.km == pytest.approx(0.885)


def test_optional_edge_fields_get_defaults(raw, tmp_path):
    # Dataset 2 might leave these out; the loader should not crash.
    del raw["edges"][0]["hwy"]
    del raw["edges"][0]["terrain"]
    ds = load_dataset(write_json(tmp_path, raw))
    assert ds.edges[0].hwy is False
    assert ds.edges[0].terrain == "urban"


# ---------- broken files raise DatasetError ----------

def test_missing_top_level_key(raw, tmp_path):
    del raw["edges"]
    with pytest.raises(DatasetError):
        load_dataset(write_json(tmp_path, raw))


def test_duplicate_node_id(raw, tmp_path):
    raw["nodes"].append(copy.deepcopy(raw["nodes"][0]))
    with pytest.raises(DatasetError):
        load_dataset(write_json(tmp_path, raw))


def test_edge_points_to_missing_node(raw, tmp_path):
    raw["edges"][0]["b"] = 999
    with pytest.raises(DatasetError):
        load_dataset(write_json(tmp_path, raw))


@pytest.mark.parametrize("bad_cost", [0, -1, "abc", None])
def test_bad_edge_cost(raw, tmp_path, bad_cost):
    raw["edges"][0]["w"] = bad_cost
    with pytest.raises(DatasetError):
        load_dataset(write_json(tmp_path, raw))


def test_poi_on_missing_node(raw, tmp_path):
    raw["pois"][0]["node"] = 999
    with pytest.raises(DatasetError):
        load_dataset(write_json(tmp_path, raw))


# ---------- TODO: write these yourselves ----------

# def test_duplicate_edge_id(raw, tmp_path): ...
# def test_hub_with_unknown_poi(raw, tmp_path): ...     # poiId "P99"
# def test_truck_home_missing(raw, tmp_path): ...
# def test_truck_negative_capacity(raw, tmp_path): ...
# def test_file_does_not_exist(): ...
#     Team decision: should this raise DatasetError or FileNotFoundError?