"""Heap-based priority queue with aging (goal 2, part 2).

Aging: a request's priority grows the longer it waits:
    priority(now) = base_score + aging_rate * (now - submitted_at)

Neat trick: every request gains aging_rate points per minute, so the
"+ aging_rate * now" part is the same for everyone and never changes the
order. That means the heap can be sorted by a key that never changes:
    key = base_score - aging_rate * submitted_at
An older request (smaller submitted_at) gets a bigger key, so it eventually
beats newer, higher-scoring ones. Aging alone never needs a re-sort.

BUT the base score itself changes over time: its deadline part grows as a
deadline gets closer, and a road closure can change travel time. So whenever
the simulation clock moves forward (or a road closes), call
rescore_all(...) with the new scores. It uses update() for every request.

Tie-breaking: equal keys -> earlier submitted_at first -> then whoever was
queued first (a running counter).

Updates and removals use "lazy deletion", the same trick as the stale-entry
skip in Dijkstra: push a fresh entry, remember which one is current, and
skip old entries when they come out of the heap.
"""
from __future__ import annotations  # lets newer type hints work on Python 3.8

import heapq
import itertools


class CrisisQueue:
    def __init__(self, aging_rate=0.0):
        self.aging_rate = aging_rate
        self._heap = []                    # entries: (-key, submitted_at, seq, request_id)
        self._current = {}                 # request_id -> seq of its live heap entry
        self._info = {}                    # request_id -> (base_score, submitted_at)
        self._counter = itertools.count()  # 0, 1, 2, ... for first-in-first-out ties

    def _key(self, base_score, submitted_at):
        return base_score - self.aging_rate * submitted_at

    def _add_entry(self, request_id):
        """Push a fresh heap entry for request_id and mark it as the live one."""
        base_score, submitted_at = self._info[request_id]
        seq = next(self._counter)
        self._current[request_id] = seq
        # heapq is a MIN-heap, so the key is negated to pop the HIGHEST first
        heapq.heappush(self._heap, (-self._key(base_score, submitted_at),
                                    submitted_at, seq, request_id))

    def push(self, request_id, base_score, submitted_at):
        if request_id in self._current:
            raise ValueError(f"Request {request_id} is already queued")
        self._info[request_id] = (base_score, submitted_at)
        self._add_entry(request_id)

    def update(self, request_id, base_score):
        """New score (e.g. a road closure changed travel time)."""
        if request_id not in self._current:
            raise KeyError(request_id)
        _, submitted_at = self._info[request_id]
        self._info[request_id] = (base_score, submitted_at)
        self._add_entry(request_id)        # the old entry is now stale

    def rescore_all(self, get_score):
        """Re-score every queued request. get_score(request_id) -> new base score.

        Example, once per clock tick:
            queue.rescore_all(lambda rid: score_request(requests[rid], now,
                                                        travel[rid], config).total)
        """
        for request_id in list(self._current):   # list(): update() changes the dict
            self.update(request_id, get_score(request_id))

    def remove(self, request_id):
        """Take a request out without popping it (e.g. cancelled)."""
        if request_id not in self._current:
            raise KeyError(request_id)
        del self._current[request_id]      # its heap entry is now stale
        del self._info[request_id]

    def pop(self):
        """Remove and return the most urgent request id, or None if empty."""
        while self._heap:
            _, _, seq, request_id = heapq.heappop(self._heap)
            if self._current.get(request_id) != seq:
                continue                   # stale entry: skip it
            del self._current[request_id]
            del self._info[request_id]
            return request_id
        return None

    def priority(self, request_id, now):
        """Current priority including the waiting-time boost (for the dashboard)."""
        base_score, submitted_at = self._info[request_id]
        return base_score + self.aging_rate * (now - submitted_at)

    def base_score(self, request_id):
        return self._info[request_id][0]

    def ordered(self):
        """All queued request ids, most urgent first, without removing them."""
        live = [e for e in self._heap if self._current.get(e[3]) == e[2]]
        return [e[3] for e in sorted(live)]

    def __len__(self):
        return len(self._current)

    def __contains__(self, request_id):
        return request_id in self._current
