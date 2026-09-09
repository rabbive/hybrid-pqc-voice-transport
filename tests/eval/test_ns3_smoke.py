import csv
import io
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("g++") is None or not os.path.exists("/usr/include/ns3"),
    reason="needs ns-3 (container)",
)

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = os.path.join(REPO_ROOT, "ns3", "hpqv_sim.cc")
BIN = "/tmp/hpqv_sim"

CSV_HEADER = [
    "scheme",
    "loss",
    "delayMs",
    "ttfb_ms",
    "voice_delay_ms",
    "voice_jitter_ms",
    "voice_loss_pct",
]


def _build():
    if os.path.exists(BIN):
        return
    subprocess.run(
        [
            "bash",
            os.path.join(REPO_ROOT, "ns3", "build.sh"),
            SRC,
            BIN,
            "network",
            "internet",
            "point-to-point",
            "applications",
            "flow-monitor",
            "internet-apps",
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def _run(**kwargs):
    _build()
    args = [BIN] + [f"--{k}={v}" for k, v in kwargs.items()]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    line = out.strip().splitlines()[-1]
    row = dict(zip(CSV_HEADER, csv.reader(io.StringIO(line)).__next__()))
    return row


def test_hybrid_no_loss():
    row = _run(scheme="hybrid", loss=0, delayMs=50, hsBytes=10842, hsMs=1, dur=2)
    assert float(row["ttfb_ms"]) > 0
    assert float(row["voice_loss_pct"]) == 0


def test_hybrid_with_loss():
    row = _run(scheme="hybrid", loss=0.2, delayMs=50, hsBytes=10842, hsMs=1, dur=2)
    assert float(row["voice_loss_pct"]) > 0
