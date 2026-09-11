"""Sweep: does Opus in-band FEC actually recover lost audio frames?

Enabling the CTL proves nothing on its own. This measures the consequence:
segmental SNR of the reconstructed signal, FEC-on vs FEC-off, across loss
levels.
"""
import csv
import os
import sys

from hpqv.eval import fec_recovery as fr

LOSSES = [0, 5, 10, 20, 30]


def main(quick: bool = False):
    seconds = 1.0 if quick else 3.0
    rows = []
    for loss in LOSSES:
        row = fr.measure(loss, seconds=seconds)
        rows.append(row)
        print(f"loss={loss:>2}%  fec_on={row['snr_fec_on_db']:6.2f}dB  "
              f"fec_off={row['snr_fec_off_db']:6.2f}dB  "
              f"recovered={row['frames_recovered_by_fec']}/{row['frames_lost']}")

    os.makedirs("results", exist_ok=True)
    with open("results/fec_recovery.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([r["loss_pct"] for r in rows], [r["snr_fec_on_db"] for r in rows],
            label="FEC on", linestyle="-", marker="o")
    ax.plot([r["loss_pct"] for r in rows], [r["snr_fec_off_db"] for r in rows],
            label="FEC off", linestyle="--", marker="s")
    ax.set_xlabel("Packet loss (%)")
    ax.set_ylabel("Segmental SNR (dB)")
    ax.set_title("FEC recovery quality vs packet loss")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig("results/fec_recovery.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return rows


if __name__ == "__main__":
    main(quick="--quick" in sys.argv)
