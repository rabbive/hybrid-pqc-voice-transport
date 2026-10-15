"""Per-second call telemetry: live MOS, loss before/after repair, citable export.

Driven by explicit sample(t) calls (the GUI ticks once a second) rather than a
hidden thread, so tests are deterministic and the caller owns the clock.
Interval figures come from counter deltas between samples; counters that a
build does not maintain yet simply stay 0.
"""
import csv
import os

from hpqv.controls import DEFAULTS
from hpqv.eval.mos import mos_from_network
from hpqv.packet import MAX_DATAGRAM

JITTER_FRAME_MS = 20     # one jitter-buffer slot = one 20 ms Opus frame

_COUNTERS = ["sent", "received", "dropped", "recovered", "concealed", "late",
             "incoming_dropped", "max_datagram", "key_gen"]
COLUMNS = (["t_s"] + _COUNTERS +
           ["interval_offered", "interval_delivered", "interval_lost",
            "interval_recovered", "interval_residual", "interval_loss_pct",
            "residual_loss_pct", "effective_delay_ms", "mos"] +
           [f"knob_{k}" for k in DEFAULTS])


def _pct(part, whole):
    return 100.0 * part / whole if whole > 0 else 0.0


class Telemetry:
    def __init__(self, stats, controls):
        self._stats = stats
        self._controls = controls
        self._prev = stats.as_dict()          # baseline: first interval starts now
        self._knobs = controls.snapshot()
        self.rows = []
        self.events = []

    def sample(self, t):
        """Append one row for the interval since the previous sample and return it."""
        cur = self._stats.as_dict()
        knobs = self._controls.snapshot()
        self._diff_events(t, cur, knobs)
        d = {k: cur[k] - self._prev[k] for k in _COUNTERS}
        self._prev, self._knobs = cur, knobs

        # Offered = what the sender produced (sent + deliberately dropped). The
        # receiver sees `received`; incoming drops happen after that count.
        offered = d["sent"] + d["dropped"]
        delivered = max(0, d["received"] - d["incoming_dropped"])
        # A late packet arrived but was useless: lost to the listener all the same.
        lost = max(0, offered - delivered + d["late"])
        # Only FEC rebuilds recover real audio; PLC/silence fills are not recovery.
        residual = max(0, lost - d["recovered"])
        residual_pct = _pct(residual, offered)
        delay = knobs["delay_ms"] + JITTER_FRAME_MS * knobs["jitter_depth"]
        row = {"t_s": t}
        row.update({k: cur[k] for k in _COUNTERS})
        row.update(
            interval_offered=offered, interval_delivered=delivered,
            interval_lost=lost, interval_recovered=d["recovered"],
            interval_residual=residual,
            interval_loss_pct=_pct(max(0, offered - delivered), offered),
            residual_loss_pct=residual_pct, effective_delay_ms=delay,
            mos=mos_from_network(delay, knobs["jitter_ms"], residual_pct))
        row.update({f"knob_{k}": v for k, v in knobs.items()})
        self.rows.append(row)
        return row

    def _diff_events(self, t, cur, knobs):
        for name, new in knobs.items():
            old = self._knobs[name]
            if new != old:
                self.events.append({"t": t, "kind": "knob", "name": name, "old": old,
                                    "new": new, "label": f"{name}: {old} -> {new}"})
        if cur["key_gen"] != self._prev["key_gen"]:
            self.events.append({"t": t, "kind": "key",
                                "label": f"key rotation -> generation {cur['key_gen']}"})

    def note(self, event, t):
        """Free-form marker for things the counters cannot show."""
        self.events.append({"t": t, "kind": "note", "label": event})

    def segments(self):
        """Rows grouped into runs of identical settings.

        A row belongs to the settings in force when it was sampled, so the
        interval containing a change counts toward the new setting.
        """
        out = []
        for row in self.rows:
            knobs = {k: row[f"knob_{k}"] for k in DEFAULTS}
            if not out or out[-1]["_knobs"] != knobs:
                out.append({"_knobs": knobs, "rows": []})
            out[-1]["rows"].append(row)
        result = []
        for seg in out:
            rows = seg["rows"]
            tot = {k: sum(r[f"interval_{k}"] for r in rows)
                   for k in ("offered", "delivered", "lost", "recovered", "residual")}
            mos = [r["mos"] for r in rows]
            result.append({
                "settings": {k: v for k, v in seg["_knobs"].items() if v != DEFAULTS[k]},
                "start_t": rows[0]["t_s"], "end_t": rows[-1]["t_s"], "samples": len(rows),
                **tot,
                "residual_loss_pct": _pct(tot["residual"], tot["offered"]),
                "recovered_per_lost": (tot["recovered"] / tot["lost"]) if tot["lost"] else None,
                "mean_mos": sum(mos) / len(mos), "min_mos": min(mos)})
        return result

    def export(self, out_dir):
        """Write timeline.csv, summary.md and three PNG charts; return their paths."""
        os.makedirs(out_dir, exist_ok=True)
        paths = [os.path.join(out_dir, n) for n in
                 ("timeline.csv", "summary.md", "loss_vs_recovered.png",
                  "mos.png", "datagram_size.png")]
        with open(paths[0], "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(self.rows)
        with open(paths[1], "w", encoding="utf-8") as f:
            f.write(self._summary())
        self._charts(*paths[2:])
        return paths

    def _summary(self):
        s = self._stats.as_dict()
        segs = self.segments()
        span = (f"{self.rows[0]['t_s']:g}-{self.rows[-1]['t_s']:g} s, {len(self.rows)} samples"
                if self.rows else "no samples")
        L = ["# Call telemetry summary", "",
             f"Sampled: {span}.", "",
             "## Totals", "",
             f"- Datagrams sent: {s['sent']}; dropped by sender: {s['dropped']}; "
             f"received: {s['received']}; dropped on receipt: {s['incoming_dropped']}; "
             f"late: {s['late']}",
             f"- Frames rebuilt by FEC (recovered): {s['recovered']}; "
             f"filled by PLC/silence (concealed): {s['concealed']}",
             f"- Largest datagram sent: {s['max_datagram']} B (cap {MAX_DATAGRAM} B)",
             f"- Key generation: {s['key_gen']}",
             f"- Handshake: {s['handshake_ms']:.1f} ms, {s['handshake_bytes']} B "
             "(HELLO + ACCEPT payloads, over TCP)", "",
             "## Segments", "",
             "A segment is a run of samples with identical settings (non-default shown). "
             "Each sample covers the 1 s interval ending at its time and is attributed to the "
             "settings in force at that moment, so the interval containing a change counts "
             "toward the new settings.", ""]
        for i, g in enumerate(segs, 1):
            settings = ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool)
                                 else f"{k}={v}" for k, v in g["settings"].items()) or "all defaults"
            ratio = ("n/a (no loss)" if g["recovered_per_lost"] is None
                     else f"{g['recovered_per_lost']:.2f}")
            L += [f"### Segment {i}: {settings}", "",
                  f"- Time: {g['start_t']:g}-{g['end_t']:g} s ({g['samples']} samples)",
                  f"- Frames offered: {g['offered']}; delivered: {g['delivered']}; "
                  f"lost (incl. late): {g['lost']}",
                  f"- Recovered by FEC: {g['recovered']} frames; recovered/lost: {ratio}",
                  f"- Residual loss after repair: {g['residual_loss_pct']:.2f} % "
                  f"({g['residual']} of {g['offered']} frames never played)",
                  f"- MOS (E-model estimate, 1-4.5): mean {g['mean_mos']:.2f}, "
                  f"min {g['min_mos']:.2f}", ""]
        L += ["## Events", ""]
        L += [f"- {e['t']:g} s: {e['label']}" for e in self.events] or ["- none"]
        L += ["", "## Definitions", "",
              "- Offered = sent + dropped by the sender (frames the encoder produced).",
              "- Delivered = received - dropped on receipt. Interval loss % = (offered - "
              "delivered) / offered.",
              "- Lost = offered - delivered + late. Residual loss % = (lost - FEC-recovered) / "
              "offered: frames never played as real audio.",
              "- MOS = ITU-T G.107 E-model estimate (eval.mos) from residual loss %, jitter "
              "(ms) and delay = delay_ms + 20 ms x jitter_depth. It is a model, not a "
              "listening test.",
              "- Offered counts the local sender and delivered the local receiver, so the "
              "ratio is only meaningful for a symmetric call such as a loopback.", ""]
        return "\n".join(L)

    def _charts(self, loss_png, mos_png, size_png):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure     # no pyplot: no global backend state
        t = [r["t_s"] for r in self.rows]

        def draw(path, title, ylabel, series, hline=None, ylim=None):
            fig = Figure(figsize=(8, 4.5))
            FigureCanvasAgg(fig)
            ax = fig.add_subplot()
            # Distinct dash/marker per series (house style): series often coincide.
            for (label, ys), style in zip(series, _STYLES):
                ax.plot(t, ys, label=label, **style)
            if hline:
                ax.axhline(hline[0], color="red", linestyle="-.", label=hline[1])
            by_t = {}                            # simultaneous events share one marker
            for e in self.events:
                by_t.setdefault(e["t"], []).append(e["label"])
            for et, labels in by_t.items():
                ax.axvline(et, color="gray", alpha=0.6, linestyle=(0, (1, 3)))
                ax.annotate("; ".join(labels), (et, 1), xycoords=("data", "axes fraction"),
                            rotation=90, va="top", ha="right", fontsize=6, color="gray")
            ax.set(xlabel="Call time (s)", ylabel=ylabel, title=title)
            if ylim:
                ax.set_ylim(*ylim)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize=8)
            fig.savefig(path, dpi=150, bbox_inches="tight")

        col = lambda k: [r[k] for r in self.rows]
        draw(loss_png, "Loss vs recovery (dotted lines = setting changes / events)",
             "% of frames offered, per 1 s interval",
             [("Network loss (offered vs delivered)", col("interval_loss_pct")),
              ("Recovered by FEC", [_pct(r["interval_recovered"], r["interval_offered"])
                                    for r in self.rows]),
              ("Residual loss after repair", col("residual_loss_pct"))])
        draw(mos_png, "Estimated MOS (E-model)", "MOS (1-4.5)",
             [("MOS", col("mos"))], ylim=(1, 4.6))
        draw(size_png, "Largest datagram sent", "Datagram size (bytes)",
             [("max datagram", col("max_datagram"))],
             hline=(MAX_DATAGRAM, f"{MAX_DATAGRAM} B cap"))


_STYLES = [
    {"linestyle": "-", "marker": "o", "linewidth": 2.4, "markersize": 5},
    {"linestyle": "--", "marker": "s", "linewidth": 1.8, "markersize": 4},
    {"linestyle": ":", "marker": "^", "linewidth": 1.4, "markersize": 4},
]
