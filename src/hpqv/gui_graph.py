"""Scrolling 60 s graph: pure scaling (testable) plus a thin Canvas that draws it.

Left axis: loss %, residual loss % and recovered frames/s share one auto scale
(a frame is ~1 % of a 50 fps stream, so the units line up closely enough to read
together). Right axis: MOS, fixed 1-4.5. Samples arrive once a second, so
interval_recovered is already "per second".
"""
import math
import tkinter as tk
from typing import NamedTuple

WINDOW_S = 60.0
MOS_LO, MOS_HI = 1.0, 4.5


class Box(NamedTuple):
    left: float
    top: float
    right: float
    bottom: float


class Series(NamedTuple):
    name: str
    key: str
    axis: str            # "left" or "mos"
    color: str
    dash: tuple          # distinct dash per line: they often coincide


SERIES = [
    Series("Loss %", "interval_loss_pct", "left", "#d62728", ()),
    Series("Residual %", "residual_loss_pct", "left", "#ff7f0e", (6, 3)),
    Series("Recovered /s", "interval_recovered", "left", "#2ca02c", (2, 3)),
    Series("MOS", "mos", "mos", "#1f77b4", (10, 3, 2, 3)),
]


def nice_max(v, floor=10.0):
    for step in (10, 20, 50, 100):
        if v <= step:
            return float(max(step, floor))
    return 100.0


def _t_lo(now):
    # Fill rightward from call start, then scroll once 60 s have passed.
    return max(0.0, now - WINDOW_S)


def x_of(t, box, now):
    return box.left + (t - _t_lo(now)) / WINDOW_S * (box.right - box.left)


def scale_series(rows, box, now):
    """Map telemetry rows to canvas points. Returns ({name: [(x, y)]}, left_axis_max)."""
    lo = _t_lo(now)
    rows = [r for r in rows if r["t_s"] >= lo]
    peak = max((r[s.key] for r in rows for s in SERIES if s.axis == "left"), default=0.0)
    y_max = nice_max(peak)
    height = box.bottom - box.top

    def y_of(s, v):
        frac = (v - MOS_LO) / (MOS_HI - MOS_LO) if s.axis == "mos" else v / y_max
        return box.bottom - max(0.0, min(1.0, frac)) * height

    return ({s.name: [(x_of(r["t_s"], box, now), y_of(s, r[s.key])) for r in rows] for s in SERIES},
            y_max)


class GraphCanvas(tk.Canvas):
    """Redraws from scratch each second; 60 points x 4 lines is trivial for Tk."""

    def __init__(self, parent, **kw):
        super().__init__(parent, background="white", highlightthickness=1,
                         highlightbackground="#999", **kw)
        self._last = ([], [], 0.0)
        self.bind("<Configure>", lambda _e: self.redraw(*self._last))

    def redraw(self, rows, events, now):
        self._last = (rows, events, now)
        self.delete("all")
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 120)
        box = Box(48, 10, w - 48, h - 24)
        series, y_max = scale_series(rows, box, now)
        for i in range(5):                               # horizontal grid + both axes' labels
            y = box.bottom - i / 4 * (box.bottom - box.top)
            self.create_line(box.left, y, box.right, y, fill="#e4e4e4")
            self.create_text(box.left - 4, y, text=f"{y_max * i / 4:g}", anchor="e", font=("", 9))
            self.create_text(box.right + 4, y, text=f"{MOS_LO + (MOS_HI - MOS_LO) * i / 4:.1f}",
                             anchor="w", font=("", 9), fill=SERIES[3].color)
        lo = _t_lo(now)
        for k in range(0, int(WINDOW_S) + 1, 15):
            self.create_text(x_of(lo + k, box, now), box.bottom + 4, text=f"{lo + k:.0f}s",
                             anchor="n", font=("", 9))
        for e in events:                                 # key rotations as vertical markers
            if e["kind"] == "key" and e["t"] >= lo:
                x = x_of(e["t"], box, now)
                self.create_line(x, box.top, x, box.bottom, fill="#888", dash=(1, 3))
                self.create_text(x, box.top, text="key", anchor="nw", font=("", 8), fill="#666")
        for s in SERIES:
            pts = series[s.name]
            if len(pts) > 1:
                self.create_line(*[c for p in pts for c in p], fill=s.color, width=2,
                                 dash=s.dash or None)
            elif pts:                                    # one sample: a dot, not a line
                x, y = pts[0]
                self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=s.color, outline=s.color)
