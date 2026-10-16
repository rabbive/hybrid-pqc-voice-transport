import socket
import struct
import threading
from pathlib import Path

import pytest

from hpqv.eval import v1_handshake as v1

NEEDLE = "o" + "qs"   # the removed C library; spelled out so this file stays clean
SRC = Path(__file__).resolve().parents[2] / "src" / "hpqv"


@pytest.fixture(scope="module")
def ids():
    return v1.make_identity(), v1.make_identity()


def test_identity_lengths(ids):
    pub, secret = ids[0]
    assert len(pub) == v1.SIG_PUB_LEN == 1952
    assert isinstance(secret, bytes)


def test_round_trip_and_exact_sizes(ids):
    (a_pub, a_sec), (b_pub, b_sec) = ids
    hello, kem, kem_pub = v1.build_hello(a_pub, a_sec)
    assert len(kem_pub) == 1184
    assert len(hello) == 6446
    accept, resp_session = v1.accept_hello(hello, a_pub, b_sec)
    assert len(accept) == 4398
    init_session = v1.finish(accept, kem, kem_pub, b_pub)
    key_i, prefix_i2r, prefix_r2i = init_session
    key_r, send_r, recv_r = resp_session
    assert key_i == key_r and len(key_i) == 32
    assert (prefix_i2r, prefix_r2i) == (recv_r, send_r)


def test_unpinned_identity_rejected(ids):
    (a_pub, a_sec), (b_pub, b_sec) = ids
    hello, _, _ = v1.build_hello(a_pub, a_sec)
    with pytest.raises(ValueError, match="unpinned"):
        v1.accept_hello(hello, b_pub, b_sec)


@pytest.mark.parametrize("offset", [1 + 5, 1 + 1184 + 5, 6446 - 1])
def test_tampered_hello_rejected(ids, offset):
    (a_pub, a_sec), (_b_pub, b_sec) = ids
    hello, _, _ = v1.build_hello(a_pub, a_sec)
    bad = bytearray(hello)
    bad[offset] ^= 1
    with pytest.raises(ValueError):
        v1.accept_hello(bytes(bad), a_pub, b_sec)


def test_tampered_accept_rejected(ids):
    (a_pub, a_sec), (b_pub, b_sec) = ids
    hello, kem, kem_pub = v1.build_hello(a_pub, a_sec)
    accept, _ = v1.accept_hello(hello, a_pub, b_sec)
    for offset in (3, 1 + 1088 + 3):
        bad = bytearray(accept)
        bad[offset] ^= 1
        with pytest.raises(ValueError):
            v1.finish(bytes(bad), kem, kem_pub, b_pub)


@pytest.mark.parametrize("size", [0, 1, 6445, 6447])
def test_wrong_length_hello_rejected(ids, size):
    (a_pub, _), (_b_pub, b_sec) = ids
    with pytest.raises(ValueError):
        v1.accept_hello(b"\x01" * size, a_pub, b_sec)


def test_wrong_length_accept_rejected(ids):
    (a_pub, a_sec), (b_pub, _) = ids
    _hello, kem, kem_pub = v1.build_hello(a_pub, a_sec)
    with pytest.raises(ValueError):
        v1.finish(b"\x01" * 100, kem, kem_pub, b_pub)


def test_record_length_cap():
    a, b = socket.socketpair()
    with a, b:
        a.sendall(struct.pack(">I", v1.MAX_RECORD + 1))
        with pytest.raises(ValueError):
            v1.recv_record(b)
        with pytest.raises(ValueError):
            v1.send_record(a, b"x" * (v1.MAX_RECORD + 1))


def test_record_round_trip_and_truncation():
    a, b = socket.socketpair()
    with a, b:
        v1.send_record(a, b"hello")
        assert v1.recv_record(b) == b"hello"
        a.sendall(struct.pack(">I", 10) + b"abc")
        a.shutdown(socket.SHUT_WR)
        with pytest.raises(ConnectionError):
            v1.recv_record(b)


def test_run_initiator_responder_over_tcp(ids):
    (a_pub, a_sec), (b_pub, b_sec) = ids
    a, b = socket.socketpair()
    box = {}
    with a, b:
        t = threading.Thread(target=lambda: box.update(r=v1.run_responder(b, b_pub, b_sec, a_pub)))
        t.start()
        init = v1.run_initiator(a, a_pub, a_sec, b_pub)
        t.join(5)
    assert init[0] == box["r"][0]


def test_nothing_outside_eval_imports_v1_handshake():
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
                 if "eval" not in p.relative_to(SRC).parts[:1]
                 and "v1_handshake" in p.read_text()]
    assert offenders == []


def test_removed_c_library_absent_from_eval_packaging_and_ci():
    """Scope: the paths T5 owns. src/hpqv/{demo,handshake}.py are T4's / the integrator's."""
    root = SRC.parents[1]
    paths = [p for d in ("src/hpqv/eval", "packaging", "scripts", ".github", "eval")
             for p in (root / d).rglob("*") if p.is_file()]
    paths += [root / "pyproject.toml", root / "uv.lock"]
    hits = [str(p.relative_to(root)) for p in paths
            if NEEDLE in p.read_text(errors="ignore").lower()
            and p != Path(__file__)]
    assert hits == []


def test_historical_label_present():
    doc = v1.__doc__
    assert "HISTORICAL v1 protocol, insecure" in doc and "never import from production code" in doc
