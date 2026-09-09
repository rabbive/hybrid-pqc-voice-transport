import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _group_by_name(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["name"], []).append(row)
    return groups


def _plot(rows, out_png, x_key, y_key, xlabel, ylabel, title):
    fig, ax = plt.subplots()
    for name, group in _group_by_name(rows).items():
        group = sorted(group, key=lambda r: r[x_key])
        ax.plot([r[x_key] for r in group], [r[y_key] for r in group],
                marker="o", label=name)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()
    fig.savefig(out_png, dpi=150)
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
