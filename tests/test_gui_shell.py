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
from hpqv.demo import Stats, handshake_info
from hpqv.gui_logic import security_rows
from hpqv.gui_panels import SecurityPanel


class FakeHandle:
    def __init__(self, ready=True, gen=1):
        self.hung = 0
        self.ready, self.gen = ready, gen
        self.peer_ended = self.connection_lost = self.protocol_error = False

    def hang_up(self):
        self.hung += 1

    def rotation_ready(self):
        return self.ready

    def key_generation(self):
        return self.gen

    def sas(self):
        return "abcd0123"

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
    win.stats.as_dict.return_value = {"last_datagram": b""}
    win.stats.levels.return_value = (0, 0)
    win.controls = MagicMock()
    win.stop_event = threading.Event()
    win.call_handle = FakeHandle()
    win.telemetry = None
    win.events = queue.Queue()
    win.worker = None
    win._live = win._hung_up = win._closing = win._ringing = False
    win._dialog = None
    win._answers = queue.Queue()
    win._last_sample = 0.0
    win._export_thread = None
    for name in ("state_label", "start_btn", "stop_btn", "export_btn", "export_label", "conn",
                 "stats_panel", "handshake_panel", "security", "wire", "graph"):
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
    args = win._args({"key": "k", "cert": "c", "peer_cert": "p", "input": "mic", "wav": None, "record_wav": None})
    assert args.seconds >= 10 ** 6


def test_wav_calls_loop_until_hang_up():
    win = make_window()
    win.controls = MagicMock(get=lambda k: 0)
    wav = win._args({"key": "k", "cert": "c", "peer_cert": "p", "input": "file", "wav": "a.wav", "record_wav": None})
    mic = win._args({"key": "k", "cert": "c", "peer_cert": "p", "input": "mic", "wav": None, "record_wav": None})
    assert wav.loop is True and mic.loop is False


def test_wav_hint_on_screen_says_it_loops(monkeypatch):
    from hpqv import gui_panels
    monkeypatch.setattr(gui_panels, "tk", MagicMock())
    monkeypatch.setattr(gui_panels, "ttk", MagicMock())
    gui_panels.ConnectionPanel(MagicMock(), on_create_identity=lambda: None)
    texts = [c.kwargs.get("text", "") for c in gui_panels.ttk.Label.call_args_list]
    hint = next(t for t in texts if "48 kHz" in t)
    assert "loops until you hang up" in hint and "plays once" not in hint


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
    win.call_handle = SimpleNamespace(key_generation=lambda: 1, sas=lambda: "x")
    assert win._rotation_ready() is True


def test_rotate_click_is_ignored_while_the_old_key_retires():
    win = make_window()
    win.call_handle = FakeHandle(ready=False)
    win._rotate()
    assert win.call_handle.gen == 1
    assert "retiring" in text_of(win.state_label)


def test_security_panel_disables_and_relabels_the_button():
    panel = object.__new__(SecurityPanel)
    panel.info = handshake_info()
    panel.button = MagicMock()
    panel.values = {label: MagicMock() for label, _ in security_rows(panel.info, Stats().as_dict(), False, 0)}
    stats = Stats().as_dict()
    panel.update(True, 2, stats, ready=False)
    panel.button.state.assert_called_with(["disabled"])
    assert "retiring" in panel.button.config.call_args.kwargs["text"].lower()
    panel.update(True, 2, stats, ready=True)
    panel.button.state.assert_called_with(["!disabled"])
    assert panel.button.config.call_args.kwargs["text"] == "Rotate keys"


# ---------------- 7: "ended by peer" only when the peer ended it ----------------

@pytest.mark.parametrize("hung, peer, want", [(True, False, "hung up"), (False, True, "call ended by peer"),
                                              (False, False, "call ended")])
def test_final_state_text(hung, peer, want):
    win = make_window()
    win.call_handle.peer_ended = peer
    win._hung_up = hung
    assert win._end_text() == want


def test_end_text_reads_the_handles_connection_lost_and_protocol_error_flags():
    win = make_window()
    win.call_handle.connection_lost = True
    assert win._end_text() == "connection lost"
    win.call_handle.protocol_error = True
    assert "protocol" in win._end_text()


# ---------------- worker: handshake failures are said in plain words ----------------

def _run_worker(win, monkeypatch, *, fail, mode="call"):
    import socket
    from hpqv import demo
    server = socket.create_server(("127.0.0.1", 0))
    monkeypatch.setattr(demo, "_load_identity", lambda *a: (object(), b"peer"))
    monkeypatch.setattr(demo, "establish_timed", fail)
    cfg = {"mode": mode, "port": 0, "target": "127.0.0.1:%d" % server.getsockname()[1],
           "key": "k", "cert": "c", "peer_cert": "p", "input": "mic", "wav": None,
           "record_wav": None, "stop": win.stop_event, "stats": demo.Stats(),
           "handle": win.call_handle}
    win.controls = MagicMock(get=lambda k: 0)
    try:
        t = threading.Thread(target=win._run, args=(cfg,))
        t.start()
        t.join(5)
        assert not t.is_alive()
    finally:
        server.close()
    events = []
    while not win.events.empty():
        events.append(win.events.get_nowait())
    return events


def test_worker_shows_the_plain_words_for_a_handshake_failure(monkeypatch):
    from hpqv.secure import HandshakeError

    def boom(*a, **k):
        raise HandshakeError("bad_peer_cert", "secret detail that must not be shown")

    events = _run_worker(make_window(), monkeypatch, fail=boom)
    state = [v for k, v in events if k == "state"][-1]
    assert state.startswith("handshake failed:") and "certificate" in state
    assert "bad_peer_cert" not in state and "secret detail" not in state
    assert events[-1] == ("done", None)


def test_worker_reports_an_unexpected_error_and_still_finishes(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    events = _run_worker(make_window(), monkeypatch, fail=boom)
    assert ("state", "error: kaboom") in events and events[-1] == ("done", None)


def test_identity_section_has_three_pickers_and_create_button(monkeypatch):
    from hpqv import gui_panels
    monkeypatch.setattr(gui_panels, "tk", MagicMock())
    monkeypatch.setattr(gui_panels, "ttk", MagicMock())
    gui_panels.ConnectionPanel(MagicMock(), on_create_identity=lambda: None)
    labels = [c.kwargs.get("text", "") for c in gui_panels.ttk.Label.call_args_list]
    buttons = [c.kwargs.get("text", "") for c in gui_panels.ttk.Button.call_args_list]
    for want in ("My key", "My certificate", "Peer's certificate"):
        assert want in labels
    assert "Create identity" in buttons and buttons.count("…") >= 4     # 3 identity + 1 WAV
    note = next(t for t in labels if "Never share the .key" in t)
    assert ".crt" in note


# ---------------- B2: pairing hooks ----------------

def test_identity_files_all_empty_means_friend_mode_and_partial_is_refused(monkeypatch):
    from hpqv import gui_panels
    monkeypatch.setattr(gui_panels, "tk", MagicMock())
    monkeypatch.setattr(gui_panels, "ttk", MagicMock())
    panel = gui_panels.ConnectionPanel(MagicMock(), on_create_identity=lambda: None)
    values = {"mode": "listen", "port": "9000", "target": "x:1", "source": "mic", "wav": "",
              "record_path": ""}
    for name, v in {**values, "key": "", "cert": "", "peer_cert": "", "record": False}.items():
        var = MagicMock()                  # each Tk variable its own mock
        var.get.return_value = v
        setattr(panel, name, var)
    assert panel.read()["friends"] is True
    panel.key.get.return_value = "me.key"
    with pytest.raises(ValueError, match="leave all three empty"):
        panel.read()


def test_worker_in_friend_mode_without_friends_says_pair_first(monkeypatch, tmp_path):
    monkeypatch.setenv("HPQV_HOME", str(tmp_path))
    win = make_window()
    cfg = {"mode": "call", "port": 9000, "target": "127.0.0.1:9", "friends": True,
           "key": "", "cert": "", "peer_cert": "", "input": "mic", "wav": None,
           "record_wav": None, "stop": threading.Event(), "stats": Stats(),
           "handle": win.call_handle}
    win.controls = MagicMock(get=lambda k: 0)
    win._run(cfg)
    events = []
    while not win.events.empty():
        events.append(win.events.get_nowait())
    assert ("state", "no paired devices yet: use Pair a device first") in events
    assert events[-1] == ("done", None)


def test_ask_user_shows_the_code_and_returns_the_answer(monkeypatch):
    win = make_window()
    win.root.after.side_effect = lambda ms, fn: fn()
    shown = {}

    def ask(title, body):
        shown["title"], shown["body"] = title, body
        return True
    monkeypatch.setattr(gui.messagebox, "askyesno", ask)
    assert win._ask_user("123 456", "bob", "a" * 79, False, threading.Event()) is True
    assert "123 456" in shown["body"] and "bob" in shown["body"]


def test_ask_user_is_no_when_the_peer_cancels_first(monkeypatch):
    win = make_window()
    win.root.after.side_effect = lambda ms, fn: None          # dialog never answers
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    t0 = time.monotonic()
    assert win._ask_user("123 456", "bob", "a" * 79, False, cancel) is False
    assert time.monotonic() - t0 < 3


# ---------------- worker: the listener asks before answering (real TLS) ----------------

def _start_listener(win, monkeypatch, friend_home=None, caller_name="caller", paired=False):
    """Start the listener worker on a free port. With `friend_home` it runs in friend
    mode (no files) with that HPQV_HOME; `paired` pins the caller there first.
    Returns (thread, cfg, caller, listener, sock)."""
    import socket
    from hpqv import demo, identity, pairflow, paths
    caller = identity.new(caller_name)
    if friend_home is not None:
        monkeypatch.setenv("HPQV_HOME", str(friend_home))
        listener = paths.device_identity("listener")
        if paired:
            pairflow.store().add(caller.cert_pem)
    else:
        listener = identity.new("listener")
        monkeypatch.setattr(demo, "_load_identity", lambda *a: (listener, caller.cert_pem))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    cfg = {"mode": "listen", "port": port, "target": "", "key": "k", "cert": "c",
           "peer_cert": "p", "input": "mic", "wav": None, "record_wav": None,
           "stop": win.stop_event, "stats": demo.Stats(), "handle": win.call_handle,
           "answers": queue.Queue(), "friends": friend_home is not None}
    win.controls = MagicMock(get=lambda k: 0)
    t = threading.Thread(target=win._run, args=(cfg,))
    t.start()
    for _ in range(50):
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=2)
            break
        except OSError:
            time.sleep(0.05)
    return t, cfg, caller, listener, sock


def _listen_real_tls(win, monkeypatch, act, friend_home=None):
    """Run the listener worker against a real caller-side TLS channel. `act(cfg, ch)`
    plays the user once the prompt event has been raised; returns (events, cfg)."""
    import struct
    from hpqv import control, secure
    t, cfg, caller, listener, sock = _start_listener(
        win, monkeypatch, friend_home, "caller" if friend_home is None else "bob", paired=True)
    ch = secure.SecureChannel.establish(sock, "client", caller, listener.cert_pem)
    ch.send(control.MSG_CALL_START, struct.pack(">H", 5555))
    events = []
    deadline = time.monotonic() + 5
    while not any(k == "incoming" for k, _ in events) and time.monotonic() < deadline:
        try:
            events.append(win.events.get(timeout=0.1))
        except queue.Empty:
            pass
    try:
        act(cfg, ch)
        t.join(5)
        assert not t.is_alive()
    finally:
        ch.close()
    while not win.events.empty():
        events.append(win.events.get_nowait())
    return events, cfg


def test_listener_prompt_carries_the_peers_fingerprint_and_sas_and_decline_ends_the_call(
        monkeypatch):
    from hpqv import control
    got = {}

    def decline(cfg, ch):
        cfg["answers"].put(False)
        got["msg"] = ch.recv(timeout=3)

    events, cfg = _listen_real_tls(make_window(), monkeypatch, decline)
    incoming = next(v for k, v in events if k == "incoming")
    assert incoming == {"fingerprint": cfg["stats"].peer_fingerprint, "sas": cfg["stats"].sas,
                        "name": ""}
    assert len(incoming["sas"]) == 8 and len(incoming["fingerprint"].split()) == 16
    assert got["msg"] == (control.MSG_CALL_END, b"")
    assert ("state", "declined") in events and events[-1] == ("done", None)


def test_listener_hang_up_while_ringing_says_hung_up(monkeypatch):
    win = make_window()
    events, _ = _listen_real_tls(win, monkeypatch, lambda cfg, ch: win.stop_event.set())
    assert ("state", "hung up") in events and events[-1] == ("done", None)


def test_listener_accept_drains_stale_audio_then_starts_the_call(monkeypatch):
    import socket
    from hpqv import demo
    calls, drained = [], []
    real_drain = demo.drain_udp
    monkeypatch.setattr(demo, "drain_udp", lambda s: drained.append(real_drain(s)))
    monkeypatch.setattr(demo, "_run_call", lambda ch, udp, dest, args, **kw: calls.append(dest))

    def accept(cfg, ch):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
            tx.sendto(b"\x21" * 40, ("127.0.0.1", cfg["port"]))     # audio sent while ringing
        time.sleep(0.1)
        cfg["answers"].put(True)

    events, _ = _listen_real_tls(make_window(), monkeypatch, accept)
    assert calls == [("127.0.0.1", 5555)]
    assert drained == [1]
    assert ("state", "connected — talk") in events


# ---------------- worker: pairing and the incoming-call prompt compose ----------------

def test_friend_mode_listener_prompts_a_paired_friend_by_name(monkeypatch, tmp_path):
    def decline(cfg, ch):
        cfg["answers"].put(False)

    events, _ = _listen_real_tls(make_window(), monkeypatch, decline, friend_home=tmp_path)
    incoming = next(v for k, v in events if k == "incoming")
    assert incoming["name"] == "bob"
    assert ("state", "declined") in events


@pytest.mark.parametrize("mode", ["call", "pair"])
def test_friend_mode_listener_never_prompts_an_unpaired_caller(monkeypatch, tmp_path, mode):
    """Trust needs a pin or a confirmed code: an unpaired caller fails the handshake
    on the call port, so no Accept prompt (and no code) is ever shown for it, and the
    call port does not run pairing either (that has its own window)."""
    from hpqv import identity, pairflow, secure
    win = make_window()
    monkeypatch.setenv("HPQV_HOME", str(tmp_path))
    pairflow.store().add(identity.new("alice").cert_pem)     # some other friend
    t, cfg, mallory, listener, sock = _start_listener(win, monkeypatch, tmp_path, "mallory")
    with pytest.raises(secure.HandshakeError):
        secure.SecureChannel.establish(sock, "client", mallory,
                                       listener.cert_pem if mode == "call" else None, mode=mode)
    t.join(5)
    assert not t.is_alive()
    events = []
    while not win.events.empty():
        events.append(win.events.get_nowait())
    assert not any(k == "incoming" for k, _ in events)
    assert any(k == "state" and v.startswith("handshake failed") for k, v in events)
    assert [f.label for f in pairflow.store().friends()] == ["alice"]
