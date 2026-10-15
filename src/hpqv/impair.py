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
        recovery = 1 / burst_len
        start = min(1, loss_pct / 100 * recovery / (1 - loss_pct / 100))
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
