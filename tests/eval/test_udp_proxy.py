import socket
import threading
import time

import pytest

from hpqv.eval.profiles import PROFILES, Profile
from hpqv.eval.udp_proxy import ImpairmentProxy

LO = "127.0.0.1"


def _sock():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    s.bind((LO, 0))
    return s


class Rig:
    def __init__(self, profile, seed=0, **kw):
        self.a, self.b = _sock(), _sock()
        self.proxy = ImpairmentProxy(self.a.getsockname(), self.b.getsockname(), profile, seed, **kw)
        self.to_proxy_a, self.to_proxy_b = self.proxy.start()

    def wait_received(self, n, timeout=10.0):
        end = time.monotonic() + timeout
        while self.proxy.received < n and time.monotonic() < end:
            time.sleep(0.001)
        assert self.proxy.received >= n

    def pump(self, n, size=20, batch=100):
        """Send n datagrams A->B in batches so the kernel buffer never drops any."""
        base = self.proxy.received
        for i in range(n):
            self.a.sendto(i.to_bytes(4, "big").ljust(size, b"x"), self.to_proxy_a)
            if (i + 1) % batch == 0:
                self.wait_received(base + i + 1)
        self.wait_received(base + n)

    def drain_b(self, quiet=0.15):
        self.b.settimeout(quiet)
        got = []
        try:
            while True:
                got.append(self.b.recvfrom(2048)[0])
        except socket.timeout:
            return got

    def close(self):
        self.proxy.stop()
        self.a.close()
        self.b.close()


@pytest.fixture
def rig():
    made = []

    def make(profile, seed=0, **kw):
        r = Rig(profile, seed, **kw)
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()
    assert not [t for t in threading.enumerate() if t.name.startswith("hpqv-proxy")]


def _trace_for(rig, profile, seed, n):
    r = rig(profile, seed)
    r.pump(n)
    return r.proxy.trace


NOISY = Profile("noisy", loss_pct=20, loss_model="burst", dup_pct=10, reorder_pct=10)


def test_same_seed_same_trace(rig):
    assert _trace_for(rig, NOISY, 7, 500) == _trace_for(rig, NOISY, 7, 500)


def test_different_seed_different_trace(rig):
    assert _trace_for(rig, NOISY, 7, 500) != _trace_for(rig, NOISY, 8, 500)


def test_loss_rate_within_tolerance(rig):
    trace = _trace_for(rig, Profile("l10", loss_pct=10), 1, 10_000)
    assert abs(sum(a == "drop" for _, a in trace) / 10_000 - 0.10) < 0.01


def test_duplication_and_reordering_rates(rig):
    r = rig(Profile("dr", dup_pct=10, reorder_pct=5), 3)
    r.pump(5_000)
    trace = r.proxy.trace
    assert abs(sum(a == "dup" for _, a in trace) / 5_000 - 0.10) < 0.02
    assert abs(sum(a == "reorder" for _, a in trace) / 5_000 - 0.05) < 0.02
    got = r.drain_b()
    ids = [int.from_bytes(d[:4], "big") for d in got]
    assert len(ids) > 5_000  # duplicates arrived
    assert any(later < earlier for earlier, later in zip(ids, ids[1:]) if later != earlier)


def test_bandwidth_cap_bounds_throughput(rig):
    r = rig(Profile("bw", bandwidth_kbps=64), 0)  # 8000 B/s
    t0 = time.monotonic()
    for i in range(100):
        r.a.sendto(bytes(1000), r.to_proxy_a)
    got = 0
    while time.monotonic() - t0 < 0.8:
        r.b.settimeout(max(0.01, 0.8 - (time.monotonic() - t0)))
        try:
            r.b.recvfrom(2048)
        except socket.timeout:
            break
        got += 1000
    elapsed = time.monotonic() - t0
    assert 2000 <= got <= 1200 + 8000 * elapsed + 1000


def test_outage_drops_everything_in_window(rig):
    now = [0.0]
    r = rig(Profile("o", outages=((1.0, 2.0),)), 0, clock=lambda: now[0])
    sent = 0
    for t in (0.5, 1.0, 1.5, 2.99, 3.0, 3.5):
        now[0] = t
        r.a.sendto(b"x" * 20, r.to_proxy_a)
        sent += 1
        r.wait_received(sent)
    actions = [a for _, a in r.proxy.trace]
    assert actions == ["fwd", "drop", "drop", "drop", "fwd", "fwd"]
    assert len(r.drain_b()) == 3


def test_proxy_binds_loopback_only(rig):
    r = rig(PROFILES["clean"])
    assert r.to_proxy_a[0] == LO and r.to_proxy_b[0] == LO
    assert r.proxy._sock_a.getsockname()[0] == LO and r.proxy._sock_b.getsockname()[0] == LO
    with pytest.raises(ValueError):
        ImpairmentProxy(("0.0.0.0", 9), r.b.getsockname(), PROFILES["clean"], 0)
    with pytest.raises(ValueError):
        ImpairmentProxy(r.a.getsockname(), ("192.0.2.1", 9), PROFILES["clean"], 0)


def test_queues_bounded_under_flood(rig):
    r = rig(Profile("slow", delay_ms=1000), 0)
    for i in range(1_000):
        r.a.sendto(i.to_bytes(4, "big"), r.to_proxy_a)
    r.wait_received(1_000)
    assert r.proxy.peak_inflight <= 256
    assert any(a == "drop" for _, a in r.proxy.trace)


def test_reverse_direction_and_clean_passthrough(rig):
    r = rig(PROFILES["clean"])
    r.a.sendto(b"ping", r.to_proxy_a)
    assert r.b.recvfrom(64)[0] == b"ping"
    r.b.sendto(b"pong", r.to_proxy_b)
    r.a.settimeout(2)
    assert r.a.recvfrom(64)[0] == b"pong"


def test_stop_is_idempotent_and_joins_with_delayed_traffic(rig):
    r = rig(Profile("d", delay_ms=500), 0)
    r.a.sendto(b"x", r.to_proxy_a)
    r.wait_received(1)
    r.proxy.stop()
    r.proxy.stop()


def test_profiles_registry():
    assert set(PROFILES) == {"clean", "loss1", "loss5", "loss10", "loss20", "burst10",
                             "high_latency_jitter", "outage"}
    assert PROFILES["burst10"].loss_model == "burst" and PROFILES["burst10"].burst_len == 3
    assert (PROFILES["high_latency_jitter"].delay_ms, PROFILES["high_latency_jitter"].jitter_ms) == (150, 40)
    assert PROFILES["outage"].outages == ((3.0, 2.0),)
    with pytest.raises(ValueError):
        Profile("bad", loss_pct=101)
