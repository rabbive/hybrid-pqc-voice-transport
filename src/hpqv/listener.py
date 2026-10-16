"""Listener that keeps serving after stray or failed connections (B2; T6 finding 6).

Each accepted socket gets the TLS handshake in a worker thread, at most
MAX_HANDSHAKES at once (extra connections are closed at once), each bounded by
the handshake timeout. An authenticated channel goes to on_call (ALPN hpqv/2,
pinned friends only, one call at a time) or on_pair (ALPN hpqv-pair/1, only
while the PairingWindow allows it). Anything else is logged and dropped, and the
accept loop carries on.
"""
import logging
import os
import socket
import threading

from hpqv.secure import HANDSHAKE_TIMEOUT, HandshakeError, SecureChannel

log = logging.getLogger(__name__)

MAX_HANDSHAKES = 4
ACCEPT_TICK = 0.25


def bind_listener(host: str, port: int) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        # On Windows SO_REUSEADDR lets another process bind the same port and take
        # connections; exclusive use is the safe equivalent.
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    # A short backlog makes the kernel reset connections that arrive in a burst (a
    # port scan on the WiFi resets a real caller too); handshakes are bounded by
    # MAX_HANDSHAKES, not by this queue.
    srv.listen(socket.SOMAXCONN)
    return srv


class Listener:
    def __init__(self, ident, port: int, *, trusted, on_call=None, on_pair=None,
                 window=None, host: str = "0.0.0.0", handshake_timeout=HANDSHAKE_TIMEOUT):
        self._ident, self._trusted = ident, trusted       # trusted(): list of friend PEMs
        self._on_call, self._on_pair, self.window = on_call, on_pair, window
        self._timeout = handshake_timeout
        self._srv = bind_listener(host, port)
        self.port = self._srv.getsockname()[1]
        self._slots = threading.BoundedSemaphore(MAX_HANDSHAKES)
        self._in_call = threading.Lock()
        self.stop_event = threading.Event()
        self.rejected = 0
        self._thread = None

    def start(self) -> "Listener":
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(2)
        self._srv.close()

    def _accept_loop(self) -> None:
        self._srv.settimeout(ACCEPT_TICK)
        while not self.stop_event.is_set():
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                self.rejected += 1
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, addr), daemon=True).start()

    def _serve(self, conn, addr) -> None:
        channel = None
        try:
            conn.settimeout(None)
            window = self.window
            accept_pairing = (self._on_pair is not None and window is not None
                              and window.is_open())
            try:
                channel = SecureChannel.establish(conn, "server", self._ident, self._trusted(),
                                                  self._timeout, accept_pairing=accept_pairing)
            except (HandshakeError, ValueError, OSError) as e:
                self.rejected += 1
                log.info("listener dropped a connection",
                         extra={"peer": addr[0], "reason": getattr(e, "reason", type(e).__name__)})
                return
        finally:
            self._slots.release()             # the slot only covers the handshake
        try:
            if channel.mode == "pair":
                if not window.try_begin():    # busy, closed, or out of attempts
                    self.rejected += 1
                    return
                failure = None
                try:
                    self._on_pair(channel, addr)
                except Exception as e:        # PairingFailed or a handler bug: window closes on mismatch
                    failure = e
                finally:
                    window.finish(failure if hasattr(failure, "mismatch") else None)
            elif self._on_call is not None and self._in_call.acquire(blocking=False):
                try:
                    self._on_call(channel, addr)
                finally:
                    self._in_call.release()
            else:
                self.rejected += 1            # already in a call, or calls not served here
        finally:
            channel.close()
