"""Per-call voice keys across live rotations (ADR-0004).

Header flags bits 0-1 carry the low two bits of the key generation. The
receiver retains every unexpired generation and makes one AEAD attempt.
"""
import hashlib
import threading
import time

from hpqv import packet, session as session_mod
from hpqv.controls import _RANGES

# The previous key can still be in the DelayLine when the peer processes the
# rotation; include the largest configured delay, jitter, and one control RTT.
RETIRE_AFTER = (_RANGES["delay_ms"][1] + _RANGES["jitter_ms"][1]) / 1000 + 1


class Keyring:
    def __init__(self, session, retire_after=RETIRE_AFTER, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._retire_after = retire_after
        self._gen = 0
        self._cur = session                  # (key, send_prefix, recv_prefix)
        self._keys = {0: session}
        self._expires = {}

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

    def seal(self, seq, frame) -> bytes:
        with self._lock:
            key, send_prefix, _ = self._cur
            generation = self._gen & 0x3
        return packet.seal(key, send_prefix, seq, frame, flags=generation)

    def rotation_ready(self) -> bool:
        with self._lock:
            self._purge_expired()
            return len(self._keys) == 1

    def open_(self, datagram):
        if len(datagram) < 11:
            raise ValueError("short datagram")
        generation = datagram[10] & 0x3  # header is AAD: changing it fails the tag
        with self._lock:
            self._purge_expired()
            matches = [sess for gen, sess in self._keys.items() if gen & 0x3 == generation]
            if not matches:
                raise ValueError("no key for this generation")
            if len(matches) != 1:
                raise ValueError("ambiguous key generation")
            sess = matches[0]
        key, _, recv_prefix = sess
        return packet.open_(key, recv_prefix, datagram)

    def rotate_to(self, generation) -> bool:
        """Peer said ROTATE(generation). Only current + 1 moves the ring, so a
        crossing ROTATE for a generation we already reached is a no-op."""
        with self._lock:
            self._purge_expired()
            if generation != self._gen + 1:
                return False
            self._advance()
            return True

    def rotate_next(self) -> int:
        """Local press: rotate now and return the generation to announce."""
        with self._lock:
            self._purge_expired()
            if len(self._keys) != 1:
                return self._gen
            self._advance()
            return self._gen

    def _advance(self):
        self._expires[self._gen] = self._clock() + self._retire_after
        self._gen += 1
        self._cur = session_mod.rotate(self._cur)
        self._keys[self._gen] = self._cur

    def _purge_expired(self):
        now = self._clock()
        for gen, expires in tuple(self._expires.items()):
            if now >= expires:
                del self._expires[gen]
                del self._keys[gen]
