import shutil

import pytest

from hpqv import handshake
from hpqv.eval import full_handshake as fh
from hpqv.eval.netem import loopback_mtu, netem


@pytest.fixture(scope="module")
def ids():
    return handshake.make_identity(), handshake.make_identity()


@pytest.mark.parametrize("transport", ["tcp", "udp-naive", "udp-retry"])
def test_clean_loopback_handshake_succeeds_three_times(ids, transport):
    run = {
        "tcp": lambda: fh.run_tcp(ids, 3.0),
        "udp-naive": lambda: fh.run_udp(ids, 3.0, retry=False),
        "udp-retry": lambda: fh.run_udp(ids, 3.0, retry=True),
    }[transport]

    assert [run()[0] for _ in range(3)] == [True, True, True]


def test_duplicate_hello_reuses_cached_accept(ids, monkeypatch):
    hello, _, _ = handshake.build_hello(*ids[0])
    original = handshake.accept_hello
    calls = 0

    def count_accept(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(handshake, "accept_hello", count_accept)
    cache = {}
    first = fh._accept_for_hello(hello, ids[0][0], ids[1][1], cache)
    second = fh._accept_for_hello(hello, ids[0][0], ids[1][1], cache)

    assert first == second
    assert calls == 1


def test_measure_reports_handshake_sizes_and_common_deadline(monkeypatch):
    ids = handshake.make_identity(), handshake.make_identity()
    monkeypatch.setattr(handshake, "make_identity", lambda: ids[0])
    monkeypatch.setattr(fh, "run_tcp", lambda *_: (True, 12.0))

    row = fh.measure("tcp", attempts=1, deadline_s=2.5)

    assert set(row) == {"name", "attempts", "successes", "success_pct",
                        "median_ms", "deadline_s", "hello_bytes",
                        "accept_bytes", "fragments"}
    assert (row["name"], row["attempts"], row["success_pct"], row["median_ms"],
            row["deadline_s"], row["fragments"]) == ("tcp", 1, 100.0, 12.0, 2.5, 0)
    assert row["hello_bytes"] > 0 and row["accept_bytes"] > 0


@pytest.mark.parametrize("transport", ["tcp", "udp-naive", "udp-retry"])
def test_deadline_expiry_returns_failure(ids, transport):
    if transport == "tcp":
        ok, _ = fh.run_tcp(ids, 1e-4)
    else:
        ok, _ = fh.run_udp(ids, 1e-4, retry=transport == "udp-retry")

    assert not ok


@pytest.mark.skipif(shutil.which("tc") is None,
                    reason="needs Linux tc — run in the hpqv-eval container")
def test_tcp_survives_loss_better_than_naive_udp(ids):
    with loopback_mtu(), netem(loss_pct=20, dev="lo"):
        tcp = fh.measure("tcp", attempts=5, deadline_s=3.0)
        naive = fh.measure("udp-naive", attempts=5, deadline_s=3.0)

    assert tcp["success_pct"] > naive["success_pct"] + 30
