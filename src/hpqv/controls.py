"""Every knob the demo can turn while a call is running.

The GUI writes, the sender/receiver threads read once per frame, so a change
lands within 20 ms without hanging up. One lock guards the whole set because
some knobs only make sense together (loss model + burst length).
"""
import threading

DEFAULTS = {
    "loss_pct": 0.0,            # outgoing datagrams dropped by the sender
    "loss_model": "random",     # "random" (independent) or "burst" (Gilbert-Elliott)
    "burst_len": 3.0,           # mean lost-packet run length in burst mode
    "delay_ms": 0.0,            # added one-way delay on outgoing datagrams
    "jitter_ms": 0.0,           # +/- random spread around delay_ms
    "incoming_loss_pct": 0.0,   # incoming datagrams dropped by the receiver
    "fec": True,                # sender embeds Opus in-band FEC
    "repair": "fec",            # receiver gap repair: "silence", "plc" or "fec"
    "bitrate": 0,               # Opus bitrate in bit/s; 0 = encoder default
    "jitter_depth": 3,          # jitter buffer depth in frames
}
_RANGES = {
    "loss_pct": (0.0, 100.0), "incoming_loss_pct": (0.0, 100.0),
    "burst_len": (1.0, 50.0), "delay_ms": (0.0, 2000.0), "jitter_ms": (0.0, 1000.0),
    "bitrate": (0, 512000), "jitter_depth": (1, 50),
}
_CHOICES = {"loss_model": ("random", "burst"), "repair": ("silence", "plc", "fec")}


class CallControls:
    def __init__(self, **values):
        self._lock = threading.Lock()
        self._v = dict(DEFAULTS)
        self.set(**values)

    def get(self, name):
        with self._lock:
            return self._v[name]

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._v)

    def set(self, **values) -> None:
        clean = {}
        for name, value in values.items():
            if name not in DEFAULTS:
                raise KeyError(name)
            if name in _CHOICES:
                if value not in _CHOICES[name]:
                    raise ValueError(f"{name} must be one of {_CHOICES[name]}")
            elif name == "fec":
                value = bool(value)
            else:
                lo, hi = _RANGES[name]
                value = type(DEFAULTS[name])(max(lo, min(hi, value)))
                if name == "bitrate" and value:
                    value = max(500, value)
            clean[name] = value
        with self._lock:
            self._v.update(clean)
