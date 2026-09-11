"""A small control window for the hpqv voice demo.

This is a shell around the same call machinery `hpqv.demo` uses on the command
line -- it does not reimplement the transport. The point of the window is the
loss slider: it changes packet drop *while the call is running*, so a live
audience hears the transport degrade and recover without hanging up.

    python -m hpqv.gui

Everything runs off the main thread except tkinter itself: the call lives in
worker threads and the UI polls shared state on an `after()` timer, because
touching widgets from a worker thread is the classic way to crash tkinter.
"""
import queue
import socket
import struct
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from hpqv import control, demo

POLL_MS = 200


class DemoWindow:
    def __init__(self, root):
        self.root = root
        root.title("hpqv — post-quantum voice demo")
        root.geometry("460x430")

        self.stats = demo.Stats()
        self.loss = demo.LossControl(0.0)
        self.stop_event = threading.Event()
        self.events = queue.Queue()        # worker -> UI messages
        self.worker = None
        self.identity_path = tk.StringVar(value="identity.json")
        self.mode = tk.StringVar(value="listen")
        self.port = tk.StringVar(value="9000")
        self.target = tk.StringVar(value="192.168.1.42:9000")

        self._build()
        self.root.after(POLL_MS, self._poll)

    # ---------------- layout ----------------

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        conn = ttk.LabelFrame(self.root, text="Connection")
        conn.pack(fill="x", **pad)
        ttk.Radiobutton(conn, text="Listen on port", variable=self.mode,
                        value="listen", command=self._sync_mode).grid(row=0, column=0, sticky="w")
        self.port_entry = ttk.Entry(conn, textvariable=self.port, width=10)
        self.port_entry.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(conn, text="Call", variable=self.mode,
                        value="call", command=self._sync_mode).grid(row=1, column=0, sticky="w")
        self.target_entry = ttk.Entry(conn, textvariable=self.target, width=22)
        self.target_entry.grid(row=1, column=1, sticky="w")

        ident = ttk.Frame(conn)
        ident.grid(row=2, column=0, columnspan=2, sticky="we", pady=(6, 0))
        ttk.Label(ident, text="Identity:").pack(side="left")
        ttk.Entry(ident, textvariable=self.identity_path, width=24).pack(side="left", padx=4)
        ttk.Button(ident, text="…", width=3, command=self._pick_identity).pack(side="left")

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Start", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Hang up", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        self.state_label = ttk.Label(self.root, text="idle", font=("", 13, "bold"))
        self.state_label.pack(anchor="w", **pad)

        loss_box = ttk.LabelFrame(self.root, text="Packet loss injection (live)")
        loss_box.pack(fill="x", **pad)
        self.loss_label = ttk.Label(loss_box, text="0 %")
        self.loss_label.pack(anchor="w", padx=6)
        self.loss_scale = ttk.Scale(loss_box, from_=0, to=50, orient="horizontal",
                                    command=self._on_loss)
        self.loss_scale.pack(fill="x", padx=6, pady=(0, 6))

        meters = ttk.LabelFrame(self.root, text="Audio level")
        meters.pack(fill="x", **pad)
        ttk.Label(meters, text="out").grid(row=0, column=0, padx=4)
        self.tx_meter = ttk.Progressbar(meters, maximum=100, length=330)
        self.tx_meter.grid(row=0, column=1, pady=2)
        ttk.Label(meters, text="in").grid(row=1, column=0, padx=4)
        self.rx_meter = ttk.Progressbar(meters, maximum=100, length=330)
        self.rx_meter.grid(row=1, column=1, pady=2)

        self.counters = ttk.Label(self.root, text="sent 0    received 0    dropped 0")
        self.counters.pack(anchor="w", **pad)
        self._sync_mode()

    def _sync_mode(self):
        listening = self.mode.get() == "listen"
        self.port_entry.state(["!disabled"] if listening else ["disabled"])
        self.target_entry.state(["disabled"] if listening else ["!disabled"])

    def _pick_identity(self):
        path = filedialog.askopenfilename(title="identity.json",
                                          filetypes=[("JSON", "*.json"), ("All", "*")])
        if path:
            self.identity_path.set(path)

    # ---------------- call control ----------------

    def _on_loss(self, value):
        pct = float(value)
        self.loss.pct = pct
        self.loss_label.config(text=f"{pct:.0f} %")

    def _args(self, identity_file):
        """The same argument object the CLI builds, so one code path serves both."""
        class A:
            pass
        a = A()
        a.identity_file = identity_file
        a.input, a.wav = "mic", None
        a.output, a.out_wav = "speaker", None
        a.drop_pct = self.loss.pct
        a.seconds = 3600          # run until Hang up
        a.jitter_depth = 3
        return a

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        # Read every tk variable HERE, on the main thread. Tk variables are Tcl
        # calls, so reading them from the worker raises "main thread is not in
        # main loop" -- the same rule as widgets, which is easy to miss.
        self._cfg = {
            "mode": self.mode.get(),
            "port": int(self.port.get()),
            "target": self.target.get(),
            "identity": self.identity_path.get(),
        }
        self.stop_event = threading.Event()
        self.stats = demo.Stats()
        self.start_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.stop_btn.state(["disabled"])
        self.start_btn.state(["!disabled"])
        self.events.put(("state", "hung up"))

    def _run(self):
        """Worker thread: set up the connection, then hand off to demo._run_call."""
        try:
            cfg = self._cfg                     # plain values, captured on the main thread
            args = self._args(cfg["identity"])
            if cfg["mode"] == "listen":
                self.events.put(("state", f"listening on {cfg['port']}…"))
                sig_pub, sig_secret, peer_pub = demo._load_identity(args.identity_file, "listener")
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("0.0.0.0", cfg["port"]))
                srv.listen(1)
                conn, addr = srv.accept()
                self.events.put(("state", f"handshake with {addr[0]}…"))
                session = control.run_responder(conn, sig_pub, sig_secret, peer_pub)
                udp = demo._open_bound_udp(cfg["port"])
                _t, payload = control.recv_control(conn)
                (peer_port,) = struct.unpack(">H", payload)
                dest = (addr[0], peer_port)
            else:
                host, port_s = cfg["target"].rsplit(":", 1)
                self.events.put(("state", f"connecting to {host}…"))
                sig_pub, sig_secret, peer_pub = demo._load_identity(args.identity_file, "caller")
                conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn.connect((host, int(port_s)))
                self.events.put(("state", "handshake…"))
                session = control.run_initiator(conn, sig_pub, sig_secret, peer_pub)
                udp = demo._open_bound_udp(0)
                control.send_control(conn, control.MSG_CALL_START,
                                     struct.pack(">H", udp.getsockname()[1]))
                dest = (host, int(port_s))

            self.events.put(("state", "connected — talk"))
            demo._run_call(conn, udp, dest, session, args,
                           stats=self.stats, loss=self.loss, stop_event=self.stop_event)
            self.events.put(("state", "call ended"))
            conn.close()
        except Exception as e:                      # surface failures in the UI, not a dead thread
            self.events.put(("state", f"error: {e}"))
        finally:
            self.events.put(("done", None))

    # ---------------- UI refresh (main thread only) ----------------

    def _poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "state":
                self.state_label.config(text=value)
            elif kind == "done":
                self.start_btn.state(["!disabled"])
                self.stop_btn.state(["disabled"])

        sent, received, dropped = self.stats.snapshot()
        self.counters.config(
            text=f"sent {sent}    received {received}    dropped {dropped}")
        tx, rx = self.stats.levels()
        self.tx_meter["value"] = tx * 100
        self.rx_meter["value"] = rx * 100
        self.root.after(POLL_MS, self._poll)


def main():
    root = tk.Tk()
    DemoWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
