"""Live-demo window for the hpqv voice call.

A shell around the same call machinery `hpqv.demo` uses on the command line --
it does not reimplement the transport. Every control changes the running call
(via CallControls / CallHandle) and every effect shows up in the live stats and
the scrolling graph, so an audience sees cause and effect without hanging up.

    python -m hpqv.gui

Layout: left column is a notebook (Call / Network / Security) for the controls,
right column (always visible) holds live stats and the graph.

Everything runs off the main thread except tkinter itself: the call lives in
worker threads and the UI polls shared state on an `after()` timer, because
touching widgets (or Tk variables) from a worker thread is the classic way to
crash tkinter. Telemetry is sampled from that same timer, once a second.
"""
import os
import queue
import socket
import struct
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from hpqv import control, demo
from hpqv.callctl import CallHandle
from hpqv.controls import CallControls
from hpqv.gui_graph import GraphCanvas, SERIES
from hpqv.gui_logic import (CallClock, end_text, snapshot_for_export, validate_wav)
from hpqv.gui_network import NetworkPanel
from hpqv.gui_panels import (ConnectionPanel, HandshakePanel, SecurityPanel,
                             StatsPanel)
from hpqv.secure import HandshakeError
from hpqv.telemetry import Telemetry

POLL_MS = 200
SAMPLE_S = 1.0           # telemetry cadence
ACCEPT_TICK = 0.5        # how often a listening worker checks for Hang up
MIC_SECONDS = 10 ** 7    # a mic call runs until Hang up; demo's default would end it
CLOSE_JOIN_S = 3.0       # how long closing the window waits for the call to tear down


class DemoWindow:
    def __init__(self, root, clock=time.monotonic):
        self.root = root
        self._clock = clock
        root.title("hpqv — post-quantum voice demo")
        root.geometry("1280x700")
        root.minsize(1100, 640)
        self._style()

        self.stats = demo.Stats()
        self.controls = CallControls()
        self.stop_event = threading.Event()
        self.call_handle = CallHandle()
        self.telemetry = None
        self.events = queue.Queue()        # worker -> UI messages
        self.worker = None
        self._live = False                 # call connected: sample telemetry, allow rotate
        self._hung_up = False
        self._closing = False
        self._cc = CallClock(clock)        # graph time; freezes at hang-up
        self._last_sample = 0.0
        self._export_thread = None
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_MS, self._poll)

    def _style(self):
        base = tkfont.nametofont("TkDefaultFont")
        base.configure(size=12)            # readable from the back of a room
        tkfont.nametofont("TkFixedFont").configure(size=12)
        style = ttk.Style()
        style.configure("Value.TLabel", font=base.copy().actual() | {"weight": "bold"})
        style.configure("Mono.TLabel", font=tkfont.nametofont("TkFixedFont"))
        style.configure("State.TLabel", font=base.copy().actual() | {"weight": "bold", "size": 14})

    # ---------------- layout ----------------

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=6)
        self.start_btn = ttk.Button(top, text="Start", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(top, text="Hang up", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.export_btn = ttk.Button(top, text="Export session…", command=self._export)
        self.export_btn.pack(side="left", padx=6)
        self.state_label = ttk.Label(top, text="idle", style="State.TLabel")
        self.state_label.pack(side="left", padx=14)

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.tabs = left = ttk.Notebook(body)
        left.pack(side="left", fill="y", padx=(8, 4), pady=2)
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(4, 8))

        call_tab, net_tab, sec_tab = (ttk.Frame(left) for _ in range(3))
        left.add(call_tab, text="Call")
        left.add(net_tab, text="Network")
        left.add(sec_tab, text="Security")
        self.conn = ConnectionPanel(call_tab, self._create_identity)
        self.info = demo.handshake_info()
        self.handshake_panel = HandshakePanel(call_tab, self.info)
        self.network = NetworkPanel(net_tab, self.controls)
        self.security = SecurityPanel(sec_tab, self._rotate, self.info)

        self.stats_panel = StatsPanel(right)
        graph_box = ttk.LabelFrame(right, text="Last 60 s")
        graph_box.pack(fill="both", expand=True, padx=6, pady=4)
        legend = ttk.Frame(graph_box)
        legend.pack(fill="x")
        for s in SERIES:
            tk.Label(legend, text="━ " + s.name, fg=s.color).pack(side="left", padx=8)
        self.graph = GraphCanvas(graph_box, height=260)
        self.graph.pack(fill="both", expand=True, padx=4, pady=4)
        self.export_label = ttk.Label(right, text="", wraplength=640, justify="left")
        self.export_label.pack(fill="x", padx=6, pady=(0, 4))

    # ---------------- identity ----------------

    def _create_identity(self):
        """Writes NAME.key (0600) and NAME.crt next to each other and shows the fingerprint.

        Key generation takes milliseconds, so it stays on the main thread; the
        handshake, which can take seconds, never does.
        """
        path = filedialog.asksaveasfilename(
            title="Name your identity (writes NAME.key and NAME.crt)", initialfile="me")
        if not path:
            return
        base = path[:-4] if path.lower().endswith((".key", ".crt")) else path
        key_path, cert_path = base + ".key", base + ".crt"
        try:
            fingerprint = demo.create_identity(os.path.basename(base), key_path, cert_path)
        except FileExistsError:
            messagebox.showerror("Identity already exists",
                                 "Choose a new name to keep your existing keys.")
        except Exception as exc:
            messagebox.showerror("Could not create identity", str(exc))
        else:
            self.conn.key.set(key_path)
            self.conn.cert.set(cert_path)
            self.conn.show_fingerprint(fingerprint)
            self.state_label.config(
                text="identity saved; send only the .crt to the other person")

    # ---------------- call control ----------------

    def _args(self, cfg):
        """The same argument object the CLI builds, so one code path serves both."""
        return demo.call_args(
            key=cfg["key"], cert=cfg["cert"], peer_cert=cfg["peer_cert"],
            input=cfg["input"], wav=cfg["wav"],
            record_wav=cfg["record_wav"], drop_pct=self.controls.get("loss_pct"),
            seconds=MIC_SECONDS,                 # run until Hang up
            loop=cfg["input"] == "file",         # a WAV restarts at EOF, so Hang up ends the call
            jitter_depth=self.controls.get("jitter_depth"))

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        # Read every tk variable HERE, on the main thread. Tk variables are Tcl
        # calls, so reading them from the worker raises "main thread is not in
        # main loop" -- the same rule as widgets, which is easy to miss.
        try:
            cfg = self.conn.read()
        except ValueError as exc:
            messagebox.showerror("Cannot start", str(exc))
            return
        if cfg["input"] == "file":         # fail here, not as a worker error after connecting
            err = validate_wav(cfg["wav"])
            if err:
                messagebox.showerror("Cannot start", err)
                return
        cfg.update(stop=threading.Event(), stats=demo.Stats(), handle=CallHandle())
        self.stop_event, self.stats, self.call_handle = cfg["stop"], cfg["stats"], cfg["handle"]
        self._hung_up = False
        self.start_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        self.conn.set_locked(True)
        self.worker = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self.worker.start()

    def _stop(self):
        """Hang up through the handle so the peer's call ends too."""
        self._hung_up = True
        self.stop_btn.state(["disabled"])
        self.call_handle.hang_up()
        self.stop_event.set()              # also frees a worker still waiting in accept()
        self.state_label.config(text="hanging up…")

    def _on_close(self):
        """Window X: hang up first so the peer gets CALL_END, then tear down."""
        self._closing = True               # _poll must not reschedule on a dead root
        try:
            if self.worker and self.worker.is_alive():
                self._hung_up = True
                try:
                    self.call_handle.hang_up()
                finally:
                    self.stop_event.set()   # even if the socket is dead, free the worker
                self.worker.join(timeout=CLOSE_JOIN_S)
        except Exception:                   # nothing may keep the window from closing
            pass
        finally:
            self.root.destroy()

    def _rotation_ready(self):
        # Older CallHandles have no rotation_ready(): treat them as always ready.
        return getattr(self.call_handle, "rotation_ready", lambda: True)()

    def _end_text(self):
        h = self.call_handle
        return end_text(self._hung_up, h.peer_ended, h.connection_lost, h.protocol_error)

    def _rotate(self):
        if not self._rotation_ready():
            self.state_label.config(text="old key retiring…")
            return
        try:
            gen = self.call_handle.rotate_keys()
        except RuntimeError as exc:
            self.state_label.config(text=str(exc))
        else:
            self.state_label.config(text=f"keys rotated → generation {gen}")

    def _accept(self, srv, stop):
        """accept() that notices Hang up: a plain accept() would block forever."""
        srv.settimeout(ACCEPT_TICK)
        while not stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(None)
            return conn, addr
        return None, None

    def _run(self, cfg):
        """Worker thread: set up the connection, then hand off to demo._run_call.

        Every blocking step (accept, TLS handshake, waiting for CALL_START) is
        bounded and runs here, never on the Tk thread.
        """
        srv = conn = udp = channel = None
        stats, stop, handle = cfg["stats"], cfg["stop"], cfg["handle"]
        put = self.events.put
        try:
            args = self._args(cfg)
            ident, peer_cert = demo._load_identity(args.key, args.cert, args.peer_cert)
            if cfg["mode"] == "listen":
                put(("state", f"listening on {cfg['port']}…"))
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(("0.0.0.0", cfg["port"]))
                srv.listen(1)
                conn, addr = self._accept(srv, stop)
                if conn is None:
                    put(("state", "hung up"))
                    return
                put(("state", f"handshake with {addr[0]}…"))
                channel = demo.establish_timed("server", conn, ident, peer_cert, stats)
                udp = demo._open_bound_udp(cfg["port"])
                put(("state", "waiting for the caller to start the call…"))
                dest = demo.wait_call_start(channel, addr[0], stop_event=stop)
            else:
                host, port_s = cfg["target"].rsplit(":", 1)
                put(("state", f"connecting to {host}…"))
                conn = socket.create_connection((host, int(port_s)), timeout=demo.CONNECT_TIMEOUT)
                put(("state", "handshake…"))
                channel = demo.establish_timed("client", conn, ident, peer_cert, stats)
                udp = demo._open_bound_udp(0)
                channel.send(control.MSG_CALL_START,
                             struct.pack(">H", udp.getsockname()[1]))
                dest = (host, int(port_s))

            put(("connected", None))
            put(("state", "connected — talk"))
            demo._run_call(channel, udp, dest, args, stats=stats, controls=self.controls,
                           stop_event=stop, call_handle=handle)
            put(("state", self._end_text()))
        except HandshakeError as e:
            put(("state", "handshake failed: " + demo.handshake_failure_text(e.reason)))
        except demo.CallSetupError as e:
            put(("state", "hung up" if stop.is_set() else f"call not started: {e}"))
        except Exception as e:                      # surface failures in the UI, not a dead thread
            put(("state", f"error: {e}"))
        finally:
            for s in (channel, conn, srv, udp):
                if s is not None:
                    s.close()
            put(("done", None))

    # ---------------- export ----------------

    def _export(self):
        if self._export_thread and self._export_thread.is_alive():
            return
        if not self.telemetry or not self.telemetry.rows:
            messagebox.showinfo("Nothing to export", "Telemetry starts once a call is connected "
                                "and takes one sample per second.")
            return
        out_dir = filedialog.askdirectory(title="Export session to folder")
        if not out_dir:
            return
        # Copy on this thread; the PNG charts take seconds, so render off it.
        tel = snapshot_for_export(self.telemetry)
        self.export_btn.state(["disabled"])
        self.export_label.config(text="Exporting…")

        def work():
            try:
                self.events.put(("exported", tel.export(out_dir)))
            except Exception as exc:        # e.g. matplotlib missing: say so, keep the demo alive
                self.events.put(("export_failed", str(exc)))

        self._export_thread = threading.Thread(target=work, daemon=True)
        self._export_thread.start()

    # ---------------- UI refresh (main thread only) ----------------

    def _sample(self):
        self.telemetry.sample(self._cc.now())
        self._last_sample = self._clock()

    def _drain(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "state":
                self.state_label.config(text=value)
            elif kind == "connected":
                self._live = True
                self._cc.start()
                self._last_sample = self._clock()
                self.telemetry = Telemetry(self.stats, self.controls)
            elif kind == "exported":
                self.export_btn.state(["!disabled"])
                self.export_label.config(text="Exported:\n" + "\n".join(value))
            elif kind == "export_failed":
                self.export_btn.state(["!disabled"])
                self.export_label.config(text="")
                messagebox.showerror("Export failed", value)
            elif kind == "done":
                try:
                    if self._live:
                        self._sample()      # close the last partial interval for export
                finally:                    # never leave Start locked, whatever the sample did
                    self._live = False
                    self._cc.freeze()       # keep the finished call on screen
                    self.start_btn.state(["!disabled"])
                    self.stop_btn.state(["disabled"])
                    self.conn.set_locked(False)

    def _refresh(self):
        # Sampling is "about once a second": the 200 ms poll lands it between 1.0 and
        # 1.2 s. Telemetry records the real time of each sample, nothing assumes 1 s.
        if self._live and self._clock() - self._last_sample >= SAMPLE_S:
            self._sample()
        rows = self.telemetry.rows if self.telemetry else []
        stats = self.stats.as_dict()
        self.stats_panel.update(stats, rows[-1] if rows else None, self.stats.levels())
        self.handshake_panel.update(stats)
        self.security.update(self._live, self.call_handle.key_generation(), stats,
                             ready=self._rotation_ready())
        if rows or self.telemetry:
            self.graph.redraw(rows, self.telemetry.events if self.telemetry else [],
                              self._cc.now())

    def _poll(self):
        """Never lets an exception end the loop: a dead poll freezes the stats mid-demo."""
        try:
            self._drain()
            self._refresh()
        except Exception as exc:
            self.state_label.config(text=f"UI error: {exc}")
        finally:
            if not self._closing:
                self.root.after(POLL_MS, self._poll)


def main():
    root = tk.Tk()
    DemoWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
