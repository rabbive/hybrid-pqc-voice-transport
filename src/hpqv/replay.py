"""Sliding anti-replay window over the 48-bit voice seq (RFC 4303 section 3.4.3 style).

The window covers [top - size + 1, top], top = highest accepted seq. Anything
older than that (seq <= top - size) is rejected, as is any seq already accepted.
"""
from hpqv.packet import MAX_SEQ


class ReplayWindow:
    def __init__(self, size: int = 256):
        if size < 1:
            raise ValueError("window size must be >= 1")
        self._size = size
        self._top = -1           # nothing accepted yet
        self._bits = 0           # bit i set = seq (top - i) accepted

    def check(self, seq: int) -> bool:
        """Pure: True if seq is new and inside the window."""
        if not 0 <= seq <= MAX_SEQ:
            return False
        if seq > self._top:
            return True
        behind = self._top - seq
        return behind < self._size and not self._bits >> behind & 1

    def accept(self, seq: int) -> None:
        """Mark seq as seen. Call only after the AEAD verifies."""
        if not self.check(seq):
            raise ValueError("seq is a replay or outside the window")
        if seq > self._top:
            shift = seq - self._top
            # Cap the shift so a huge forward jump never builds a huge int.
            self._bits = 1 if shift >= self._size else (self._bits << shift | 1) & ((1 << self._size) - 1)
            self._top = seq
        else:
            self._bits |= 1 << (self._top - seq)
