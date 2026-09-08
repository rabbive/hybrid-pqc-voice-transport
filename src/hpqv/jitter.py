import heapq

class JitterBuffer:
    def __init__(self, depth: int = 3):
        self.depth = depth
        self._heap = []
        self._seen = set()
        self._last = -1

    def push(self, seq: int, frame: bytes) -> None:
        if seq <= self._last or seq in self._seen:
            return
        self._seen.add(seq)
        heapq.heappush(self._heap, (seq, frame))

    def pop(self):
        if len(self._heap) < self.depth:
            return None
        seq, frame = heapq.heappop(self._heap)
        self._seen.discard(seq)
        self._last = seq
        return frame

    def flush(self):
        out = []
        while self._heap:
            seq, frame = heapq.heappop(self._heap)
            self._seen.discard(seq)
            self._last = seq
            out.append(frame)
        return out
