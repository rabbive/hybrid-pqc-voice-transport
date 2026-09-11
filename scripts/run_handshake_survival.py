"""Sweep: does the PQC handshake survive packet loss?

Counting fragments shows hybrid never fragments and naive always does. This
measures what that COSTS: how often each handshake actually completes.
"""
import csv
import os
import sys

from hpqv.eval import graphs, handshake_survival as hs
from hpqv.eval.netem import loopback_mtu, netem

LOSSES = [0, 5, 10, 20, 30]
TRANSPORTS = ["naive", "hybrid"]


def main(quick: bool = False, losses=None, attempts: int = 100):
    losses = LOSSES if losses is None else losses
    if quick:
        attempts = 5
    rows = []
    # Pin the loopback MTU once for the whole sweep: without it Docker's 65536
    # MTU means the oversized datagram never fragments and the result is a lie.
    with loopback_mtu():
        for loss in losses:
            for transport in TRANSPORTS:
                # naive is fire-and-forget: if it has not arrived in 1s on
                # loopback it never will. TCP must keep its long timeout —
                # its retransmit backoff legitimately takes ~1s at 30% loss.
                tmo = {"timeout": 1.0} if transport == "naive" else {}
                if loss == 0:
                    row = hs.measure(transport, attempts=attempts, **tmo)
                else:
                    with netem(loss_pct=loss, dev="lo"):
                        row = hs.measure(transport, attempts=attempts, **tmo)
                row["loss_pct"] = loss
                # Theoretical survival of an n-fragment datagram: (1-p)^n.
                n = row["fragments"]
                row["predicted_pct"] = 100.0 * ((1 - loss / 100.0) ** n) if n else 100.0
                rows.append(row)
                print(f"{transport:>6} loss={loss:>2}%  "
                      f"success={row['success_pct']:5.1f}%  "
                      f"(predicted {row['predicted_pct']:5.1f}%)  "
                      f"median={row['median_ms']:.1f}ms")

    os.makedirs("results", exist_ok=True)
    with open("results/handshake_survival.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    graphs.plot_loss_vs_success(rows, "results/handshake_survival.png")
    return rows


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
