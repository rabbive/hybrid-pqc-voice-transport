import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Distinct style per series. Series often coincide exactly (e.g. hybrid and tcp
# both sit at 0 fragments), so identical solid lines would hide one under the
# other. Varying dash pattern, marker and width keeps every series readable.
_STYLES = [
    {"linestyle": "-", "marker": "o", "linewidth": 2.4, "markersize": 7},
    {"linestyle": "--", "marker": "s", "linewidth": 1.8, "markersize": 6},
    {"linestyle": ":", "marker": "^", "linewidth": 1.4, "markersize": 5},
]


def _group_by_name(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["name"], []).append(row)
    return groups


def _plot(rows, out_png, x_key, y_key, xlabel, ylabel, title):
    fig, ax = plt.subplots()
    for i, (name, group) in enumerate(_group_by_name(rows).items()):
        group = sorted(group, key=lambda r: r[x_key])
        ax.plot([r[x_key] for r in group], [r[y_key] for r in group],
                label=name, **_STYLES[i % len(_STYLES)])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_png


def plot_loss_vs_mos(rows, out_png):
    return _plot(rows, out_png, "loss_pct", "mos",
                 "Packet loss (%)", "MOS", "MOS vs packet loss")


def plot_loss_vs_fragments(rows, out_png):
    return _plot(rows, out_png, "loss_pct", "frag_count",
                 "Packet loss (%)", "Fragment count", "Fragment count vs packet loss")


def plot_latency_vs_ttfb(rows, out_png):
    return _plot(rows, out_png, "delay_ms", "ttfb_ms",
                 "Delay (ms)", "TTFB (ms)", "TTFB vs delay")


def plot_loss_vs_success(rows, out_png):
    return _plot(rows, out_png, "loss_pct", "success_pct",
                 "Packet loss (%)", "Handshake success (%)",
                 "Handshake completion vs packet loss")


# Categorical slots 1 and 2 of the dataviz reference palette (CVD-validated pair).
_MODE_COLORS = ["#2a78d6", "#eb6834"]
_BENCH_PANELS = [("handshake_ms", "Handshake time (ms)", "{:.1f}"),
                 ("handshake_bytes", "Bytes on the wire", "{:,.0f}"),
                 ("cpu_ms", "CPU time (ms)", "{:.1f}")]


def plot_handshake_bench(summary, out_png, labels=None):
    """One panel per metric (different units, so no shared axis): median bar,
    p25-p75 whisker, value at the tip, ratio of first mode to second in the title."""
    modes = list(summary)
    names = [(labels or {}).get(m, m) for m in modes]
    fig, axes = plt.subplots(1, len(_BENCH_PANELS), figsize=(12, 2.8))
    for ax, (key, title, fmt) in zip(axes, _BENCH_PANELS):
        s = [summary[m][key] for m in modes]
        med = [x["median"] for x in s]
        err = [[x["median"] - x["p25"] for x in s], [x["p75"] - x["median"] for x in s]]
        y = range(len(modes))
        ax.barh(y, med, height=0.5, color=_MODE_COLORS[:len(modes)],
                xerr=err, error_kw={"ecolor": "#52514e", "elinewidth": 1, "capsize": 3})
        xmax = max(x["p75"] for x in s) * 1.3
        for i, (m, x) in enumerate(zip(med, s)):
            ax.text(max(m, x["p75"]) + xmax * 0.03, i, fmt.format(m), va="center",
                    color="#0b0b0b")
        ax.set_yticks(list(y), names)
        ax.invert_yaxis()
        ax.set_xlim(0, xmax)
        ax.set_title(f"{title}: {med[0] / med[1]:.1f}×", loc="left")
        ax.grid(True, axis="x", alpha=0.3)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    for ax in axes[1:]:
        ax.set_yticklabels([])
    fig.suptitle(f"{names[0].splitlines()[0]} vs {names[1].splitlines()[0]}: "
                 "median, whisker p25-p75, title shows the ratio", x=0.01, ha="left",
                 color="#52514e", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_png
