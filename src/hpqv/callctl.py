"""In-call control channel: live key rotation and hang-up over the TLS channel.

The GUI creates a CallHandle and passes it to demo._run_call; the call starts
it once the secure channel exists. A reader thread applies the peer's
KEY_ROTATE and CALL_END. Control messages are authenticated by TLS, so
anything unexpected from the peer (unknown type, bad payload, a generation
that cannot be next) is a protocol error and ends the call, unlike v1 where
it was dropped because it could have been forged. The three ways a call can
end from the far side are kept apart: `peer_ended` (an authenticated
CALL_END), `connection_lost` (the TCP connection went away) and
`protocol_error`.
"""
import logging
import struct
import threading

from hpqv import control
from hpqv.keyring import Keyring

log = logging.getLogger(__name__)

POLL = 0.2          # how often the reader notices a local stop


class CallHandle:
    def __init__(self):
        self._send_lock = threading.Lock()
        self._channel = self._ring = self._stats = self._stop = self._reader = None
        self._early_hang_up = False   # hang_up() before the call started
        self._end_sent = False
        self.peer_ended = False       # the peer sent CALL_END
        self.connection_lost = False  # the connection closed without a CALL_END
        self.protocol_error = False   # the peer sent something it must not

    def start(self, channel, stats, stop_event) -> Keyring:
        self._channel, self._stats, self._stop = channel, stats, stop_event
        self._ring = Keyring(channel.media_keys())
        if self._early_hang_up:
            stop_event.set()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        return self._ring

    def rotate_keys(self) -> int:
        if self._ring is None:
            raise RuntimeError("no call in progress")
        # Rotate and announce under one lock so ROTATE(n) always precedes
        # ROTATE(n + 1) on the wire; the peer only accepts current + 1.
        with self._send_lock:
            current = self._ring.generation
            gen = self._ring.rotate_next()
            if gen == current:
                return current
            self._mirror(gen)
            self._send(control.MSG_KEY_ROTATE, struct.pack(">I", gen))
        return gen

    def rotation_ready(self) -> bool:
        return self._ring is not None and self._ring.rotation_ready()

    def hang_up(self) -> None:
        if self._stop is None:
            self._early_hang_up = True
            return
        self._end_call()
        self._stop.set()

    def key_generation(self) -> int:
        return self._ring.generation if self._ring else 0

    def sas(self) -> str:
        """The short code both peers read aloud; empty before the call starts."""
        return self._channel.sas() if self._channel else ""

    def finish(self) -> None:
        """Call teardown: tell the peer unless they ended it or are gone, stop the reader."""
        if self._ring is None:
            return
        if not (self.peer_ended or self.connection_lost):
            self._end_call()
        self._reader.join(timeout=2 * POLL + 1)

    def _end_call(self):
        with self._send_lock:
            if self._end_sent:
                return
            self._end_sent = True
            self._send(control.MSG_CALL_END)

    def _send(self, msg_type, payload=b""):
        try:
            self._channel.send(msg_type, payload)
        except OSError:          # ConnectionError too: the reader sees the close and ends the call
            pass

    def _mirror(self, gen):
        with self._stats.lock:
            self._stats.key_gen = gen

    def _read(self):
        try:
            while not self._stop.is_set():
                try:
                    msg = self._channel.recv(timeout=POLL)
                except ConnectionError:
                    self.connection_lost = True
                    return
                except ValueError:                 # malformed frame (the channel is now dead)
                    self._protocol_error("malformed control frame")
                    return
                if msg is not None and not self._apply(*msg):
                    return
        finally:
            self._stop.set()

    def _apply(self, msg_type, payload) -> bool:
        """Handle one authenticated message; False ends the call."""
        if msg_type == control.MSG_CALL_END and not payload:
            self.peer_ended = True
            return False
        if msg_type == control.MSG_KEY_ROTATE and len(payload) == 4:
            (gen,) = struct.unpack(">I", payload)
            with self._send_lock:
                # A generation we already reached is a crossing rotate (both
                # sides pressed at once): nothing to do. One beyond current + 1
                # cannot come from an honest peer, which rotates one step at a time.
                if gen > self._ring.generation + 1:
                    self._protocol_error(f"rotate to generation {gen} out of sequence")
                    return False
                if self._ring.rotate_to(gen):
                    self._mirror(gen)
            return True
        self._protocol_error(f"unexpected control message type {msg_type}, {len(payload)} B")
        return False

    def _protocol_error(self, why):
        self.protocol_error = True
        log.warning("control protocol error, ending call", extra={"why": why})
