"""Telemetry numbers end up cited in the report, so they are checked against
eval.mos and hand-computed counter deltas rather than against themselves."""
import csv
import os
import socket
import sys
import threading
import types
import wave

import pytest

from hpqv import control, handshake as h
from hpqv.controls import CallControls, DEFAULTS
from hpqv.demo import FrameSink, Stats, handshake_info, timed_handshake
from hpqv.eval.mos import mos_from_network
from hpqv.telemetry import COLUMNS, Telemetry


def _bump(stats, **delta):
    for k, v in delta.items():
        setattr(stats, k, getattr(stats, k) + v)


def test_interval_loss_residual_and_mos_match_eval_mos():
    stats, controls = Stats(), CallControls(delay_ms=40, jitter_ms=10, jitter_depth=3)
    tel = Telemetry(stats, controls)
    _bump(stats, sent=90, dropped=10, received=80, recovered=5)
    row = tel.sample(1.0)
    assert row["interval_offered"] == 100
    assert row["interval_delivered"] == 80
    assert row["interval_loss_pct"] == pytest.approx(20.0)
    # 20 frames lost, FEC rebuilt 5 -> 15 never played
    assert row["interval_residual"] == 15
    assert row["residual_loss_pct"] == pytest.approx(15.0)
    assert row["effective_delay_ms"] == 40 + 20 * 3
    assert row["mos"] == pytest.approx(mos_from_network(100, 10, 15.0))

    _bump(stats, sent=100, received=100)         # clean second interval
    row = tel.sample(2.0)
    assert row["interval_loss_pct"] == 0 and row["residual_loss_pct"] == 0
    assert row["mos"] == pytest.approx(mos_from_network(100, 10, 0.0))
    assert row["sent"] == 190                    # counters stay cumulative


def test_incoming_drops_and_late_packets_count_as_loss():
    stats = Stats()
    tel = Telemetry(stats, CallControls())
    # 100 offered, 100 reach the socket, 10 dropped by receiver-side loss, 4 too late
    _bump(stats, sent=100, received=100, incoming_dropped=10, late=4)
    row = tel.sample(1.0)
    assert row["interval_delivered"] == 90
    assert row["interval_loss_pct"] == pytest.approx(10.0)
    assert row["interval_lost"] == 14
    assert row["residual_loss_pct"] == pytest.approx(14.0)


def test_baseline_taken_at_construction_and_idle_sample_is_safe():
    stats = Stats()
    _bump(stats, sent=500, received=500)
    tel = Telemetry(stats, CallControls())
    row = tel.sample(1.0)                        # nothing happened since construction
    assert row["interval_offered"] == 0
    assert row["interval_loss_pct"] == 0 and row["residual_loss_pct"] == 0
    assert row["mos"] == pytest.approx(mos_from_network(60, 0, 0.0))


def test_events_on_knob_change_key_rotation_and_note():
    stats, controls = Stats(), CallControls()
    tel = Telemetry(stats, controls)
    tel.sample(1.0)
    assert tel.events == []
    controls.set(loss_pct=20, repair="plc")
    tel.sample(2.0)
    changed = {(e["name"], e["new"]) for e in tel.events if e["kind"] == "knob"}
    assert changed == {("loss_pct", 20.0), ("repair", "plc")}
    assert all(e["t"] == 2.0 for e in tel.events)
    stats.key_gen = 1
    tel.sample(3.0)
    assert any(e["kind"] == "key" and e["t"] == 3.0 for e in tel.events)
    tel.note("hold music started", 3.5)
    assert tel.events[-1] == {"t": 3.5, "kind": "note", "label": "hold music started"}


def _filled(tmp_path):
    stats, controls = Stats(), CallControls()
    tel = Telemetry(stats, controls)
    for t in (1.0, 2.0, 3.0):                    # segment 1: defaults, clean
        _bump(stats, sent=50, received=50)
        tel.sample(t)
    controls.set(loss_pct=20, repair="fec")
    for t in (4.0, 5.0):                         # segment 2: 20% loss, FEC repairs 6 of 10
        _bump(stats, sent=40, dropped=10, received=40, recovered=6)
        tel.sample(t)
    tel.note("rotate keys", 5.0)
    return tel, stats


def test_segments_follow_settings_changes():
    tel, _ = _filled(None)
    s1, s2 = tel.segments()
    assert (s1["start_t"], s1["end_t"], s1["samples"]) == (1.0, 3.0, 3)
    assert s1["settings"] == {}                  # all defaults
    assert s1["offered"] == 150 and s1["lost"] == 0
    assert s1["mean_mos"] == pytest.approx(mos_from_network(60, 0, 0.0))
    assert s2["settings"] == {"loss_pct": 20.0}
    assert (s2["start_t"], s2["end_t"], s2["samples"]) == (4.0, 5.0, 2)
    assert s2["offered"] == 100 and s2["delivered"] == 80
    assert s2["lost"] == 20 and s2["recovered"] == 12
    assert s2["residual"] == 8
    assert s2["residual_loss_pct"] == pytest.approx(8.0)
    assert s2["recovered_per_lost"] == pytest.approx(0.6)
    per_row = mos_from_network(60, 0, 8.0)
    assert s2["mean_mos"] == pytest.approx(per_row) and s2["min_mos"] == pytest.approx(per_row)


def test_export_writes_csv_summary_and_pngs(tmp_path):
    tel, stats = _filled(tmp_path)
    stats.handshake_ms, stats.handshake_bytes = 41.5, 8000
    paths = tel.export(str(tmp_path / "out"))
    names = sorted(os.path.basename(p) for p in paths)
    assert names == ["datagram_size.png", "loss_vs_recovered.png", "mos.png",
                     "summary.md", "timeline.csv"]
    assert all(os.path.getsize(p) > 0 for p in paths)
    for p in paths:
        if p.endswith(".png"):
            with open(p, "rb") as f:
                assert f.read(8) == b"\x89PNG\r\n\x1a\n"

    with open(os.path.join(tmp_path, "out", "timeline.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == COLUMNS
    assert {f"knob_{k}" for k in DEFAULTS} <= set(COLUMNS)
    assert len(rows) == 5 and float(rows[3]["knob_loss_pct"]) == 20.0

    text = open(os.path.join(tmp_path, "out", "summary.md"), encoding="utf-8").read()
    assert "Segment 1" in text and "Segment 2" in text
    assert "loss_pct=20" in text
    assert "41.5 ms" in text and "8000 B" in text
    assert "rotate keys" in text                 # events are listed


def test_export_with_no_samples_still_works(tmp_path):
    tel = Telemetry(Stats(), CallControls())
    paths = tel.export(str(tmp_path))
    assert len(paths) == 5 and all(os.path.getsize(p) > 0 for p in paths)


def test_frame_sink_tees_to_wav_while_playing(tmp_path, monkeypatch):
    played = []

    class FakeStream:
        def __init__(self, **kw): pass
        def start(self): pass
        def write(self, arr): played.append(arr.tobytes())
        def stop(self): pass
        def close(self): pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    out = str(tmp_path / "rec.wav")
    sink = FrameSink("speaker", record_path=out)
    frames = [bytes([i]) * 1920 for i in range(5)]
    for f in frames:
        sink.write(f)
    sink.close()
    sink.write(frames[0])                        # late write after hang-up is ignored
    assert played == frames
    with wave.open(out, "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 1, 2)
        assert w.readframes(w.getnframes()) == b"".join(frames)


def test_handshake_info_matches_real_message_sizes():
    pub, sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(pub, sec)
    accept, _ = h.accept_hello(hello, pub, sec)
    info = handshake_info()
    assert info["hello_bytes"] == len(hello) and info["accept_bytes"] == len(accept)
    assert info["total_bytes"] == len(hello) + len(accept)
    assert (info["kem"], info["sig"]) == ("ML-KEM-768", "ML-DSA-65")
    assert (info["transport"], info["udp_fragments"], info["datagram_cap"]) == ("TCP", 0, 1200)


def test_timed_handshake_fills_stats_in_loopback():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    box = {"stats": Stats()}

    def responder():
        conn, _ = srv.accept()
        box["session"] = timed_handshake("responder", conn, b_pub, b_sec, a_pub, box["stats"])
        conn.close()

    t = threading.Thread(target=responder)
    t.start()
    cli = socket.create_connection(srv.getsockname())
    init_stats = Stats()
    session = timed_handshake("initiator", cli, a_pub, a_sec, b_pub, init_stats)
    t.join()
    cli.close()
    srv.close()
    assert session[0] == box["session"][0]       # same key: the wrapper didn't disturb the protocol
    expected = handshake_info()["total_bytes"]
    for stats in (init_stats, box["stats"]):
        assert stats.handshake_ms > 0
        assert stats.handshake_bytes == expected
