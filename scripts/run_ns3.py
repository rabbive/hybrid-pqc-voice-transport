import csv
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hpqv.eval import graphs, mos, pqc_cost

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SIM_SRC = os.path.join(REPO_ROOT, "ns3", "hpqv_sim.cc")
SIM_BIN = "/tmp/hpqv_sim"

SCHEMES = ("hybrid", "tcp", "quic")
LOSSES = (0, 5, 10, 20, 30)
DELAYS = (20, 50, 100, 200)
DELAY_MS = 50

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
    if os.path.exists(SIM_BIN):
        return
    subprocess.run(
        [
            "bash",
            os.path.join(REPO_ROOT, "ns3", "build.sh"),
            SIM_SRC,
            SIM_BIN,
            "network",
            "internet",
            "point-to-point",
            "applications",
            "flow-monitor",
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def _run(scheme, loss_pct, delay_ms, hs_bytes, hs_ms, dur):
    _build()
    args = [
        SIM_BIN,
        f"--scheme={scheme}",
        f"--loss={loss_pct / 100.0}",
        f"--delayMs={delay_ms}",
        f"--hsBytes={hs_bytes}",
        f"--hsMs={hs_ms}",
        f"--dur={dur}",
    ]
    out = subprocess.run(args, check=True, capture_output=True, text=True).stdout
    line = out.strip().splitlines()[-1]
    values = line.split(",")
    row = dict(zip(CSV_HEADER, values))

    # NS-3's link model is constant-delay point-to-point, so jitter is ~0 by
    # construction; MOS variation here is driven by loss and delay, not jitter.
    return {
        "name": scheme,
        "loss_pct": loss_pct,
        "delay_ms": delay_ms,
        "ttfb_ms": float(row["ttfb_ms"]),
        "voice_delay_ms": float(row["voice_delay_ms"]),
        "voice_jitter_ms": float(row["voice_jitter_ms"]),
        "voice_loss_pct": float(row["voice_loss_pct"]),
        "mos": mos.mos_from_network(
            float(row["voice_delay_ms"]),
            float(row["voice_jitter_ms"]),
            float(row["voice_loss_pct"]),
        ),
    }


def main(quick: bool = False, losses=None, delays=None):
    losses = (LOSSES if not quick else (0, 20)) if losses is None else losses
    delays = (DELAYS if not quick else (20, 100)) if delays is None else delays
    dur = 2 if quick else 10

    hs_bytes = pqc_cost.handshake_bytes("hybrid")
    hs_ms = pqc_cost.handshake_ms()

    os.makedirs("results", exist_ok=True)

    rows = [
        _run(scheme, loss_pct, DELAY_MS, hs_bytes, hs_ms, dur)
        for loss_pct in losses
        for scheme in SCHEMES
    ]
    with open("results/ns3_eval.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    latency_rows = [
        _run(scheme, 0, delay_ms, hs_bytes, hs_ms, dur)
        for delay_ms in delays
        for scheme in SCHEMES
    ]
    with open("results/ns3_latency.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(latency_rows[0].keys()))
        writer.writeheader()
        writer.writerows(latency_rows)

    graphs.plot_loss_vs_mos(rows, "results/ns3_loss_vs_mos.png")
    graphs.plot_latency_vs_ttfb(latency_rows, "results/ns3_latency_vs_ttfb.png")


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
