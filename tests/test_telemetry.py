"""Telemetry numbers end up cited in the report, so they are checked against
eval.mos and hand-computed counter deltas rather than against themselves."""
import csv
import os
import socket
import sys
import threading
import types
import wave

import numpy as np
import pytest

from hpqv.controls import CallControls, DEFAULTS
from hpqv.demo import FrameSink, Stats
from hpqv.eval.mos import mos_from_network
from hpqv.packet import REJECT_REASONS
from hpqv.telemetry import COLUMNS, Telemetry

from v2pair import rings


def _bump(stats, **delta):
    for k, v in delta.items():
        setattr(stats, k, getattr(stats, k) + v)


def test_interval_loss_residual_and_mos_come_from_the_receiver_only():
    stats, controls = Stats(), CallControls(delay_ms=40, jitter_ms=10, jitter_depth=3)
    tel = Telemetry(stats, controls)
    # Local sender numbers describe the PEER's stream and must not leak in.
    _bump(stats, sent=500, dropped=500)
    # Peer's stream: 100 seqs spanned, 80 arrived, FEC rebuilt 5.
    # Transit relative to the call's fastest packet: mean 30 ms, minimum -10 ms.
    _bump(stats, rx_expected=100, received=80, recovered=5,
          rx_transit_n=80, rx_transit_sum_ms=80 * 30.0)
    stats.rx_transit_min_ms, stats.rx_jitter_ms = -10.0, 7.5
    row = tel.sample(1.0)
    assert row["interval_expected"] == 100
    assert row["interval_delivered"] == 80
    assert row["interval_loss_pct"] == pytest.approx(20.0)
    # 20 frames lost, FEC rebuilt 5 -> 15 never played
    assert row["interval_residual"] == 15
    assert row["residual_loss_pct"] == pytest.approx(15.0)
    assert row["added_delay_ms"] == pytest.approx(40.0)
    assert row["rx_jitter_ms"] == pytest.approx(7.5)
    assert row["effective_delay_ms"] == pytest.approx(20 * 3 + 40.0)
    # Local delay/jitter knobs act on the peer's audio, not on these numbers.
    assert row["mos"] == pytest.approx(mos_from_network(100, 0, 15.0))

    _bump(stats, rx_expected=100, received=100,     # clean, at the fastest transit seen
          rx_transit_n=100, rx_transit_sum_ms=100 * -10.0)
    row = tel.sample(2.0)
    assert row["interval_loss_pct"] == 0 and row["residual_loss_pct"] == 0
    assert row["added_delay_ms"] == pytest.approx(0.0)
    assert row["mos"] == pytest.approx(mos_from_network(60, 0, 0.0))
    assert row["sent"] == 500                    # counters stay cumulative


def test_incoming_drops_and_late_packets_count_as_loss():
    stats = Stats()
    tel = Telemetry(stats, CallControls())
    # 100 expected, 100 reach the socket, 10 dropped by receiver-side loss, 4 too late
    _bump(stats, rx_expected=100, received=100, incoming_dropped=10, late=4)
    row = tel.sample(1.0)
    assert row["interval_delivered"] == 90
    assert row["interval_loss_pct"] == pytest.approx(10.0)
    assert row["interval_lost"] == 14
    assert row["residual_loss_pct"] == pytest.approx(14.0)


def test_baseline_taken_at_construction_and_idle_sample_is_safe():
    stats = Stats()
    _bump(stats, sent=500, received=500, rx_expected=500)
    tel = Telemetry(stats, CallControls())
    row = tel.sample(1.0)                        # nothing happened since construction
    assert row["interval_expected"] == 0
    assert row["interval_loss_pct"] == 0 and row["residual_loss_pct"] == 0
    assert row["mos"] == pytest.approx(mos_from_network(60, 0, 0.0))


def test_rates_use_the_real_sample_spacing():
    stats = Stats()
    tel = Telemetry(stats, CallControls())
    _bump(stats, rx_expected=50, received=50, recovered=3)
    assert tel.sample(1.0)["dt_s"] == pytest.approx(1.0)
    _bump(stats, rx_expected=60, received=54, recovered=6)
    row = tel.sample(2.2)                        # GUI timer slipped: 1.2 s interval
    assert row["dt_s"] == pytest.approx(1.2)
    assert row["recovered_per_s"] == pytest.approx(6 / 1.2)


def test_rx_meter_counts_seqs_older_than_the_first_arrival():
    """Jitter can deliver seq 1 before seq 0; the span must still cover seq 0."""
    stats = Stats()
    from hpqv.demo import RxMeter
    meter = RxMeter(stats)
    for seq, now in ((1, 10.000), (0, 10.010), (2, 10.040)):
        meter.arrive(seq, now)
    assert stats.rx_expected == 3


def test_rx_meter_tracks_seq_span_rfc3550_jitter_and_relative_transit():
    stats = Stats()
    from hpqv.demo import RxMeter
    meter = RxMeter(stats)
    tel = Telemetry(stats, CallControls(jitter_depth=2))
    # transit vs a 20 ms/frame schedule: 0, 0, +20, 0 ms
    for seq, now in ((0, 10.000), (1, 10.020), (2, 10.060), (3, 10.060)):
        meter.arrive(seq, now)
    meter.arrive(6, 10.120)                      # 4, 5 missing: span grows to 7
    meter.arrive(5, 10.200)                      # reordered: no new span
    assert stats.rx_expected == 7
    assert stats.rx_transit_min_ms == pytest.approx(0.0)
    # transits 0, 0, 20, 0, 0, +100 (seq 5 at 10.2 s) -> mean 20 ms above the minimum
    row = tel.sample(1.0)
    assert row["added_delay_ms"] == pytest.approx(120 / 6)
    assert row["effective_delay_ms"] == pytest.approx(40 + 120 / 6)
    # RFC 3550: J += (|D| - J) / 16 over |D| = 0, 20, 20, 0, 100
    j = 0.0
    for d in (0, 20, 20, 0, 100):
        j += (d - j) / 16
    assert row["rx_jitter_ms"] == pytest.approx(j)


class _Frames:
    def __init__(self, n):
        self.n = n

    def frames(self):
        pcm = (np.sin(np.arange(960 * self.n) / 7.0) * 8000).astype(np.int16).tobytes()
        for i in range(self.n):
            yield pcm[i * 1920:(i + 1) * 1920]


def test_two_peers_each_measure_the_stream_they_receive():
    """A drops 20 % of what it sends; B sends fewer frames than A. Only B hears
    loss, and A, whose own receive stream is clean, must report none."""
    from hpqv.demo import _receiver_loop, _sender_loop
    ring_a, ring_b = rings()
    sess = {"A": ring_a, "B": ring_b}
    socks = {n: socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for n in "AB"}
    for s in socks.values():
        s.bind(("127.0.0.1", 0))
    ctl = {"A": CallControls(loss_pct=20), "B": CallControls()}
    stats = {"A": Stats(), "B": Stats()}
    tel = {n: Telemetry(stats[n], ctl[n]) for n in "AB"}
    n_frames = {"A": 500, "B": 300}
    rx_stop = threading.Event()
    rx = [threading.Thread(target=_receiver_loop,
                           args=(socks[n], sess[n], _Sink(), ctl[n], stats[n], rx_stop))
          for n in "AB"]
    tx = [threading.Thread(target=_sender_loop,
                           args=(socks[n], socks[o].getsockname(), sess[n], _Frames(n_frames[n]),
                                 ctl[n], stats[n], threading.Event()))
          for n, o in (("A", "B"), ("B", "A"))]
    for t in rx + tx:
        t.start()
    for t in tx:
        t.join(timeout=30)
    for _ in range(100):                         # let the receivers drain
        got = (stats["A"].received, stats["B"].received)
        threading.Event().wait(0.05)
        if got == (stats["A"].received, stats["B"].received) and got[0] == 300:
            break
    rx_stop.set()
    for t in rx:
        t.join(timeout=3)
    for s in socks.values():
        s.close()

    row_a, row_b = tel["A"].sample(10.0), tel["B"].sample(10.0)
    assert stats["A"].dropped > 0 and stats["B"].dropped == 0
    assert row_b["interval_expected"] > 480      # trailing drops are invisible, nothing else
    assert 13.0 < row_b["interval_loss_pct"] < 27.0
    assert row_b["residual_loss_pct"] <= row_b["interval_loss_pct"]
    assert row_a["interval_expected"] == 300
    assert row_a["interval_loss_pct"] == 0 and row_a["residual_loss_pct"] == 0


class _Sink:
    def write(self, pcm):
        pass


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
        _bump(stats, rx_expected=50, received=50)
        tel.sample(t)
    controls.set(loss_pct=20, repair="fec")
    for t in (4.0, 5.0):                         # segment 2: 20% loss heard here, FEC repairs 6 of 10
        _bump(stats, rx_expected=50, received=40, recovered=6)
        tel.sample(t)
    tel.note("rotate keys", 5.0)
    return tel, stats


def test_segments_follow_settings_changes():
    tel, _ = _filled(None)
    s1, s2 = tel.segments()
    assert (s1["start_t"], s1["end_t"], s1["samples"]) == (1.0, 3.0, 3)
    assert s1["settings"] == {}                  # all defaults
    assert s1["expected"] == 150 and s1["lost"] == 0
    assert s1["mean_mos"] == pytest.approx(mos_from_network(60, 0, 0.0))
    assert s2["settings"] == {"loss_pct": 20.0}
    assert (s2["start_t"], s2["end_t"], s2["samples"]) == (4.0, 5.0, 2)
    assert s2["expected"] == 100 and s2["delivered"] == 80
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
    # Local knobs act on the peer's audio: the summary must say so next to them
    assert "local sender knobs (affect what the PEER hears): loss_pct=20" in text
    assert "### Segment 2: loss_pct" not in text
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


def test_speaker_backlog_is_bounded_and_skips_are_counted(monkeypatch):
    """A PLC burst (or a lowered delay) must not leave the speaker seconds behind:
    beyond jitter_depth + 2 queued frames, new frames are skipped and counted."""
    played = []

    class FakeStream:
        def __init__(self, **kw): pass
        def start(self): pass
        def write(self, arr): played.append(arr.tobytes())
        def stop(self): pass
        def close(self): pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=FakeStream))
    now = [100.0]
    stats, controls = Stats(), CallControls(jitter_depth=3)
    sink = FrameSink("speaker", controls=controls, stats=stats, clock=lambda: now[0])
    frame = bytes(1920)
    for _ in range(50):                          # 1 s of PLC written at one instant
        sink.write(frame)
    assert len(played) == 5 and stats.skipped == 45
    now[0] += 0.1                                # speaker plays 5 frames
    for _ in range(6):
        sink.write(frame)
    assert len(played) == 10 and stats.skipped == 46
    for _ in range(200):                         # real-time pace: nothing skipped
        now[0] += 0.02
        sink.write(frame)
    assert stats.skipped == 46 and len(played) == 210
    assert "skipped" in stats.as_dict() and "skipped" in COLUMNS
    sink.close()


def test_receiver_dead_socket_ends_the_call():
    """A socket that is really gone ends the call. (WSAECONNRESET no longer does: it
    comes from a spoofable ICMP, so a dead peer is detected on the authenticated TCP
    channel instead; see tests/test_t6_regressions.py.)"""
    import errno
    from hpqv.demo import _receiver_loop

    class DeadSock:
        def settimeout(self, t): pass
        def recvfrom(self, n): raise OSError(errno.EBADF, "Bad file descriptor")

    stop = threading.Event()
    _receiver_loop(DeadSock(), rings()[0], _Sink(),
                   CallControls(), Stats(), stop)
    assert stop.is_set()


def test_rejects_are_cumulative_columns_in_every_row():
    stats, controls = Stats(), CallControls()
    tel = Telemetry(stats, controls)
    assert all(f"rejects_{r}" in COLUMNS for r in REJECT_REASONS)
    assert tel.sample(1.0)["rejects_replay"] == 0
    stats.rejects = {**stats.rejects, "replay": 3, "auth": 1}
    row = tel.sample(2.0)
    assert (row["rejects_replay"], row["rejects_auth"], row["rejects_oversize"]) == (3, 1, 0)
    stats.rejects = {**stats.rejects, "replay": 4}
    assert tel.sample(3.0)["rejects_replay"] == 4


def test_export_never_writes_the_sas_the_fingerprint_or_key_material(tmp_path):
    tel, stats = _filled(tmp_path)
    stats.sas, stats.peer_fingerprint = "c0ffee42", "dead beef " * 15 + "dead beef"
    stats.group, stats.cipher = "X25519MLKEM768", "TLS_AES_256_GCM_SHA384"
    stats.handshake_ms, stats.handshake_bytes = 41.5, 20000
    stats.last_datagram = b"\x21" + b"\xee" * 60
    paths = tel.export(str(tmp_path / "out"))
    assert not {"sas", "peer_fingerprint", "last_datagram"} & set(COLUMNS)
    for p in paths:
        if p.endswith((".csv", ".md")):
            text = open(p, encoding="utf-8").read().lower()
            assert "c0ffee42" not in text and "dead beef" not in text
            assert "eeeeee" not in text and "xee" not in text
            assert "fingerprint" not in text and "sas" not in text.split()
    summary = open(paths[1], encoding="utf-8").read()
    assert "X25519MLKEM768" in summary and "20000 B" in summary     # public negotiation facts
    assert "HELLO" not in summary                                    # v1 wording is gone


def test_summary_lists_the_reject_counters(tmp_path):
    tel, stats = _filled(tmp_path)
    stats.rejects = {**stats.rejects, "replay": 7}
    summary = tel.export(str(tmp_path / "out"))[1]
    assert "replay 7" in open(summary, encoding="utf-8").read()
