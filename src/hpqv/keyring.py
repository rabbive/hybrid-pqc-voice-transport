"""Per-call v2 voice keys across live rotations (ADR-0004).

Each direction has its own exporter secret (secure.MediaKeys). Header flags
bits 0-1 carry the generation mod 4; the receiver keeps every unexpired
generation and makes exactly one AEAD attempt with the key the tag names.
One replay window covers every generation, because seq keeps counting.
"""
import threading
import time
from typing import TYPE_CHECKING

from hpqv import packet
from hpqv.controls import _RANGES
from hpqv.packet import Reject
from hpqv.replay import ReplayWindow

if TYPE_CHECKING:
    from hpqv.secure import MediaKeys

# The previous key can still be in the DelayLine when the peer processes the
# rotation; include the largest configured delay, jitter, and one control RTT.
RETIRE_AFTER = (_RANGES["delay_ms"][1] + _RANGES["jitter_ms"][1]) / 1000 + 1


class Keyring:
    def __init__(self, keys: "MediaKeys", retire_after=RETIRE_AFTER, clock=time.monotonic):
        if len(keys.send_secret) != 32 or len(keys.recv_secret) != 32:
            raise ValueError("media secrets must be 32 bytes")
        if len(keys.session_id) != packet.SESSION_ID_LEN:
            raise ValueError("session_id must be 4 bytes")
        self._lock = threading.Lock()
        self._clock = clock
        self._retire_after = retire_after
        self._session_id = bytes(keys.session_id)
        self._send_secret, self._recv_secret = bytes(keys.send_secret), bytes(keys.recv_secret)
        self._gen = 0
        self._send = packet.direction_keys(self._send_secret)
        self._recv = {0: packet.direction_keys(self._recv_secret)}   # generation -> (key, iv)
        self._expires = {}
        self._last_sent = -1
        self._window = ReplayWindow()
        self._rejects = dict.fromkeys(packet.REJECT_REASONS, 0)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._gen

    @property
    def rejects(self) -> dict[str, int]:
        with self._lock:
            return dict(self._rejects)

    def seal(self, seq, frame, extra_flags=0) -> bytes:
        """`extra_flags` may set FLAG_FEC; bits 0-1 always stay the keyring's.
        seq must strictly increase across the ring's life: (key, nonce) never repeats."""
        with self._lock:
            if seq <= self._last_sent:
                raise ValueError("seq must increase: refusing to reuse a nonce")
            flags = (self._gen & packet.GEN_MASK) | (extra_flags & ~packet.GEN_MASK)
            dg = packet.seal(*self._send, self._session_id, seq, frame, flags=flags)
            self._last_sent = seq
            return dg

    def open_(self, dg) -> tuple[int, int, bytes]:
        """(seq, flags, frame). Raises packet.Reject and counts it; nothing but
        the reject counter changes unless the AEAD verifies."""
        with self._lock:
            try:
                session_id, seq, flags = packet.parse_header(dg)
                if session_id != self._session_id:
                    raise Reject("wrong_session")
                self._purge_expired()
                tag = flags & packet.GEN_MASK
                live = [gen for gen in self._recv if gen & packet.GEN_MASK == tag]
                if not live:
                    raise Reject("no_key")
                if not self._window.check(seq):
                    raise Reject("replay")
                result = packet.open_(*self._recv[live[0]], dg)
            except Reject as e:
                self._rejects[e.reason] += 1
                raise
            self._window.accept(seq)
            return result

    def rotation_ready(self) -> bool:
        with self._lock:
            self._purge_expired()
            return len(self._recv) == 1

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
            if len(self._recv) != 1:
                return self._gen
            self._advance()
            return self._gen

    def _advance(self):
        self._expires[self._gen] = self._clock() + self._retire_after
        self._gen += 1
        # The 2-bit tag can name only one generation per slot: the one four
        # back (only reachable through rapid peer rotations) gives way.
        self._recv.pop(self._gen - 4, None)
        self._expires.pop(self._gen - 4, None)
        self._send_secret = packet.next_secret(self._send_secret)
        self._recv_secret = packet.next_secret(self._recv_secret)
        self._send = packet.direction_keys(self._send_secret)
        self._recv[self._gen] = packet.direction_keys(self._recv_secret)

    def _purge_expired(self):
        now = self._clock()
        for gen, expires in tuple(self._expires.items()):
            if now >= expires:
                del self._expires[gen]
                del self._recv[gen]
