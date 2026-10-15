"""In-call control channel: live key rotation and hang-up over the TCP socket.

The GUI creates a CallHandle and passes it to demo._run_call; the call starts
it once the session exists. A reader thread applies the peer's KEY_ROTATE and
CALL_END; losing the TCP connection ends the call too.
"""
import select
import struct
import threading

from hpqv import control
from hpqv.keyring import Keyring

POLL = 0.2          # how often the reader notices a local stop


class CallHandle:
    def __init__(self):
        self._send_lock = threading.Lock()
        self._sock = self._ring = self._stats = self._stop = self._reader = None
        self._early_hang_up = False   # hang_up() before the call started
        self._end_sent = False
        self._peer_ended = False

    def start(self, sock, session, stats, stop_event) -> Keyring:
        self._sock, self._stats, self._stop = sock, stats, stop_event
        self._ring = Keyring.of(session)
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
            gen = self._ring.rotate_next()
            self._mirror(gen)
            self._send(control.MSG_KEY_ROTATE, struct.pack(">I", gen))
        return gen

    def hang_up(self) -> None:
        if self._stop is None:
            self._early_hang_up = True
            return
        self._end_call()
        self._stop.set()

    def key_generation(self) -> int:
        return self._ring.generation if self._ring else 0

    def key_fingerprint(self) -> str:
        return self._ring.fingerprint() if self._ring else ""

    def finish(self) -> None:
        """Call teardown: tell the peer unless they ended it, stop the reader."""
        if self._ring is None:
            return
        if not self._peer_ended:
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
            control.send_control(self._sock, msg_type, payload)
        except OSError:
            pass                 # peer gone: the reader sees the close and ends the call

    def _mirror(self, gen):
        with self._stats.lock:
            self._stats.key_gen = gen

    def _read(self):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._sock], [], [], POLL)
                if not ready:
                    continue
                msg_type, payload = control.recv_control(self._sock)
            except IndexError:                # empty record: framing intact, skip it
                continue
            except (OSError, ValueError):     # ConnectionError is an OSError
                self._peer_ended = True
                break
            if msg_type == control.MSG_CALL_END:
                self._peer_ended = True
                break
            # Anything malformed or out of sequence is dropped: the channel is
            # not authenticated, and a wrong generation must never move the ring.
            if msg_type == control.MSG_KEY_ROTATE and len(payload) == 4:
                with self._send_lock:
                    (gen,) = struct.unpack(">I", payload)
                    if self._ring.rotate_to(gen):
                        self._mirror(gen)
        self._stop.set()
