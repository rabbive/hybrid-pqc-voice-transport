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
