import csv
import runpy
import shutil
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from hpqv.eval import full_handshake as fh, graphs
from hpqv.eval import v1_handshake as handshake
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


def test_udp_retry_reuses_accept_after_first_accept_is_lost(ids, monkeypatch):
    original = handshake.accept_hello
    calls, accepts, dropped = 0, [], False

    def count_accept(*args):
        nonlocal calls
        calls += 1
        accept_session = original(*args)
        accepts.append(accept_session[0])
        return accept_session

    original_send = fh._send_udp

    def drop_first_accept(sock, payload, addr, deadline):
        nonlocal dropped
        if accepts and payload == accepts[0] and not dropped:
            dropped = True
            return
        return original_send(sock, payload, addr, deadline)

    monkeypatch.setattr(handshake, "accept_hello", count_accept)
    monkeypatch.setattr(fh, "_send_udp", drop_first_accept)

    ok, _ = fh.run_udp(ids, deadline_s=3, retry=True)

    assert dropped
    assert ok
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
def test_deadline_expiry_returns_failure_and_joins_peer(ids, transport, monkeypatch):
    def delayed_peer(*_args):
        time.sleep(0.35)
        return (None, None, None)

    if transport == "tcp":
        monkeypatch.setattr(fh.v1, "run_responder", delayed_peer)
    else:
        monkeypatch.setattr(fh.v1, "accept_hello", delayed_peer)

    baseline = threading.active_count()
    run = (lambda: fh.run_tcp(ids, 0.3)) if transport == "tcp" else (
        lambda: fh.run_udp(ids, 0.3, retry=transport == "udp-retry"))
    ok, elapsed_ms = run()

    assert not ok
    assert elapsed_ms >= 250
    assert elapsed_ms < 600
    assert threading.active_count() == baseline


def test_udp_naive_clean_loopback_timing_excludes_responder_poll(ids):
    row = fh.measure("udp-naive", attempts=5, deadline_s=3)

    # cryptography's ML-DSA sign takes ~20 ms on this path; a 50 ms responder poll would exceed this.
    assert row["median_ms"] < 45


def test_cli_uses_attempts_and_predicts_only_naive_udp(monkeypatch, tmp_path, capsys):
    calls = []

    def measure(transport, attempts, deadline):
        calls.append((transport, attempts, deadline))
        return {"name": transport, "attempts": attempts, "successes": 0,
                "success_pct": 0.0, "median_ms": float("nan"),
                "deadline_s": deadline, "hello_bytes": 10, "accept_bytes": 10,
                "fragments": 2}

    monkeypatch.setattr(fh, "measure", measure)
    monkeypatch.setattr(graphs, "plot_loss_vs_success", lambda *_: None)
    monkeypatch.setattr("hpqv.eval.netem.loopback_mtu", nullcontext)
    monkeypatch.setattr("hpqv.eval.netem.netem", lambda **_: nullcontext())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_full_handshake.py", "--attempts", "2",
                                       "--deadline", "0.5"])

    runpy.run_path(str(Path(__file__).parents[2] / "scripts/run_full_handshake.py"),
                   run_name="__main__")

    assert calls == [(name, 2, 0.5) for _loss in (0, 5, 10, 20, 30)
                     for name in ("tcp", "udp-naive", "udp-retry")]
    assert capsys.readouterr().out.startswith("Estimated worst-case runtime: 15s\n")
    with (tmp_path / "results/full_handshake.csv").open(newline="") as result:
        rows = list(csv.DictReader(result))
    assert rows[1]["predicted_pct"] == "100.0"
    assert rows[3]["predicted_pct"] == ""
    assert rows[4]["predicted_pct"] == "90.25"


@pytest.mark.skipif(shutil.which("tc") is None,
                    reason="needs Linux tc — run in the hpqv-eval container")
def test_tcp_survives_loss_better_than_naive_udp(ids):
    with loopback_mtu(), netem(loss_pct=20, dev="lo"):
        tcp = fh.measure("tcp", attempts=30, deadline_s=3.0)
        naive = fh.measure("udp-naive", attempts=30, deadline_s=3.0)

    assert tcp["success_pct"] > naive["success_pct"] + 30
