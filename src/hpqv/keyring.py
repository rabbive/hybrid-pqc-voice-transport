"""Per-call voice keys across live rotations (ADR-0004).

Header flags bit 0 carries the sender's key-generation parity. The receiver
holds the current generation and, for a short window, the previous one, and
decrypts with exactly the key the parity names: one AEAD attempt per datagram,
so a failed decrypt never tells anyone which other key would have worked.
"""
import hashlib
import threading
import time

from hpqv import packet, session as session_mod

# The previous key is retired on a timer, not on the first current-generation
# packet: with the delay/jitter knobs, datagrams reorder, so old-generation
# packets legitimately arrive after new ones. 3 s covers the largest one-way
# delay the controls allow (delay_ms 2000 + jitter_ms 1000).
RETIRE_AFTER = 3.0


class Keyring:
    def __init__(self, session, retire_after=RETIRE_AFTER, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._retire_after = retire_after
        self._gen = 0
        self._cur = session                  # (key, send_prefix, recv_prefix)
        self._prev = None
        self._prev_until = 0.0

    @classmethod
    def of(cls, session_or_ring):
        """Loops take either; tests and one-shot callers still pass a tuple."""
        return session_or_ring if isinstance(session_or_ring, cls) else cls(session_or_ring)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._gen

    def fingerprint(self) -> str:
        """Short tag both users can compare aloud; not a secret."""
        with self._lock:
            return hashlib.sha256(self._cur[0]).hexdigest()[:8]

    def seal(self, seq, frame, extra_flags=0) -> bytes:
        """`extra_flags` sets header bits 2-7; bits 0-1 always stay the keyring's."""
        with self._lock:
            key, send_prefix, _ = self._cur
            parity = self._gen & 1
        return packet.seal(key, send_prefix, seq, frame, flags=parity | (extra_flags & ~0x03))

    def open_(self, datagram):
        if len(datagram) < 11:
            raise ValueError("short datagram")
        parity = datagram[10] & 1        # header is AAD: flipping it fails the tag
        with self._lock:
            if self._prev is not None and self._clock() >= self._prev_until:
                self._prev = None
            if parity == self._gen & 1:
                sess = self._cur
            elif self._prev is not None:
                sess = self._prev
            else:
                raise ValueError("no key for this generation")
        key, _, recv_prefix = sess
        return packet.open_(key, recv_prefix, datagram)

    def rotate_to(self, generation) -> bool:
        """Peer said ROTATE(generation). Only current + 1 moves the ring, so a
        crossing ROTATE for a generation we already reached is a no-op."""
        with self._lock:
            if generation != self._gen + 1:
                return False
            self._advance()
            return True

    def rotate_next(self) -> int:
        """Local press: rotate now and return the generation to announce."""
        with self._lock:
            self._advance()
            return self._gen

    def _advance(self):
        self._prev, self._cur = self._cur, session_mod.rotate(self._cur)
        self._prev_until = self._clock() + self._retire_after
        self._gen += 1
