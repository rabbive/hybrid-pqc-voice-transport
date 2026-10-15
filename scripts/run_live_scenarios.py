"""Run reportable scenarios through hpqv.demo's live loopback call path."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path

from hpqv.eval.live_scenarios import RESULTS_DIR, make_speech_wav, run_scenario

ROOT = Path("results")
FIELDS = [
    "name", "knobs", "frames_offered", "frames_delivered", "frames_lost",
    "frames_late", "frames_recovered", "frames_concealed", "measured_loss_pct",
    "residual_loss_pct", "mean_mos", "whole_signal_snr_db",
    "repaired_frame_snr_db", "alignment_offset_frames", "received_wav",
]


def scenarios():
    rows = [("clean", {}, "clean")]
    for loss in (10, 20):
        for repair in ("silence", "plc", "fec"):
            rows.append((f"random-{loss}-{repair}",
                         {"loss_pct": loss, "loss_model": "random", "repair": repair,
                          "fec": repair == "fec"}, repair))
    for repair in ("plc", "fec"):
        rows.append((f"burst-10-{repair}",
                     {"loss_pct": 10, "loss_model": "burst", "burst_len": 3,
                      "repair": repair, "fec": repair == "fec"}, repair))
    for depth in (2, 6):
        rows.append((f"delay-100-jitter30-depth{depth}",
                     {"delay_ms": 100, "jitter_ms": 30, "jitter_depth": depth,
                      "repair": "fec", "fec": True}, "fec"))
    return rows


def _charts(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ROOT.mkdir(parents=True, exist_ok=True)
    names = [r["name"] for r in rows]
    x = list(range(len(rows)))
    colors = {"clean": "#555555", "silence": "#d95f02", "plc": "#1b9e77", "fec": "#7570b3"}
    fig, ax = plt.subplots(figsize=(12, 5))
    width = 0.22
    modes = ("clean", "silence", "plc", "fec")
    for i, mode in enumerate(modes):
        values = [r["residual_loss_pct"] if r["repair"] == mode else float("nan") for r in rows]
        ax.bar([v + (i - 1.5) * width for v in x], values, width,
               color=colors[mode], label=mode)
    ax.set_xticks(x, names, rotation=35, ha="right")
    ax.set_ylabel("Residual loss (%)")
    ax.set_title("Live pipeline residual loss by scenario and repair")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.savefig(ROOT / "live_scenarios_residual_loss.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    for metric, label, file, title in (
        ("whole_signal_snr_db", "Whole-signal SNR vs clean decode (dB)",
         "live_scenarios_snr.png", "Received audio quality vs clean loopback"),
        ("mean_mos", "MOS (E-model estimate)",
         "live_scenarios_mos.png", "Estimated MOS by live scenario"),
    ):
        fig, ax = plt.subplots(figsize=(12, 5))
        values = [r[metric] for r in rows]
        finite = [value for value in values if math.isfinite(value)]
        infinity_height = max(finite, default=0) + 5
        plotted = [value if math.isfinite(value) else infinity_height for value in values]
        ax.bar(x, plotted,
               color=[colors[r["repair"]] for r in rows])
        for index, value in enumerate(values):
            if math.isinf(value):
                ax.annotate("∞", (index, infinity_height), ha="center", va="bottom")
        ax.set_xticks(x, names, rotation=35, ha="right")
        ax.set_ylabel(label)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
        fig.savefig(ROOT / file, dpi=150, bbox_inches="tight")
        plt.close(fig)

    late = [r for r in rows if r["name"].startswith("delay-")]
    fig, ax = plt.subplots()
    ax.bar([json.loads(r["knobs"])["jitter_depth"] for r in late],
           [r["frames_late"] for r in late], color=colors["fec"])
    ax.set_xlabel("Jitter buffer depth (frames)")
    ax.set_ylabel("Late packets")
    ax.set_title("Late packets under 100 ms ± 30 ms delay")
    ax.grid(axis="y", alpha=0.3)
    fig.savefig(ROOT / "live_scenarios_late_by_depth.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def run(quick=False, wav_path=None):
    seconds, repeats = (4.0, 1) if quick else (10.0, 3)
    matrix = scenarios()
    total = len(matrix) * repeats
    estimate = total * (seconds + 3.0)
    print(f"Estimated runtime: about {estimate / 60:.1f} minutes "
          f"({total} loopback calls at {seconds:g}s each).", flush=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(wav_path) if wav_path else make_speech_wav(
        RESULTS_DIR / "speech_like.wav", seconds=seconds)
    medians = []
    numeric = [field for field in FIELDS
               if field not in ("name", "knobs", "received_wav")]
    for name, knobs, repair in matrix:
        trials = []
        for repetition in range(repeats):
            row = run_scenario(name, knobs, seconds, source, seed=20261015 + repetition)
            trials.append(row)
            print(f"{name} run {repetition + 1}/{repeats}: "
                  f"residual={row['residual_loss_pct']:.2f}% "
                  f"MOS={row['mean_mos']:.2f} SNR={row['whole_signal_snr_db']:.2f}dB",
                  flush=True)
        median = {"name": name, "knobs": json.dumps(knobs, sort_keys=True),
                  "repair": repair, "runs": repeats,
                  "received_wav": trials[-1]["received_wav"]}
        median.update({key: statistics.median(values) if (values := [r[key] for r in trials
                                                                      if r[key] is not None])
                       else None for key in numeric})
        medians.append(median)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "live_scenarios.csv").open("w", newline="", encoding="utf-8") as out:
        fields = FIELDS + ["repair", "runs"]
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(medians)
    _charts(medians)
    print(f"Wrote {ROOT / 'live_scenarios.csv'} and four charts.")
    return medians


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="one 4-second run per scenario")
    parser.add_argument("--wav", help="optional 48 kHz mono 16-bit PCM input")
    args = parser.parse_args()
    run(quick=args.quick, wav_path=args.wav)
