"""Sweep authenticated handshake completion under packet loss."""
import argparse
import csv
import os

from hpqv.eval import full_handshake as fh, graphs
from hpqv.eval.netem import loopback_mtu, netem

LOSSES = [0, 5, 10, 20, 30]
TRANSPORTS = ["tcp", "udp-naive", "udp-retry"]


def main(quick=False, deadline=3.0, losses=None, attempts=100):
    losses = LOSSES if losses is None else losses
    if quick:
        attempts = 5
    rows = []
    with loopback_mtu():
        for loss in losses:
            for transport in TRANSPORTS:
                if loss:
                    with netem(loss_pct=loss, dev="lo"):
                        row = fh.measure(transport, attempts, deadline)
                else:
                    row = fh.measure(transport, attempts, deadline)
                row["loss_pct"] = loss
                n = row["fragments"]
                row["predicted_pct"] = (
                    100 * (1 - loss / 100) ** n
                    if transport == "udp-naive" else 100.0)
                rows.append(row)
                print(f"{transport:>9} loss={loss:>2}%  "
                      f"success={row['success_pct']:5.1f}%  "
                      f"median={row['median_ms']:.1f}ms")

    os.makedirs("results", exist_ok=True)
    with open("results/full_handshake.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    graphs.plot_loss_vs_success(rows, "results/full_handshake.png")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--deadline", type=float, default=3.0)
    args = parser.parse_args()
    main(quick=args.quick, deadline=args.deadline)
