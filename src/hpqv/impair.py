"""Small stateful network impairments for the live media path."""
import heapq
import random
import threading
import time


class LossModel:
    def __init__(self, seed=None):
        self._rng = random.Random(seed)
        self._bad = False
        self._model = None

    @staticmethod
    def effective_loss_pct(loss_pct, model="random", burst_len=3):
        """Return the achievable target; burst mode tops out at 100*B/(B+1)%.

        A 100% request remains an explicit drop-everything setting.
        """
        if model == "random" or loss_pct <= 0 or loss_pct >= 100:
            return loss_pct
        if model != "burst":
            raise ValueError(f"unknown loss model: {model}")
        return min(loss_pct, 100 * burst_len / (burst_len + 1))

    def drop(self, loss_pct, model="random", burst_len=3):
        if loss_pct <= 0:
            self._bad = False
            return False
        if loss_pct >= 100:
            self._bad = True
            return True
        if model == "random":
            self._bad = False
            return self._rng.random() < loss_pct / 100
        if model != "burst":
            raise ValueError(f"unknown loss model: {model}")
        if self._model != model:
            self._bad = False
            self._model = model
        loss_pct = self.effective_loss_pct(loss_pct, model, burst_len)
        recovery = 1 / burst_len
        target = loss_pct / 100
        start = min(1, target * recovery / (1 - target))
        if self._bad:
            if self._rng.random() < recovery:
                self._bad = False
        elif self._rng.random() < start:
            self._bad = True
        return self._bad


class DelayLine:
    def __init__(self, deliver, seed=None):
        self._deliver = deliver
        self._rng = random.Random(seed)
        self._condition = threading.Condition()
        self._heap = []
        self._sequence = 0
        self._closed = False
        self._error = None
        self._thread = None

    def send(self, value, delay_ms=0, jitter_ms=0):
        with self._condition:
            if self._error is not None:
                raise self._error
            if self._closed:
                return
            if delay_ms == 0 and jitter_ms == 0:
                self._deliver(value)
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            delay = max(0.0, delay_ms + self._rng.uniform(-jitter_ms, jitter_ms)) / 1000
            heapq.heappush(self._heap, (time.monotonic() + delay, self._sequence, value))
            self._sequence += 1
            self._condition.notify()

    def _run(self):
        with self._condition:
            while not self._closed:
                if not self._heap:
                    self._condition.wait()
                    continue
                due, _sequence, value = self._heap[0]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                heapq.heappop(self._heap)
                try:
                    self._deliver(value)
                    self._condition.notify_all()
                except BaseException as error:
                    self._error = error
                    self._closed = True
                    self._heap.clear()
                    self._condition.notify_all()

    def close(self, drain=False):
        with self._condition:
            while drain and self._heap and self._error is None:
                self._condition.wait()
            self._closed = True
            self._heap.clear()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join()
        if self._error is not None:
            raise self._error


class DupModel:
    def __init__(self, seed=None):
        self._rng = random.Random(seed)

    def duplicate(self, dup_pct):
        if dup_pct <= 0:
            return False
        if dup_pct >= 100:
            return True
        return self._rng.random() < dup_pct / 100


class TokenBucket:
    """Shaper: reserve() returns how long a packet must wait to stay at the rate."""

    def __init__(self, rate_bytes_per_s, burst_bytes, clock=time.monotonic):
        if rate_bytes_per_s <= 0 or burst_bytes <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = rate_bytes_per_s
        self._burst = burst_bytes
        self._clock = clock
        self._tokens = float(burst_bytes)
        self._last = clock()
        self._lock = threading.Lock()

    def reserve(self, nbytes):
        with self._lock:
            now = self._clock()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            self._tokens -= nbytes
            return max(0.0, -self._tokens / self._rate)
