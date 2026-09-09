import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hpqv.eval import graphs
from hpqv.eval.runner import run_scenario

SCENARIOS = ("hybrid", "naive", "tcp")
LOSSES = (0, 5, 10, 20, 30)
DELAY_MS = 50
JITTER_MS = 10


def _median_run(name, loss_pct, n_frames, n_runs):
    runs = [run_scenario(name, loss_pct, DELAY_MS, JITTER_MS, n_frames=n_frames)
            for _ in range(n_runs)]
    keys = runs[0].keys()
    return {k: (runs[0][k] if isinstance(runs[0][k], str)
                 else statistics.median(r[k] for r in runs))
            for k in keys}


def main(quick: bool = False, losses=None):
    losses = LOSSES if losses is None else losses
    n_runs = 1 if quick else 3
    n_frames = 50 if quick else 100

    os.makedirs("results", exist_ok=True)

    rows = [_median_run(name, loss_pct, n_frames, n_runs)
            for loss_pct in losses for name in SCENARIOS]

    with open("results/eval.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    graphs.plot_loss_vs_mos(rows, "results/loss_vs_mos.png")
    graphs.plot_loss_vs_fragments(rows, "results/loss_vs_fragments.png")
    graphs.plot_latency_vs_ttfb(rows, "results/latency_vs_ttfb.png")


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
