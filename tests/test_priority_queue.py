"""Tests for engine/priority_queue.py (heap queue with aging and tie-breaking).

Run from the project root with:  python -m pytest tests/test_priority_queue.py -v
"""
import pytest

from engine.priority_queue import CrisisQueue


# ---------- basic ordering ----------

def test_pops_highest_score_first():
    q = CrisisQueue()
    q.push("low", 20, submitted_at=0)
    q.push("high", 80, submitted_at=0)
    q.push("mid", 50, submitted_at=0)
    assert [q.pop(), q.pop(), q.pop()] == ["high", "mid", "low"]


def test_empty_queue_pops_none():
    assert CrisisQueue().pop() is None


def test_len_and_contains():
    q = CrisisQueue()
    q.push("A", 10, 0)
    q.push("B", 20, 0)
    assert len(q) == 2 and "A" in q
    q.pop()
    assert len(q) == 1 and "B" not in q


def test_duplicate_push_raises():
    q = CrisisQueue()
    q.push("A", 10, 0)
    with pytest.raises(ValueError):
        q.push("A", 20, 0)


# ---------- ties ----------

def test_equal_scores_earlier_submission_first():
    q = CrisisQueue()
    q.push("late", 50, submitted_at=30)
    q.push("early", 50, submitted_at=10)
    assert q.pop() == "early"


def test_equal_scores_and_times_first_in_first_out():
    q = CrisisQueue()
    q.push("first", 50, submitted_at=0)
    q.push("second", 50, submitted_at=0)
    assert q.pop() == "first"


# ---------- aging (waiting-time adjustment) ----------

def test_without_aging_higher_score_wins():
    q = CrisisQueue(aging_rate=0)
    q.push("old_low", 40, submitted_at=0)
    q.push("new_high", 50, submitted_at=100)
    assert q.pop() == "new_high"


def test_aging_lets_long_waiting_request_win():
    # old_low has waited 100 minutes longer: 0.2 * 100 = +20 points,
    # so 40 + 20 = 60 beats new_high's 50.
    q = CrisisQueue(aging_rate=0.2)
    q.push("old_low", 40, submitted_at=0)
    q.push("new_high", 50, submitted_at=100)
    assert q.pop() == "old_low"


def test_priority_includes_waiting_time():
    q = CrisisQueue(aging_rate=0.2)
    q.push("A", 40, submitted_at=10)
    assert q.priority("A", now=10) == pytest.approx(40)
    assert q.priority("A", now=60) == pytest.approx(50)   # waited 50 min


# ---------- changing conditions ----------

def test_update_changes_order():
    q = CrisisQueue()
    q.push("A", 30, 0)
    q.push("B", 60, 0)
    q.update("A", 90)          # e.g. a road closure made A more urgent
    assert q.pop() == "A"
    assert q.pop() == "B"
    assert q.pop() is None     # the old entry for A must not come back


def test_update_unknown_raises():
    with pytest.raises(KeyError):
        CrisisQueue().update("nope", 50)


def test_remove():
    q = CrisisQueue()
    q.push("A", 90, 0)
    q.push("B", 10, 0)
    q.remove("A")              # e.g. request cancelled
    assert len(q) == 1
    assert q.pop() == "B"
    assert q.pop() is None


def test_rescore_all_reorders_as_deadlines_approach():
    # At t=0, A scores higher. Later B's deadline is close, so its score
    # rises; without re-scoring, the queue would still put A first.
    q = CrisisQueue()
    q.push("A", 48, 0)
    q.push("B", 33, 0)
    new_scores = {"A": 48, "B": 54}
    q.rescore_all(lambda rid: new_scores[rid])
    assert q.ordered() == ["B", "A"]
    assert len(q) == 2


def test_ordered_shows_queue_without_removing():
    q = CrisisQueue()
    q.push("A", 10, 0)
    q.push("B", 30, 0)
    q.push("C", 20, 0)
    q.update("A", 40)
    assert q.ordered() == ["A", "B", "C"]
    assert len(q) == 3
