"""Seeded, loopback-only UDP impairment proxy between two endpoints.

Endpoint A sends to the first returned address, B to the second; each side
sees the proxy as its peer. Every impairment decision is drawn from per-direction
RNGs seeded from `seed`, always in the same order for each datagram, so the same
seed gives the same trace for the same input. Wall-clock effects (outage
windows, bandwidth shaping, queue overflow) only decide whether a datagram is
dropped or delayed; they never shift the seeded draws of later datagrams.
"""
import random
import socket
import threading
import time

from hpqv.impair import DelayLine, DupModel, LossModel, TokenBucket

LOOPBACK = "127.0.0.1"
QUEUE_MAX = 256          # datagrams admitted per direction but not yet delivered
TRACE_MAX = 200_000      # trace entries kept per direction
_RECV_MAX = 2048
_POLL_S = 0.05           # recv timeout: stop polling and release a held reorder
_BURST_BYTES = 1200      # token-bucket burst: one maximum datagram


def _check_addr(addr):
    host, port = addr
    if host != LOOPBACK or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError(f"proxy endpoints must be ({LOOPBACK!r}, port), got {addr!r}")
    return host, port


class _Lane:
    """One direction: recv on `rx`, impair, deliver via `tx` to `dest`."""

    def __init__(self, name, rx, tx, dest, profile, seed, clock):
        self.name, self.rx, self.tx, self.dest = name, rx, tx, dest
        self.profile = profile
        self.loss = LossModel(f"{seed}/{name}/loss")
        self.dup = DupModel(f"{seed}/{name}/dup")
        self.reorder_rng = random.Random(f"{seed}/{name}/reorder")
        self.line = DelayLine(self._deliver, f"{seed}/{name}/delay")
        self.bucket = (
            TokenBucket(profile.bandwidth_kbps * 1000 / 8, _BURST_BYTES, clock)
            if profile.bandwidth_kbps > 0 else None
        )
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak_inflight = 0
        self.index = 0
        self.entries = []
        self.held = []
        self.thread = None

    def _deliver(self, data):
        try:
            self.tx.sendto(data, self.dest)
        except OSError:
            pass  # peer or proxy socket gone; UDP loses it like the network would
        finally:
            with self.lock:
                self.inflight -= 1

    def record(self, action):
        if len(self.entries) < TRACE_MAX:
            self.entries.append((self.index, action))

    def emit(self, data):
        wait_ms = self.bucket.reserve(len(data)) * 1000 if self.bucket else 0
        self.line.send(data, self.profile.delay_ms + wait_ms, self.profile.jitter_ms)

    def flush_held(self):
        held, self.held = self.held, []
        for data in held:
            self.emit(data)

    def process(self, data, outage):
        p = self.profile
        drop = self.loss.drop(p.loss_pct, p.loss_model, p.burst_len)
        dup = self.dup.duplicate(p.dup_pct)
        reorder = self.reorder_rng.random() < p.reorder_pct / 100
        copies = [data, data] if dup else [data]
        with self.lock:
            full = self.inflight + len(copies) > QUEUE_MAX
            if not (drop or outage or full):
                self.inflight += len(copies)
                self.peak_inflight = max(self.peak_inflight, self.inflight)
        if drop or outage or full:
            self.record("drop")
        else:
            hold = reorder and not self.held
            actions = ["dup"] * dup + ["reorder"] * hold
            actions += ["late"] * bool(p.delay_ms or p.jitter_ms)
            for action in actions or ["fwd"]:
                self.record(action)
            if hold:
                self.held = copies
            else:
                for copy in copies:
                    self.emit(copy)
                self.flush_held()
        self.index += 1


class ImpairmentProxy:
    def __init__(self, a_addr, b_addr, profile, seed, *, clock=time.monotonic):
        self.a_addr, self.b_addr = _check_addr(a_addr), _check_addr(b_addr)
        self.profile, self.seed, self._clock = profile, seed, clock
        self.received = 0
        self._sock_a = self._sock_b = None
        self._lanes = []
        self._stop = threading.Event()
        self._count_lock = threading.Lock()
        self._t0 = None
        self._stopped = False

    def start(self):
        if self._sock_a is not None:
            raise RuntimeError("proxy already started")
        self._sock_a, self._sock_b = (self._bind() for _ in range(2))
        self._t0 = self._clock()
        self._lanes = [
            _Lane("a2b", self._sock_a, self._sock_b, self.b_addr, self.profile, self.seed, self._clock),
            _Lane("b2a", self._sock_b, self._sock_a, self.a_addr, self.profile, self.seed, self._clock),
        ]
        for lane in self._lanes:
            lane.thread = threading.Thread(
                target=self._run, args=(lane,), name=f"hpqv-proxy-{lane.name}", daemon=True)
            lane.thread.start()
        return self._sock_a.getsockname(), self._sock_b.getsockname()

    @staticmethod
    def _bind():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        sock.bind((LOOPBACK, 0))
        sock.settimeout(_POLL_S)
        return sock

    def _in_outage(self):
        elapsed = self._clock() - self._t0
        return any(start <= elapsed < start + dur for start, dur in self.profile.outages)

    def _run(self, lane):
        while not self._stop.is_set():
            try:
                data, _src = lane.rx.recvfrom(_RECV_MAX)
            except socket.timeout:
                lane.flush_held()
                continue
            except OSError:
                return
            lane.process(data, self._in_outage())
            with self._count_lock:
                self.received += 1

    def stop(self):
        if self._stopped or self._sock_a is None:
            return
        self._stopped = True
        self._stop.set()
        for lane in self._lanes:
            lane.thread.join()
        for lane in self._lanes:
            lane.line.close()
        self._sock_a.close()
        self._sock_b.close()

    @property
    def trace(self):
        """(datagram index, action) merged over both directions, ordered by index then direction."""
        merged = [(i, n, a) for n, lane in enumerate(self._lanes) for i, a in list(lane.entries)]
        merged.sort(key=lambda e: e[:2])
        return [(i, a) for i, _n, a in merged]

    @property
    def peak_inflight(self):
        return max((lane.peak_inflight for lane in self._lanes), default=0)
