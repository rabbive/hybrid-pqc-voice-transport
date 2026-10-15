"""GUI decisions that need no Tk: presets, slider <-> knob mapping, stats text.

Kept apart from the widgets so they are unit-testable and so there is one
definition of every range and label shared by the sliders and the tests.
"""
import copy
import wave
from dataclasses import dataclass
from typing import Callable

from hpqv.audio import CHANNELS, FS
from hpqv.controls import DEFAULTS
from hpqv.telemetry import JITTER_FRAME_MS

# Presets own the network knobs and nothing else: repair mode, FEC and bitrate
# are what the presenter A/Bs while a preset is held, so a preset must not reset them.
NETWORK_KNOBS = ("loss_pct", "loss_model", "burst_len", "delay_ms", "jitter_ms",
                 "incoming_loss_pct", "jitter_depth")

# Overrides on the network defaults. A preset writes every network knob, so picking
# one never leaves a stale setting from the previous preset behind.
PRESETS = {
    "Clean": {},
    "Bad Wi-Fi": dict(loss_pct=5, loss_model="random", delay_ms=40, jitter_ms=20),
    "Burst loss": dict(loss_pct=10, loss_model="burst", burst_len=4),
    "Congested": dict(loss_pct=20, loss_model="random", delay_ms=150, jitter_ms=60, jitter_depth=6),
}


def preset_values(name) -> dict:
    return {**{k: DEFAULTS[k] for k in NETWORK_KNOBS}, **PRESETS[name]}


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
    SliderSpec("loss_pct", "Outgoing loss\napplied to audio the peer hears", 0, 50, _unit("%")),
    SliderSpec("burst_len", "Mean burst length", 1, 10, lambda v: f"{v:.0f} packets"),
    SliderSpec("delay_ms", "Delay", 0, 500, _unit("ms")),
    SliderSpec("jitter_ms", "Jitter", 0, 200, _unit("ms")),
    SliderSpec("incoming_loss_pct", "Incoming loss\napplied to audio you hear", 0, 50, _unit("%")),
    SliderSpec("jitter_depth", "Jitter buffer", 1, 15, _depth_text),
    SliderSpec("bitrate", "Opus bitrate", 7, 64, _bitrate_text, _bitrate_to_knob, _bitrate_to_slider),
]


# ---------------- live numbers ----------------

NONE = "—"
# demo.handshake_info() hard-codes 0: true by construction, not measured live.
FRAGMENTS_BY_DESIGN = "(by design: handshake is on TCP)"


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
        ("Loss you hear", pct("interval_loss_pct")),
        ("Residual loss", pct("residual_loss_pct")),
        ("MOS (audio you hear)", f"{row['mos']:.2f}" if row else NONE),
    ]


def handshake_rows(info, stats: dict) -> list:
    """(label, text) pairs for the handshake panel. `info` is demo.handshake_info() or None."""
    ms, biggest = stats["handshake_ms"], stats["max_datagram"]
    if info:
        cap = info["datagram_cap"]
        facts = [("Key exchange", info["kem"]), ("Signature", info["sig"]),
                 ("HELLO / ACCEPT / total",
                  f"{info['hello_bytes']} / {info['accept_bytes']} / {info['total_bytes']} B"),
                 ("Transport", info["transport"]),
                 ("UDP fragments", f"{info['udp_fragments']} {FRAGMENTS_BY_DESIGN}")]
    else:
        cap = 1200
        facts = [("Key exchange", "unavailable"), ("Signature", "unavailable"),
                 ("HELLO / ACCEPT / total", NONE), ("Transport", "TCP"),
                 ("UDP fragments", f"0 {FRAGMENTS_BY_DESIGN}")]
    return facts + [("Handshake time", f"{ms:.1f} ms" if ms else NONE),
                    ("Largest datagram", f"{biggest} B  (cap {cap} B)" if biggest else NONE)]


# ---------------- call lifecycle ----------------

def validate_wav(path) -> "str | None":
    """Why `path` cannot be a call input, or None. Mirrors demo.FrameSource's checks."""
    try:
        with wave.open(path, "rb") as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    except OSError as e:
        return f"Cannot open {path}: {e.strerror or e}"
    except (wave.Error, EOFError) as e:
        return f"{path} is not a readable PCM WAV file ({e})."
    if (rate, ch, width) != (FS, CHANNELS, 2):
        return (f"WAV must be {FS // 1000} kHz mono 16-bit PCM; this file is "
                f"{rate / 1000:g} kHz, {ch} channel(s), {width * 8}-bit.")
    return None


def end_text(hung_up: bool, peer_ended: bool) -> str:
    """State-label text once the call is over; claims the peer only when it really ended it."""
    if hung_up:
        return "hung up"
    return "call ended by peer" if peer_ended else "call ended"


def rotate_button_state(live: bool, ready: bool) -> tuple:
    """(enabled, label) for Rotate keys: one rotation at a time while the old key retires."""
    if live and not ready:
        return False, "Old key retiring…"
    return live, "Rotate keys"


class CallClock:
    """Seconds since the call connected, frozen at hang-up so the graph keeps its data."""

    def __init__(self, clock):
        self._clock = clock
        self._t0, self._frozen = 0.0, None

    def start(self):
        self._t0, self._frozen = self._clock(), None

    def freeze(self):
        if self._frozen is None:
            self._frozen = self._clock() - self._t0

    def now(self) -> float:
        return self._frozen if self._frozen is not None else self._clock() - self._t0


def snapshot_for_export(telemetry):
    """A Telemetry copy whose rows/events a worker can read while sampling carries on.

    Take it on the main thread. Rows are never mutated after being appended, so
    copying the lists is enough.
    """
    snap = copy.copy(telemetry)
    snap.rows, snap.events = list(telemetry.rows), list(telemetry.events)
    return snap
