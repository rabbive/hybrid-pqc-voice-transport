"""Window smoke tests: build the real DemoWindow, no network, no audio."""
import os
import subprocess
import sys
import tkinter as tk

import pytest

from hpqv.gui import DemoWindow


def _tk_available():
    # Tk without a window server can abort the whole process (macOS) instead of
    # raising TclError, so probe in a child before touching it here. Agents and
    # CI run without a terminal; there the probe's abort still files a macOS
    # crash report and pops a "Python quit unexpectedly" dialog, so only probe
    # from an interactive terminal or when HPQV_GUI_TESTS=1 opts in.
    if not (sys.stdin.isatty() or os.environ.get("HPQV_GUI_TESTS") == "1"):
        return False
    try:
        probe = subprocess.run([sys.executable, "-c", "import tkinter; tkinter.Tk().destroy()"],
                               capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return probe.returncode == 0


@pytest.fixture(scope="module")
def tk_ok():
    if not _tk_available():
        pytest.skip("no display")


@pytest.fixture
def root(tk_ok):
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def test_window_builds_and_destroys(root):
    win = DemoWindow(root)
    root.update_idletasks()
    assert win.start_btn.instate(["!disabled"]) and win.stop_btn.instate(["disabled"])


def test_preset_moves_sliders_and_controls_then_poll_samples_telemetry(root):
    now = [100.0]
    win = DemoWindow(root, clock=lambda: now[0])

    win.network.apply_preset("Congested")
    snap = win.controls.snapshot()
    assert (snap["loss_pct"], snap["delay_ms"], snap["jitter_ms"], snap["jitter_depth"]) == (20, 150, 60, 6)
    sliders = win.network.sliders
    assert sliders["loss_pct"].var.get() == 20 and sliders["jitter_depth"].var.get() == 6
    assert "120 ms" in sliders["jitter_depth"].value_label.cget("text")

    win.events.put(("connected", None))
    win._poll()                                    # call starts: telemetry baseline at t=0
    s = win.stats
    with s.lock:
        s.sent, s.dropped, s.received, s.recovered, s.late = 80, 20, 70, 6, 1
        s.handshake_ms, s.max_datagram, s.key_gen = 12.5, 173, 2
    now[0] += 1.2
    win._poll()

    assert len(win.telemetry.rows) == 1
    tiles = win.stats_panel.values
    assert tiles["Sent"].cget("text") == "80" and tiles["Recovered (FEC)"].cget("text") == "6"
    assert tiles["Loss you hear"].cget("text") == "30.0 %"
    assert tiles["MOS (audio you hear)"].cget("text") != "—"
    assert "12.5 ms" in win.handshake_panel.values["Handshake time"].cget("text")
    assert win.handshake_panel.values["Largest datagram"].cget("text").startswith("173 B")


def test_preset_then_slider_drag_stays_consistent(root):
    win = DemoWindow(root)
    win.network.apply_preset("Burst loss")
    assert win.network.model.get() == "burst"
    win.network.sliders["loss_pct"].on_move("33.4")
    assert win.controls.get("loss_pct") == 33
    win.network.apply_preset("Clean")
    assert win.controls.get("loss_pct") == 0 and win.network.sliders["loss_pct"].var.get() == 0
    assert win.network.model.get() == "random"


def test_repair_radio_and_fec_checkbox_write_controls(root):
    win = DemoWindow(root)
    win.network.repair.set("plc")
    win.network.on_repair()
    win.network.fec.set(False)
    win.network.on_fec()
    assert (win.controls.get("repair"), win.controls.get("fec")) == ("plc", False)


def test_hang_up_before_a_call_is_harmless_and_peer_hangup_updates_state(root):
    win = DemoWindow(root)
    win._stop()
    win.events.put(("state", "peer hung up"))
    win._poll()
    assert win.state_label.cget("text") == "peer hung up"


def test_export_after_hang_up_lists_written_paths(root, tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    now = [0.0]
    win = DemoWindow(root, clock=lambda: now[0])
    win.events.put(("connected", None))
    win._poll()
    now[0] = 1.5
    win.stats.sent = 50
    win._poll()
    win.events.put(("done", None))                 # call finished; export must still work
    win._poll()
    monkeypatch.setattr("hpqv.gui.filedialog.askdirectory", lambda **k: str(tmp_path))
    win._export()
    win._export_thread.join(30)                    # charts render off the Tk thread
    win._poll()
    assert (tmp_path / "timeline.csv").exists()
    assert "timeline.csv" in win.export_label.cget("text")


def test_close_button_is_wired_to_the_hang_up_handler(root):
    win = DemoWindow(root)
    assert root.protocol("WM_DELETE_WINDOW")       # a Tcl command is registered


def test_presets_leave_repair_fec_and_bitrate_alone(root):
    win = DemoWindow(root)
    win.controls.set(repair="plc", fec=False, bitrate=24000)
    win.network.apply_preset("Congested")
    win.network.apply_preset("Clean")
    assert (win.controls.get("repair"), win.controls.get("fec"), win.controls.get("bitrate")) == ("plc", False, 24000)
    assert win.network.repair.get() == "plc" and win.network.fec.get() is False


def test_start_validates_the_wav_choice(root, monkeypatch):
    errors = []
    monkeypatch.setattr("hpqv.gui.messagebox.showerror", lambda *a, **k: errors.append(a))
    win = DemoWindow(root)
    win.conn.source.set("file")
    win._start()
    assert errors and win.worker is None
