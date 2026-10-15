"""DemoWindow behaviour without a display: widgets are mocks, the logic is real.

tests/test_gui_window.py covers the real Tk build and skips without a display;
these cover the call lifecycle, poll loop, close handler and export so the
demo-safety fixes are exercised on every machine.
"""
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hpqv import gui
from hpqv.gui import DemoWindow
from hpqv.gui_logic import CallClock
from hpqv.gui_panels import SecurityPanel


class FakeHandle:
    def __init__(self, ready=True, gen=1):
        self.hung = 0
        self.ready, self.gen = ready, gen
        self._peer_ended = False

    def hang_up(self):
        self.hung += 1

    def rotation_ready(self):
        return self.ready

    def key_generation(self):
        return self.gen

    def key_fingerprint(self):
        return "ab:cd"

    def rotate_keys(self):
        self.gen += 1
        return self.gen


def make_window(clock=None):
    """A DemoWindow with every widget mocked, built without Tk."""
    now = [100.0]
    win = object.__new__(DemoWindow)
    win.root = MagicMock()
    win._clock = clock or (lambda: now[0])
    win._now = now
    win._cc = CallClock(win._clock)
    win.stats = MagicMock()
    win.stats.as_dict.return_value = {}
    win.stats.levels.return_value = (0, 0)
    win.controls = MagicMock()
    win.stop_event = threading.Event()
    win.call_handle = FakeHandle()
    win.telemetry = None
    win.events = queue.Queue()
    win.worker = None
    win._live = win._hung_up = win._closing = False
    win._last_sample = 0.0
    win._export_thread = None
    for name in ("state_label", "start_btn", "stop_btn", "export_btn", "export_label", "conn",
                 "stats_panel", "handshake_panel", "security", "graph"):
        setattr(win, name, MagicMock())
    return win


def text_of(label):
    return label.config.call_args.kwargs["text"]


# ---------------- 2: the poll loop survives anything ----------------

def test_poll_reschedules_and_shows_the_error_when_a_panel_raises():
    win = make_window()
    win.stats_panel.update.side_effect = RuntimeError("boom")
    win._poll()
    win.root.after.assert_called_once_with(gui.POLL_MS, win._poll)
    assert "boom" in text_of(win.state_label)


def test_poll_keeps_going_after_an_error_and_recovers():
    win = make_window()
    win.stats_panel.update.side_effect = [RuntimeError("boom"), None]
    win._poll()
    win._poll()
    assert win.root.after.call_count == 2


def test_a_failing_final_sample_still_unlocks_the_start_button():
    win = make_window()
    win.telemetry = MagicMock()
    win.telemetry.sample.side_effect = RuntimeError("bad sample")
    win._live = True
    win.events.put(("done", None))
    win._poll()
    win.start_btn.state.assert_called_with(["!disabled"])
    win.conn.set_locked.assert_called_with(False)
    assert not win._live and win.root.after.called


# ---------------- 3: closing the window mid-call ----------------

def test_close_hangs_up_then_joins_then_destroys():
    win = make_window()
    order = []
    win.call_handle.hang_up = lambda: order.append("hang_up")
    win.worker = MagicMock()
    win.worker.is_alive.return_value = True
    win.worker.join.side_effect = lambda timeout=None: order.append(("join", timeout))
    win.root.destroy.side_effect = lambda: order.append("destroy")
    win._on_close()
    assert order[0] == "hang_up" and order[-1] == "destroy"
    assert win.stop_event.is_set() and win._hung_up
    (_, timeout), = [o for o in order if isinstance(o, tuple)]
    assert 0 < timeout <= 5


def test_close_with_no_call_just_destroys_and_stops_polling():
    win = make_window()
    win._on_close()
    win.root.destroy.assert_called_once()
    win._poll()
    win.root.after.assert_not_called()          # no reschedule on a destroyed root


def test_close_destroys_even_if_hang_up_raises():
    win = make_window()
    win.worker = MagicMock()
    win.worker.is_alive.return_value = True
    win.call_handle.hang_up = MagicMock(side_effect=OSError("socket gone"))
    win._on_close()
    win.root.destroy.assert_called_once()


# ---------------- 1: WAV is validated before connecting ----------------

def test_start_rejects_a_bad_wav_before_connecting(monkeypatch):
    win = make_window()
    win.conn.read.return_value = {"input": "file", "wav": "/x/16k.wav", "mode": "listen"}
    errors, threads = [], []
    monkeypatch.setattr(gui, "validate_wav", lambda p: "WAV must be 48 kHz mono 16-bit")
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(gui.threading, "Thread", lambda *a, **k: threads.append(1))
    win._start()
    assert errors and "48 kHz" in errors[0][1]
    assert win.worker is None and not threads
    win.start_btn.state.assert_not_called()      # Start stays enabled


def test_start_skips_wav_validation_for_the_microphone(monkeypatch):
    win = make_window()
    win.conn.read.return_value = {"input": "mic", "wav": None, "mode": "listen"}
    monkeypatch.setattr(gui, "validate_wav", lambda p: pytest.fail("mic needs no WAV"))
    monkeypatch.setattr(gui.threading, "Thread", lambda *a, **k: MagicMock())
    win._start()
    assert win.worker is not None


def test_mic_calls_run_until_hang_up_not_an_hour():
    win = make_window()
    win.controls = MagicMock(get=lambda k: 0)
    args = win._args({"identity": "i", "input": "mic", "wav": None, "record_wav": None})
    assert args.seconds >= 10 ** 6


def test_wav_calls_loop_until_hang_up():
    win = make_window()
    win.controls = MagicMock(get=lambda k: 0)
    wav = win._args({"identity": "i", "input": "file", "wav": "a.wav", "record_wav": None})
    mic = win._args({"identity": "i", "input": "mic", "wav": None, "record_wav": None})
    assert wav.loop is True and mic.loop is False


# ---------------- 4: the graph stops scrolling after hang-up ----------------

def test_graph_time_freezes_when_the_call_ends_and_restarts_on_next_connect():
    win = make_window()
    win.telemetry = MagicMock(rows=[], events=[])
    win.events.put(("connected", None))
    win._poll()
    win._now[0] += 30
    win.events.put(("done", None))
    win._poll()
    win._now[0] += 500                          # minutes later, still showing the finished call
    win._poll()
    assert win.graph.redraw.call_args.args[2] == pytest.approx(30)


# ---------------- 6: export runs off the Tk thread ----------------

def test_export_runs_in_a_worker_on_a_copy_and_posts_the_result(monkeypatch):
    win = make_window()
    seen = {}
    gate = threading.Event()

    class Tel:
        rows, events = [{"t_s": 1.0}], []

        def export(self, out_dir):
            seen["thread"] = threading.current_thread()
            seen["rows"] = list(self.rows)
            gate.wait(2)
            return [out_dir + "/timeline.csv"]

    win.telemetry = live = Tel()
    live.rows = [{"t_s": 1.0}]
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **k: "/out")
    win._export()
    live.rows.append({"t_s": 2.0})              # a sample lands while exporting
    assert win._export_thread.is_alive()        # _export returned without waiting
    win.export_btn.state.assert_called_with(["disabled"])
    gate.set()
    win._export_thread.join(2)
    assert seen["thread"] is not threading.main_thread()
    assert seen["rows"] == [{"t_s": 1.0}]       # the copy, not the live rows
    win._poll()
    assert "/out/timeline.csv" in text_of(win.export_label)
    win.export_btn.state.assert_called_with(["!disabled"])


def test_export_failure_is_shown_and_does_not_kill_the_loop(monkeypatch):
    win = make_window()
    errors = []

    class Tel:
        rows, events = [{"t_s": 1.0}], []

        def export(self, out_dir):
            raise RuntimeError("no matplotlib")

    win.telemetry = Tel()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **k: "/out")
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    win._export()
    win._export_thread.join(2)
    win._poll()
    assert errors and "no matplotlib" in errors[0][1]
    win.export_btn.state.assert_called_with(["!disabled"])
    assert win.root.after.called


# ---------------- 8: rotate waits for the old key to retire ----------------

def test_poll_gates_the_rotate_button_on_rotation_ready():
    win = make_window()
    win.call_handle = FakeHandle(ready=False)
    win.telemetry = MagicMock(rows=[], events=[])
    win._live = True
    win._poll()
    assert win.security.update.call_args.args[:1] == (True,)
    assert win.security.update.call_args.kwargs["ready"] is False
    win.call_handle.ready = True
    win._poll()
    assert win.security.update.call_args.kwargs["ready"] is True


def test_handle_without_rotation_ready_counts_as_ready():
    win = make_window()
    win.call_handle = SimpleNamespace(key_generation=lambda: 1, key_fingerprint=lambda: "x")
    assert win._rotation_ready() is True


def test_rotate_click_is_ignored_while_the_old_key_retires():
    win = make_window()
    win.call_handle = FakeHandle(ready=False)
    win._rotate()
    assert win.call_handle.gen == 1
    assert "retiring" in text_of(win.state_label)


def test_security_panel_disables_and_relabels_the_button():
    panel = object.__new__(SecurityPanel)
    panel.button, panel.gen, panel.fingerprint = MagicMock(), MagicMock(), MagicMock()
    panel.update(True, 2, "ab", ready=False)
    panel.button.state.assert_called_with(["disabled"])
    assert "retiring" in panel.button.config.call_args.kwargs["text"].lower()
    panel.update(True, 2, "ab", ready=True)
    panel.button.state.assert_called_with(["!disabled"])
    assert panel.button.config.call_args.kwargs["text"] == "Rotate keys"


# ---------------- 7: "ended by peer" only when the peer ended it ----------------

@pytest.mark.parametrize("hung, peer, want", [(True, False, "hung up"), (False, True, "call ended by peer"),
                                              (False, False, "call ended")])
def test_final_state_text(hung, peer, want):
    win = make_window()
    win.call_handle._peer_ended = peer
    win._hung_up = hung
    assert win._end_text() == want
