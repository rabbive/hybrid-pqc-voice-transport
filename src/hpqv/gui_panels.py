"""Call, handshake, security and live-stats panels. Main thread only.

Each panel owns its widgets and exposes plain `read()` / `update()` methods so
the window shell never reaches into a panel's internals.
"""
import tkinter as tk
from tkinter import filedialog, ttk

from hpqv.gui_logic import NONE, handshake_rows, rotate_button_state, stat_tiles


class ConnectionPanel:
    """Connection, identity, input source and recording."""

    def __init__(self, parent, on_create_identity):
        self.mode = tk.StringVar(value="listen")
        self.port = tk.StringVar(value="9000")
        self.target = tk.StringVar(value="192.168.1.42:9000")
        self.identity = tk.StringVar()
        self.source = tk.StringVar(value="mic")
        self.wav = tk.StringVar()
        self.record = tk.BooleanVar()
        self.record_path = tk.StringVar()

        conn = ttk.LabelFrame(parent, text="Connection")
        conn.pack(fill="x", padx=6, pady=4)
        ttk.Radiobutton(conn, text="Listen on port", variable=self.mode, value="listen",
                        command=self._sync).grid(row=0, column=0, sticky="w")
        self.port_entry = ttk.Entry(conn, textvariable=self.port, width=10)
        self.port_entry.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(conn, text="Call", variable=self.mode, value="call",
                        command=self._sync).grid(row=1, column=0, sticky="w")
        self.target_entry = ttk.Entry(conn, textvariable=self.target, width=22)
        self.target_entry.grid(row=1, column=1, sticky="w")
        ident = ttk.Frame(conn)
        ident.grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 0))
        ttk.Label(ident, text="Identity:").pack(side="left")
        ttk.Entry(ident, textvariable=self.identity, width=22).pack(side="left", padx=4)
        ttk.Button(ident, text="…", width=3, command=self._pick_identity).pack(side="left")
        ttk.Button(ident, text="Create identity", command=on_create_identity).pack(side="left", padx=4)
        ttk.Label(conn, text="Create once, then privately copy the identity file to your other computer.",
                  wraplength=480).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)

        media = ttk.LabelFrame(parent, text="Audio")
        media.pack(fill="x", padx=6, pady=4)
        ttk.Radiobutton(media, text="Microphone", variable=self.source, value="mic",
                        command=self._sync).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(media, text="WAV file", variable=self.source, value="file",
                        command=self._sync).grid(row=1, column=0, sticky="w")
        self.wav_entry = ttk.Entry(media, textvariable=self.wav, width=26)
        self.wav_entry.grid(row=1, column=1, sticky="w")
        self.wav_pick = ttk.Button(media, text="…", width=3, command=self._pick_wav)
        self.wav_pick.grid(row=1, column=2, padx=4)
        ttk.Label(media, text="48 kHz mono 16-bit PCM; loops until you hang up.").grid(
            row=2, column=0, columnspan=3, sticky="w")
        self.record_check = ttk.Checkbutton(media, text="Record received audio",
                                            variable=self.record, command=self._on_record)
        self.record_check.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(media, textvariable=self.record_path).grid(row=3, column=1, columnspan=2, sticky="w")
        self._sync()

    def _sync(self):
        listening = self.mode.get() == "listen"
        self.port_entry.state(["!disabled"] if listening else ["disabled"])
        self.target_entry.state(["disabled"] if listening else ["!disabled"])
        file_in = self.source.get() == "file"
        for w in (self.wav_entry, self.wav_pick):
            w.state(["!disabled"] if file_in else ["disabled"])

    def _pick_identity(self):
        path = filedialog.askopenfilename(title="identity.json",
                                          filetypes=[("JSON", "*.json"), ("All", "*")])
        if path:
            self.identity.set(path)

    def _pick_wav(self):
        path = filedialog.askopenfilename(title="Input WAV", filetypes=[("WAV", "*.wav"), ("All", "*")])
        if path:
            self.wav.set(path)

    def _on_record(self):
        if not self.record.get():
            self.record_path.set("")
            return
        path = filedialog.asksaveasfilename(title="Record received audio to", initialfile="received.wav",
                                            defaultextension=".wav", filetypes=[("WAV", "*.wav")])
        self.record_path.set(path)
        self.record.set(bool(path))

    def set_locked(self, locked):
        """Everything here is read once at Start, so freeze it while a call runs."""
        state = ["disabled"] if locked else ["!disabled"]
        self.record_check.state(state)
        if locked:
            for w in (self.port_entry, self.target_entry, self.wav_entry, self.wav_pick):
                w.state(["disabled"])
        else:
            self._sync()

    def read(self) -> dict:
        """Plain values for the worker; raises ValueError for a bad port or missing WAV."""
        cfg = {"mode": self.mode.get(), "port": int(self.port.get()), "target": self.target.get(),
               "identity": self.identity.get(), "input": self.source.get(),
               "wav": self.wav.get() or None,
               "record_wav": (self.record_path.get() or None) if self.record.get() else None}
        if cfg["input"] == "file" and not cfg["wav"]:
            raise ValueError("Choose a WAV file, or switch the input to Microphone.")
        return cfg


class _Grid:
    """Label/value pairs in a grid; `values[label]` is the value Label."""

    def __init__(self, parent, labels, columns=1):
        self.values = {}
        for i, label in enumerate(labels):
            r, c = divmod(i, columns)
            ttk.Label(parent, text=label).grid(row=r, column=2 * c, sticky="w", padx=(6, 4))
            self.values[label] = ttk.Label(parent, text=NONE, style="Value.TLabel")
            self.values[label].grid(row=r, column=2 * c + 1, sticky="w", padx=(0, 14))

    def show(self, pairs):
        for label, text in pairs:
            self.values[label].config(text=text)


class HandshakePanel(_Grid):
    def __init__(self, parent, info):
        self.info = info
        box = ttk.LabelFrame(parent, text="Handshake")
        box.pack(fill="x", padx=6, pady=4)
        empty = {"handshake_ms": 0.0, "max_datagram": 0}
        super().__init__(box, [label for label, _ in handshake_rows(info, empty)])
        self.update(empty)

    def update(self, stats):
        self.show(handshake_rows(self.info, stats))


class SecurityPanel:
    def __init__(self, parent, on_rotate):
        box = ttk.LabelFrame(parent, text="Key rotation")
        box.pack(fill="x", padx=6, pady=4)
        self.button = ttk.Button(box, text="Rotate keys", command=on_rotate, state="disabled")
        self.button.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.gen = ttk.Label(box, text=NONE, style="Value.TLabel")
        self.fingerprint = ttk.Label(box, text=NONE, style="Mono.TLabel")
        ttk.Label(box, text="Key generation").grid(row=1, column=0, sticky="w", padx=6)
        self.gen.grid(row=1, column=1, sticky="w")
        ttk.Label(box, text="Fingerprint").grid(row=2, column=0, sticky="w", padx=6)
        self.fingerprint.grid(row=2, column=1, sticky="w")
        ttk.Label(box, text="Both sides re-key mid-call; the audio never stops.",
                  wraplength=480).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=4)

    def update(self, live, generation, fingerprint, ready=True):
        enabled, text = rotate_button_state(live, ready)
        self.button.config(text=text)
        self.button.state(["!disabled"] if enabled else ["disabled"])
        self.gen.config(text=str(generation) if live else NONE)
        self.fingerprint.config(text=fingerprint if live and fingerprint else NONE)


class StatsPanel(_Grid):
    def __init__(self, parent):
        box = ttk.LabelFrame(parent, text="Live stats")
        box.pack(fill="x", padx=6, pady=4)
        labels = [label for label, _ in stat_tiles(_ZERO, None)]
        super().__init__(box, labels, columns=2)
        meters = ttk.Frame(box)
        meters.grid(row=len(labels), column=0, columnspan=4, sticky="we", pady=4)
        self.tx = self._meter(meters, "out", 0)
        self.rx = self._meter(meters, "in", 1)

    @staticmethod
    def _meter(parent, text, row):
        ttk.Label(parent, text=text, width=4).grid(row=row, column=0)
        bar = ttk.Progressbar(parent, maximum=100, length=300)
        bar.grid(row=row, column=1, pady=1, sticky="we")
        parent.columnconfigure(1, weight=1)
        return bar

    def update(self, stats, row, levels):
        self.show(stat_tiles(stats, row))
        self.tx["value"], self.rx["value"] = levels[0] * 100, levels[1] * 100


_ZERO = dict(sent=0, received=0, dropped=0, incoming_dropped=0, late=0, recovered=0, concealed=0)
