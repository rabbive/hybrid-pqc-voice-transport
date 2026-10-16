"""B2 LAN discovery: bounded, untrusted, survives blocked broadcast."""
import json
import socket
import time

from hpqv import discovery
from hpqv.discovery import Discovery, decode, encode

FP_A = "a1b2 " * 15 + "c3d4"
FP_B = "0f0f " * 15 + "9999"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_roundtrip_and_no_key_material():
    data = encode("alice", 9000, FP_A, True)
    seen = decode(data, "10.0.0.2", 1.0)
    assert (seen.name, seen.tcp_port, seen.fp_hint, seen.pairing) == ("alice", 9000,
                                                                      "a1b2a1b2a1b2a1b2", True)
    assert len(data) <= discovery.MAX_ANNOUNCE and b"PRIVATE" not in data and b"CERT" not in data


def test_decode_rejects_bad_input():
    good = json.loads(encode("alice", 9000, FP_A, False))
    bad = [b"x" * 600, b"{not json", json.dumps({**good, "app": "other"}).encode(),
           json.dumps({**good, "v": 1}).encode(), json.dumps({**good, "port": 0}).encode(),
           json.dumps({**good, "port": "9000"}).encode(), json.dumps({**good, "fp": "XYZ"}).encode(),
           json.dumps({**good, "name": "x" * 40}).encode(),
           json.dumps({k: v for k, v in good.items() if k != "pairing"}).encode()]
    assert all(decode(b, "1.2.3.4", 0) is None for b in bad)


def test_expiry_cap_and_own_announcement_ignored():
    clock = Clock()
    d = Discovery("me", 1, FP_A, clock=clock)
    d.observe(encode("me", 1, FP_A, False), "10.0.0.1")
    assert d.peers() == []
    for i in range(200):
        d.observe(encode(f"n{i}", 1000 + i, f"{i:04x} " * 15 + "0000", False), f"10.0.1.{i % 250}")
    assert len(d.peers()) == discovery.MAX_PEERS
    clock.t = discovery.EXPIRE_AFTER + 1
    assert d.peers() == []


def test_same_name_two_keys_are_two_entries():
    d = Discovery("me", 1, FP_A)
    d.observe(encode("bob", 9000, FP_B, False), "10.0.0.3")
    d.observe(encode("bob", 9000, "1234 " * 15 + "5678", False), "10.0.0.4")
    assert sorted(s.fp_hint for s in d.peers()) == ["0f0f0f0f0f0f0f0f", "1234123412341234"]


def _free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_two_instances_hear_each_other_on_loopback():
    pa, pb = _free_udp_port(), _free_udp_port()
    a = Discovery("alice", 9000, FP_A, port=pa, send_to=("127.0.0.1", pb))
    b = Discovery("bob", 9001, FP_B, port=pb, send_to=("127.0.0.1", pa))
    a.start(), b.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (a.peers() and b.peers()):
            time.sleep(0.1)
        assert [s.name for s in a.peers()] == ["bob"] and [s.name for s in b.peers()] == ["alice"]
    finally:
        a.stop(), b.stop()


def test_blocked_broadcast_degrades_to_an_empty_list(monkeypatch):
    d = Discovery("me", 1, FP_A, port=_free_udp_port(), send_to=("127.0.0.1", 9))
    d.start()
    try:
        real = d._sock

        class Blocked:
            def __getattr__(self, name):
                return getattr(real, name)

            def sendto(self, *a):
                raise OSError(65, "No route to host")
        d._sock = Blocked()
        time.sleep(0.6)
        assert d.peers() == [] and d._thread.is_alive()
    finally:
        d._sock = real
        d.stop()
