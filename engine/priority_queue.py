#=======================================================================================================================
import heapq
import itertools

#Imports heapq for the priority queue and itertools for a counter that breaks ties first come, first served
#=======================================================================================================================

#=======================================================================================================================
class CrisisQueue:
    def __init__(self, aging_rate=0.0):
        self.aging_rate = aging_rate
        self._heap = []                    # entries: (-key, submitted_at, seq, request_id)
        self._current = {}                 # request_id -> seq of its live heap entry
        self._info = {}                    # request_id -> (base_score, submitted_at)
        self._counter = itertools.count()  # 0, 1, 2, ... for first-in-first-out ties

#Priority queue of waiting requests, built on a heap. Tracks each request's live heap entry, its score and its submit
#time
#=======================================================================================================================

#=======================================================================================================================
    def _key(self, base_score, submitted_at):
        return base_score - self.aging_rate * submitted_at

#Heap key = score - aging rate x submit time. Every request ages at the same rate, so this never changes as time passes
#=======================================================================================================================

#=======================================================================================================================
    def _add_entry(self, request_id):
        """Push a fresh heap entry for request_id and mark it as the live one."""
        base_score, submitted_at = self._info[request_id]
        seq = next(self._counter)
        self._current[request_id] = seq
        # heapq is a MIN-heap, so the key is negated to pop the HIGHEST first
        heapq.heappush(self._heap, (-self._key(base_score, submitted_at),
                                    submitted_at, seq, request_id))

#Pushes a new heap entry and marks it as the live one. The key is negated because heapq pops the smallest first
#=======================================================================================================================

#=======================================================================================================================
    def push(self, request_id, base_score, submitted_at):
        if request_id in self._current:
            raise ValueError(f"Request {request_id} is already queued")
        self._info[request_id] = (base_score, submitted_at)
        self._add_entry(request_id)

#Adds a new request to the queue; an id that's already queued raises an error
#=======================================================================================================================

#=======================================================================================================================
    def update(self, request_id, base_score):
        """New score (e.g. a road closure changed travel time)."""
        if request_id not in self._current:
            raise KeyError(request_id)
        _, submitted_at = self._info[request_id]
        self._info[request_id] = (base_score, submitted_at)
        self._add_entry(request_id)        # the old entry is now stale

#Gives a request a new score by pushing a fresh entry; the old one becomes stale (lazy deletion)
#=======================================================================================================================

#=======================================================================================================================
    def rescore_all(self, get_score):
        """Re-score every queued request. get_score(request_id) -> new base score.

        Example, once per clock tick:
            queue.rescore_all(lambda rid: score_request(requests[rid], now,
                                                        travel[rid], config).total)
        """
        for request_id in list(self._current):   # list(): update() changes the dict
            self.update(request_id, get_score(request_id))

#Re-scores every queued request with the function passed in (called once per clock tick)
#=======================================================================================================================

#=======================================================================================================================
    def remove(self, request_id):
        """Take a request out without popping it (e.g. cancelled)."""
        if request_id not in self._current:
            raise KeyError(request_id)
        del self._current[request_id]      # its heap entry is now stale
        del self._info[request_id]

#Takes a request out without popping it; its heap entry becomes stale
#=======================================================================================================================

#=======================================================================================================================
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

#Removes and returns the most urgent request, skipping stale entries. None if empty
#=======================================================================================================================

#=======================================================================================================================
    def priority(self, request_id, now):
        """Current priority including the waiting-time boost (for the dashboard)."""
        base_score, submitted_at = self._info[request_id]
        return base_score + self.aging_rate * (now - submitted_at)

#Current priority = score + aging rate x minutes waited, for the dashboard
#=======================================================================================================================

#=======================================================================================================================
    def base_score(self, request_id):
        return self._info[request_id][0]

#A request's score without the waiting boost
#=======================================================================================================================

#=======================================================================================================================
    def ordered(self):
        """All queued request ids, most urgent first, without removing them."""
        live = [e for e in self._heap if self._current.get(e[3]) == e[2]]
        return [e[3] for e in sorted(live)]

#All queued requests, most urgent first, without removing them
#=======================================================================================================================

#=======================================================================================================================
    def __len__(self):
        return len(self._current)

#Number of requests in the queue
#=======================================================================================================================

#=======================================================================================================================
    def __contains__(self, request_id):
        return request_id in self._current

#Lets `request_id in queue` work
#=======================================================================================================================