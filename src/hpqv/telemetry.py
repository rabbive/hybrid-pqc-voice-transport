"""Per-second call telemetry: live MOS, loss before/after repair, citable export.

Driven by explicit sample(t) calls (the GUI ticks about once a second) rather
than a hidden thread, so tests are deterministic and the caller owns the clock.
Interval figures come from counter deltas between samples.

Every measured number describes the stream this peer RECEIVES (the other
side's audio), counted by the local receiver: the local sender's loss shows up
on the peer's screen, never on ours. Local knob values are recorded alongside
for context only.
"""
import csv
import os

from hpqv.controls import DEFAULTS
from hpqv.eval.mos import mos_from_network
from hpqv.packet import MAX_DATAGRAM

JITTER_FRAME_MS = 20     # one jitter-buffer slot = one 20 ms Opus frame

_COUNTERS = ["sent", "received", "dropped", "recovered", "concealed", "late",
             "incoming_dropped", "skipped", "rx_expected", "max_datagram", "key_gen"]
_DELTAS = _COUNTERS + ["rx_transit_sum_ms", "rx_transit_n"]
COLUMNS = (["t_s", "dt_s"] + _COUNTERS +
           ["interval_expected", "interval_delivered", "interval_lost",
            "interval_recovered", "interval_residual", "interval_loss_pct",
            "residual_loss_pct", "recovered_per_s", "rx_jitter_ms", "added_delay_ms",
            "effective_delay_ms", "mos"] +
           [f"knob_{k}" for k in DEFAULTS])
# Knobs that act on this peer's receiver, so they do shape the numbers measured
# here. Every other knob acts on the local sender: it changes what the PEER hears.
RECEIVER_KNOBS = ("incoming_loss_pct", "repair", "jitter_depth")


def _pct(part, whole):
    return 100.0 * part / whole if whole > 0 else 0.0


class Telemetry:
    def __init__(self, stats, controls):
        self._stats = stats
        self._controls = controls
        self._prev = stats.as_dict()          # baseline: first interval starts now
        self._knobs = controls.snapshot()
        self._t = 0.0                         # caller's clock starts at construction
        self._added_delay = 0.0               # held over intervals with no arrivals
        self.rows = []
        self.events = []

    def sample(self, t):
        """Append one row for the interval since the previous sample and return it."""
        cur = self._stats.as_dict()
        knobs = self._controls.snapshot()
        self._diff_events(t, cur, knobs)
        d = {k: cur[k] - self._prev[k] for k in _DELTAS}
        dt = t - self._t
        self._prev, self._knobs, self._t = cur, knobs, t

        # Expected = seq span of the peer's stream seen this interval (its sender
        # advances seq on frames it drops). Frames lost at the very end of an
        # interval are counted once a later packet arrives, as in RFC 3550.
        expected = d["rx_expected"]
        delivered = max(0, d["received"] - d["incoming_dropped"])
        # A late packet arrived but was useless: lost to the listener all the same.
        lost = max(0, expected - delivered + d["late"])
        # Only FEC rebuilds recover real audio; PLC/silence fills are not recovery.
        residual = max(0, lost - d["recovered"])
        residual_pct = _pct(residual, expected)
        if d["rx_transit_n"]:
            # One-way delay added on top of the lowest delay observed this call
            # (the peers' clocks are not synced, so the absolute delay is unknown).
            mean = d["rx_transit_sum_ms"] / d["rx_transit_n"]
            self._added_delay = max(0.0, mean - cur["rx_transit_min_ms"])
        delay = JITTER_FRAME_MS * knobs["jitter_depth"] + self._added_delay
        row = {"t_s": t, "dt_s": dt}
        row.update({k: cur[k] for k in _COUNTERS})
        row.update(
            interval_expected=expected, interval_delivered=delivered,
            interval_lost=lost, interval_recovered=d["recovered"],
            interval_residual=residual,
            interval_loss_pct=_pct(max(0, expected - delivered), expected),
            residual_loss_pct=residual_pct,
            recovered_per_s=d["recovered"] / dt if dt > 0 else 0.0,
            rx_jitter_ms=cur["rx_jitter_ms"], added_delay_ms=self._added_delay,
            effective_delay_ms=delay,
            # jitter_ms=0 on purpose: r_factor models a jitter buffer as +2x
            # jitter of delay, but `delay` already holds the real buffer
            # (20 ms x jitter_depth), and jitter beyond it shows up as late
            # packets in residual loss. Passing rx_jitter_ms would count it twice.
            mos=mos_from_network(delay, 0.0, residual_pct))
        row.update({f"knob_{k}": v for k, v in knobs.items()})
        self.rows.append(row)
        return row

    def _diff_events(self, t, cur, knobs):
        for name, new in knobs.items():
            old = self._knobs[name]
            if new != old:
                self.events.append({"t": t, "kind": "knob", "name": name, "old": old,
                                    "new": new, "label": f"local {name}: {old} -> {new}"})
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
                   for k in ("expected", "delivered", "lost", "recovered", "residual")}
            mos = [r["mos"] for r in rows]
            result.append({
                "settings": {k: v for k, v in seg["_knobs"].items() if v != DEFAULTS[k]},
                "start_t": rows[0]["t_s"], "end_t": rows[-1]["t_s"], "samples": len(rows),
                **tot,
                "residual_loss_pct": _pct(tot["residual"], tot["expected"]),
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
             "All loss, recovery, delay and MOS figures below are measured by this peer's "
             "receiver on the audio it receives from the other peer. This peer's own "
             "sender settings change what the OTHER peer hears and are measured there.", "",
             "## Totals", "",
             f"- Received stream (the peer's audio): expected {s['rx_expected']} frames; "
             f"received: {s['received']}; dropped on receipt: {s['incoming_dropped']}; "
             f"late: {s['late']}",
             f"- Frames rebuilt by FEC (recovered): {s['recovered']}; "
             f"filled by PLC/silence (concealed): {s['concealed']}; "
             f"skipped to keep speaker latency bounded: {s['skipped']}",
             f"- Local sender (measured on the peer, not here): sent {s['sent']}, "
             f"dropped by sender {s['dropped']}; largest datagram {s['max_datagram']} B "
             f"(cap {MAX_DATAGRAM} B)",
             f"- Key generation: {s['key_gen']}",
             f"- Handshake: {s['handshake_ms']:.1f} ms, {s['handshake_bytes']} B "
             "(HELLO + ACCEPT payloads, over TCP)", "",
             "## Segments", "",
             "A segment is a run of samples with identical local knob settings "
             "(non-default shown). Each sample covers the interval since the previous "
             "sample (dt_s, about 1 s) and is attributed to the settings in force at that "
             "moment, so the interval containing a change counts toward the new settings. "
             "The peer's knobs are not visible here: a change in measured loss or delay "
             "with no local receiver-knob change was made on the peer's side.", ""]

        def fmt(d):
            return ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool)
                             else f"{k}={v}" for k, v in d.items()) or "defaults"

        for i, g in enumerate(segs, 1):
            tx = {k: v for k, v in g["settings"].items() if k not in RECEIVER_KNOBS}
            rx = {k: v for k, v in g["settings"].items() if k in RECEIVER_KNOBS}
            ratio = ("n/a (no loss)" if g["recovered_per_lost"] is None
                     else f"{g['recovered_per_lost']:.2f}")
            L += [f"### Segment {i}", "",
                  f"- Time: {g['start_t']:g}-{g['end_t']:g} s ({g['samples']} samples)",
                  f"- In force: local sender knobs (affect what the PEER hears): {fmt(tx)}; "
                  f"local receiver knobs (shape the numbers below): {fmt(rx)}",
                  f"- Received frames expected: {g['expected']}; delivered: {g['delivered']}; "
                  f"lost (incl. late): {g['lost']}",
                  f"- Recovered by FEC: {g['recovered']} frames; recovered/lost: {ratio}",
                  f"- Residual loss after repair: {g['residual_loss_pct']:.2f} % "
                  f"({g['residual']} of {g['expected']} frames never played)",
                  f"- MOS (E-model estimate, 1-4.5): mean {g['mean_mos']:.2f}, "
                  f"min {g['min_mos']:.2f}", ""]
        L += ["## Events", ""]
        L += [f"- {e['t']:g} s: {e['label']}" for e in self.events] or ["- none"]
        L += ["", "## Definitions", "",
              "- Expected = span of sequence numbers received from the peer. Its sender "
              "advances the sequence on frames it drops, so the span counts every frame its "
              "encoder produced. Frames lost at the end of an interval are counted when a "
              "later packet arrives (RFC 3550); a stream that stops entirely shows no loss.",
              "- Delivered = received - dropped on receipt. Interval loss % = (expected - "
              "delivered) / expected.",
              "- Lost = expected - delivered + late. Residual loss % = (lost - FEC-recovered) / "
              "expected: frames never played as real audio. Recovered counts only frames "
              "rebuilt from LBRR data the next packet actually carried.",
              "- rx_jitter_ms = RFC 3550 interarrival jitter of the received stream.",
              "- added_delay_ms = mean one-way transit this interval minus the minimum seen "
              "so far, i.e. delay relative to the lowest delay observed this call. Transit = "
              "arrival time - seq x 20 ms; clocks are not synced, so absolute one-way delay "
              "is not known and a base network delay present from the first packet is "
              "not included.",
              "- MOS = ITU-T G.107 E-model estimate (eval.mos) from residual loss % and delay "
              "= 20 ms x jitter_depth (local buffer) + added_delay_ms, with the model's "
              "jitter term set to 0 because the buffer is already counted in delay and "
              "jitter beyond it appears as late packets. It is a model, not a listening test.",
              "- knob_* columns are this peer's settings at sample time. Sender knobs "
              "(loss_pct, loss_model, burst_len, delay_ms, jitter_ms, fec, bitrate) affect "
              "the audio the PEER receives and do not appear in these measurements.", ""]
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
        draw(loss_png, "Received stream: loss vs recovery (dotted lines = local "
             "setting changes / events)",
             "% of the peer's frames expected, per interval",
             [("Network loss (expected vs delivered)", col("interval_loss_pct")),
              ("Recovered by FEC", [_pct(r["interval_recovered"], r["interval_expected"])
                                    for r in self.rows]),
              ("Residual loss after repair", col("residual_loss_pct"))])
        draw(mos_png, "Estimated MOS of the received stream (E-model)", "MOS (1-4.5)",
             [("MOS", col("mos"))], ylim=(1, 4.6))
        draw(size_png, "Largest datagram sent", "Datagram size (bytes)",
             [("max datagram", col("max_datagram"))],
             hline=(MAX_DATAGRAM, f"{MAX_DATAGRAM} B cap"))


_STYLES = [
    {"linestyle": "-", "marker": "o", "linewidth": 2.4, "markersize": 5},
    {"linestyle": "--", "marker": "s", "linewidth": 1.8, "markersize": 4},
    {"linestyle": ":", "marker": "^", "linewidth": 1.4, "markersize": 4},
]
