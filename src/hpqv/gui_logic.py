"""GUI decisions that need no Tk: presets, slider <-> knob mapping, stats text.

Kept apart from the widgets so they are unit-testable and so there is one
definition of every range and label shared by the sliders and the tests.
"""
from dataclasses import dataclass
from typing import Callable

from hpqv.controls import DEFAULTS
from hpqv.telemetry import JITTER_FRAME_MS

# Overrides on DEFAULTS. Presets always write every knob, so picking one never
# leaves a stale setting from the previous preset behind.
PRESETS = {
    "Clean": {},
    "Bad Wi-Fi": dict(loss_pct=5, loss_model="random", delay_ms=40, jitter_ms=20),
    "Burst loss": dict(loss_pct=10, loss_model="burst", burst_len=4),
    "Congested": dict(loss_pct=20, loss_model="random", delay_ms=150, jitter_ms=60, jitter_depth=6),
}


def preset_values(name) -> dict:
    return {**DEFAULTS, **PRESETS[name]}


# ---------------- sliders ----------------

def _round(v):
    return round(v)


def _same(v):
    return v


def _bitrate_to_knob(v):
    return 0 if v < 8 else int(round(v)) * 1000     # bottom stop of the slider (7) = Auto


def _bitrate_to_slider(bps):
    return 7 if not bps else bps / 1000


def _bitrate_text(bps):
    return "Auto" if not bps else f"{int(bps) // 1000} kbit/s"


def _depth_text(d):
    return f"{int(d)} frame{'' if d == 1 else 's'} = {JITTER_FRAME_MS * int(d)} ms"


@dataclass(frozen=True)
class SliderSpec:
    knob: str
    label: str
    lo: float
    hi: float
    fmt: Callable                  # knob value -> text next to the slider
    to_knob: Callable = _round     # raw Scale position -> knob value
    to_slider: Callable = _same    # knob value -> Scale position


def _unit(unit):
    return lambda v: f"{v:.0f} {unit}"


SLIDERS = [
    SliderSpec("loss_pct", "Outgoing loss", 0, 50, _unit("%")),
    SliderSpec("burst_len", "Mean burst length", 1, 10, lambda v: f"{v:.0f} packets"),
    SliderSpec("delay_ms", "Delay", 0, 500, _unit("ms")),
    SliderSpec("jitter_ms", "Jitter", 0, 200, _unit("ms")),
    SliderSpec("incoming_loss_pct", "Incoming loss", 0, 50, _unit("%")),
    SliderSpec("jitter_depth", "Jitter buffer", 1, 15, _depth_text),
    SliderSpec("bitrate", "Opus bitrate", 7, 64, _bitrate_text, _bitrate_to_knob, _bitrate_to_slider),
]


# ---------------- live numbers ----------------

NONE = "—"


def stat_tiles(stats: dict, row) -> list:
    """(label, text) pairs for the counter grid. `row` is Telemetry's latest row or None."""
    pct = lambda k: f"{row[k]:.1f} %" if row else NONE
    return [
        ("Sent", str(stats["sent"])),
        ("Received", str(stats["received"])),
        ("Dropped (out)", str(stats["dropped"])),
        ("Dropped (in)", str(stats["incoming_dropped"])),
        ("Late", str(stats["late"])),
        ("Recovered (FEC)", str(stats["recovered"])),
        ("Concealed", str(stats["concealed"])),
        ("Measured loss", pct("interval_loss_pct")),
        ("Residual loss", pct("residual_loss_pct")),
        ("MOS", f"{row['mos']:.2f}" if row else NONE),
    ]


def handshake_rows(info, stats: dict) -> list:
    """(label, text) pairs for the handshake panel. `info` is demo.handshake_info() or None."""
    ms, biggest = stats["handshake_ms"], stats["max_datagram"]
    if info:
        cap = info["datagram_cap"]
        facts = [("Key exchange", info["kem"]), ("Signature", info["sig"]),
                 ("HELLO / ACCEPT / total",
                  f"{info['hello_bytes']} / {info['accept_bytes']} / {info['total_bytes']} B"),
                 ("Transport", info["transport"]), ("UDP fragments", str(info["udp_fragments"]))]
    else:
        cap = 1200
        facts = [("Key exchange", "unavailable"), ("Signature", "unavailable"),
                 ("HELLO / ACCEPT / total", NONE), ("Transport", "TCP"), ("UDP fragments", "0")]
    return facts + [("Handshake time", f"{ms:.1f} ms" if ms else NONE),
                    ("Largest datagram", f"{biggest} B  (cap {cap} B)" if biggest else NONE)]
