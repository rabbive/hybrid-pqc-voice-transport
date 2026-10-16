"""Network tab: presets, repair mode, impairment sliders.

Controls are the single source of truth. Sliders and radios write to them;
`refresh()` pushes controls back into every widget, which is how presets move
the sliders. Main thread only (it touches Tk).
"""
import tkinter as tk
from tkinter import ttk

from hpqv.controls import CallControls
from hpqv.gui_logic import PRESETS, SLIDERS, preset_values

REPAIR_MODES = [("Silence", "silence"), ("Concealment only", "plc"), ("FEC + concealment", "fec")]


class SliderRow:
    def __init__(self, parent, row, spec, controls, syncing):
        self.spec, self.controls, self._syncing = spec, controls, syncing
        self.var = tk.DoubleVar(value=spec.to_slider(controls.get(spec.knob)))
        ttk.Label(parent, text=spec.label).grid(row=row, column=0, sticky="w", padx=(6, 8))
        self.scale = ttk.Scale(parent, from_=spec.lo, to=spec.hi, orient="horizontal",
                               variable=self.var, command=self.on_move)
        self.scale.grid(row=row, column=1, sticky="we", pady=2)
        self.value_label = ttk.Label(parent, width=18, anchor="w")
        self.value_label.grid(row=row, column=2, padx=6)
        parent.columnconfigure(1, weight=1)
        self.refresh()

    def on_move(self, raw):
        if self._syncing():                # a programmatic refresh, not the user
            return
        value = self.spec.to_knob(float(raw))
        self.controls.set(**{self.spec.knob: value})
        self.value_label.config(text=self.spec.fmt(self.controls.get(self.spec.knob)))

    def refresh(self):
        value = self.controls.get(self.spec.knob)
        self.var.set(self.spec.to_slider(value))
        self.value_label.config(text=self.spec.fmt(value))

    def enable(self, on):
        self.scale.state(["!disabled"] if on else ["disabled"])


class NetworkPanel:
    def __init__(self, parent, controls: CallControls):
        self.controls = controls
        self._busy = False
        self.repair = tk.StringVar()
        self.fec = tk.BooleanVar()
        self.model = tk.StringVar()

        presets = ttk.LabelFrame(parent, text="Presets (network only — repair, FEC and bitrate stay as set)")
        presets.pack(fill="x", padx=6, pady=4)
        for name in PRESETS:
            ttk.Button(presets, text=name, command=lambda n=name: self.apply_preset(n)).pack(
                side="left", padx=4, pady=4)

        repair = ttk.LabelFrame(parent, text="Repair of lost frames (receiver)")
        repair.pack(fill="x", padx=6, pady=4)
        for text, value in REPAIR_MODES:
            ttk.Radiobutton(repair, text=text, value=value, variable=self.repair,
                            command=self.on_repair).pack(side="left", padx=6)

        net = ttk.LabelFrame(parent, text="Network impairment (sender / receiver, live)")
        net.pack(fill="x", padx=6, pady=4)
        self.sliders = {}
        rows = {s.knob: s for s in SLIDERS}
        self._add(net, 0, rows["loss_pct"])
        model = ttk.Frame(net)
        model.grid(row=1, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Label(model, text="Loss model").pack(side="left", padx=(0, 10))
        for text, value in (("Random", "random"), ("Burst", "burst")):
            ttk.Radiobutton(model, text=text, value=value, variable=self.model,
                            command=self.on_model).pack(side="left", padx=4)
        # Sender-side switch: it changes the audio the PEER receives, so it lives with
        # the outgoing-loss controls, not in the receiver's repair box.
        ttk.Checkbutton(model, text="Encoder FEC (in audio the peer receives)",
                        variable=self.fec, command=self.on_fec).pack(side="left", padx=(24, 0))
        for i, knob in enumerate(("burst_len", "delay_ms", "jitter_ms", "incoming_loss_pct"), 2):
            self._add(net, i, rows[knob])

        buf = ttk.LabelFrame(parent, text="Receiver buffer and encoder")
        buf.pack(fill="x", padx=6, pady=4)
        self._add(buf, 0, rows["jitter_depth"])
        self._add(buf, 1, rows["bitrate"])
        self.refresh()

    def _add(self, parent, row, spec):
        self.sliders[spec.knob] = SliderRow(parent, row, spec, self.controls, lambda: self._busy)

    def on_repair(self):
        self.controls.set(repair=self.repair.get())

    def on_fec(self):
        self.controls.set(fec=self.fec.get())

    def on_model(self):
        self.controls.set(loss_model=self.model.get())
        self.refresh()

    def apply_preset(self, name):
        self.controls.set(**preset_values(name))
        self.refresh()

    def refresh(self):
        """Controls -> widgets."""
        self._busy = True                  # Scale commands fire on set(); don't echo them back
        try:
            snap = self.controls.snapshot()
            self.repair.set(snap["repair"])
            self.fec.set(snap["fec"])
            self.model.set(snap["loss_model"])
            for s in self.sliders.values():
                s.refresh()
            self.sliders["burst_len"].enable(snap["loss_model"] == "burst")
        finally:
            self._busy = False
