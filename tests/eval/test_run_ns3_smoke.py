import csv
import os
import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None or not os.path.exists("/usr/include/ns3"),
    reason="needs ns-3 (container)",
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from run_ns3 import main


def test_smoke(tmp_path, monkeypatch):
# Runs into a tmp cwd: main() writes results/ relative to the working
# directory, so without this the smoke run would overwrite the real
# evaluation results (which are committed) with 2-point quick data.
    monkeypatch.chdir(tmp_path)
    main(quick=True, losses=[0, 20])

    with open("results/ns3_eval.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) > 0
    assert {r["name"] for r in rows} == {"hybrid", "tcp", "quic"}

    by_key = {(r["name"], r["loss_pct"]): r for r in rows}
    for scheme in ("hybrid", "tcp", "quic"):
        assert float(by_key[(scheme, "0")]["voice_loss_pct"]) == pytest.approx(0, abs=1e-6)

    hybrid_loss_20 = float(by_key[("hybrid", "20")]["voice_loss_pct"])
    quic_loss_20 = float(by_key[("quic", "20")]["voice_loss_pct"])
    tcp_loss_20 = float(by_key[("tcp", "20")]["voice_loss_pct"])
    assert hybrid_loss_20 > 0
    assert quic_loss_20 > 0
    # TCP is reliable: it must show near-zero loss at the app layer, well
    # below UDP's raw datagram loss, or this reverts to the bug where IP-layer
    # tx/rx counted TCP retransmissions as permanent loss.
    assert tcp_loss_20 < hybrid_loss_20 - 5

    with open("results/ns3_latency.csv") as f:
        latency_rows = list(csv.DictReader(f))
    assert len(latency_rows) > 0
    assert len({r["delay_ms"] for r in latency_rows}) > 1

    for png in ("ns3_loss_vs_mos.png", "ns3_latency_vs_ttfb.png"):
        path = os.path.join("results", png)
        assert os.path.getsize(path) > 0
