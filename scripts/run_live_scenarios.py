"""Run reportable scenarios through hpqv.demo's live loopback call path."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path

from hpqv.eval.live_scenarios import (RESULTS_DIR, lbrr_coverage, make_speech_wav,
                                      run_scenario)

ROOT = Path("results")
FIELDS = [
    "name", "knobs", "frames_offered", "frames_expected", "frames_delivered",
    "frames_lost", "frames_late", "frames_recovered", "frames_concealed",
    "measured_loss_pct", "residual_loss_pct", "rx_jitter_ms", "mean_added_delay_ms",
    "mean_mos", "whole_signal_snr_db",
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
    ax.set_ylabel("Residual loss (%, pooled over runs)")
    ax.set_title("Live pipeline residual loss by scenario and repair")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.savefig(ROOT / "live_scenarios_residual_loss.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    for metric, label, file, title in (
        ("whole_signal_snr_db", "Whole-signal SNR vs clean decode (dB, mean of runs)",
         "live_scenarios_snr.png", "Received audio quality vs clean loopback"),
        ("mean_mos", "MOS (E-model estimate, mean of runs)",
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
    ax.set_ylabel("Late packets (mean of runs)")
    ax.set_title("Late packets under 100 ms ± 30 ms delay")
    ax.grid(axis="y", alpha=0.3)
    fig.savefig(ROOT / "live_scenarios_late_by_depth.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def _summarise(name, knobs, repair, trials):
    """Mean over the seeds, which every scenario shares.

    Not a per-scenario median run: that can pick a different seed (so a
    different drop pattern) for FEC than for silence, breaking the paired
    comparison. Means keep counts additive (lost - recovered = residual), and
    loss percentages are pooled over the same counts.
    """
    kept = RESULTS_DIR / f"{name}.wav"       # audio of the middle seed only
    middle = trials[len(trials) // 2]
    Path(middle["received_wav"]).replace(kept)
    for trial in trials:
        Path(trial["received_wav"]).unlink(missing_ok=True)
        trial["received_wav"] = str(kept) if trial is middle else ""
    row = {"name": name, "knobs": json.dumps(knobs, sort_keys=True), "repair": repair,
           "runs": len(trials), "received_wav": str(kept)}
    for key in FIELDS:
        if key not in row:
            values = [t[key] for t in trials if t[key] is not None]
            row[key] = statistics.fmean(values) if values else None
    expected = sum(t["frames_expected"] for t in trials)
    row["measured_loss_pct"] = 100 * sum(
        t["frames_expected"] - t["frames_delivered"] for t in trials) / expected
    row["residual_loss_pct"] = 100 * sum(
        t["frames_lost"] - t["frames_recovered"] for t in trials) / expected
    return row


def run(quick=False, wav_path=None):
    seconds, repeats = (4.0, 1) if quick else (10.0, 3)
    matrix = scenarios()
    total = len(matrix) * repeats
    estimate = total * (seconds + 3.0)
    print(f"Estimated runtime: about {estimate / 60:.1f} minutes "
          f"({total} loopback calls at {seconds:g}s each).", flush=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # The reference cache key does not cover the pipeline code: never reuse one
    # decoded by an older build.
    for stale in RESULTS_DIR.glob("_clean_reference_*.wav"):
        stale.unlink()
    source = Path(wav_path) if wav_path else make_speech_wav(
        RESULTS_DIR / "speech_like.wav", seconds=seconds)
    with_lbrr, packets = lbrr_coverage(source)
    print(f"LBRR (FEC) data in {with_lbrr}/{packets} encoded packets of the source.", flush=True)
    summaries, runs = [], []
    for name, knobs, repair in matrix:
        trials = []
        for repetition in range(repeats):
            seed = 20261015 + repetition
            row = run_scenario(f"{name}.seed{seed}", knobs, seconds, source, seed=seed)
            row.update(name=name, knobs=json.dumps(knobs, sort_keys=True), repair=repair,
                       seed=seed)
            trials.append(row)
            print(f"{name} run {repetition + 1}/{repeats}: "
                  f"residual={row['residual_loss_pct']:.2f}% "
                  f"MOS={row['mean_mos']:.2f} SNR={row['whole_signal_snr_db']:.2f}dB",
                  flush=True)
        runs += trials
        summaries.append(_summarise(name, knobs, repair, trials))
    ROOT.mkdir(parents=True, exist_ok=True)
    for file, rows, extra in (("live_scenarios.csv", summaries, ["repair", "runs"]),
                              ("live_scenarios_runs.csv", runs, ["repair", "seed"])):
        with (ROOT / file).open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=FIELDS + extra, lineterminator="\n",
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    _charts(summaries)
    print(f"Wrote {ROOT / 'live_scenarios.csv'}, every run to "
          f"{ROOT / 'live_scenarios_runs.csv'}, and four charts.")
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="one 4-second run per scenario")
    parser.add_argument("--wav", help="optional 48 kHz mono 16-bit PCM input")
    args = parser.parse_args()
    run(quick=args.quick, wav_path=args.wav)
