"""Live FEC: the receiver must call decode_fec when a gap appears.

The encoder always enabled in-band FEC, but the receive path only ever did a
normal decode, so a lost frame was skipped and the redundancy was never used.
"""
import socket
import threading

import numpy as np
import pytest

from hpqv import packet
from hpqv.audio import FRAME_SAMPLES, FS, OpusCodec, _encoder_get, _encoder_set
from hpqv.controls import CallControls
from hpqv.demo import Stats, _receiver_loop
from hpqv.eval.fec_recovery import _frames, _segmental_snr_db, _test_signal
from hpqv import playout as playout_mod
from hpqv.playout import MAX_GAP, Playout, packet_has_lbrr

from v2pair import rings, sealer_and_ring

N = 40
_ALWAYS = lambda opus, fec_hint: True


def _packets(n=N):
    enc = OpusCodec()
    return [enc.encode(f.tobytes()) for f in _frames(_test_signal(n * 0.02))]


def _run(packets, dropped):
    pl = Playout(OpusCodec())
    out = []
    for seq, opus in enumerate(packets):
        if seq not in dropped:
            out += pl.frames(seq, opus, fec_hint=True)     # encoder FEC is on
    return pl, out


def test_single_gaps_are_recovered_and_timing_is_continuous():
    pl, out = _run(_packets(), {5, 12, 20})
    assert len(out) == N
    assert (pl.recovered, pl.concealed) == (3, 0)


def test_burst_gap_recovers_last_frame_and_conceals_the_rest():
    pl, out = _run(_packets(), {10, 11, 12})
    assert len(out) == N
    assert (pl.recovered, pl.concealed) == (1, 2)


def test_trailing_loss_emits_nothing_extra():
    pl, out = _run(_packets(), {N - 1})
    assert len(out) == N - 1 and (pl.recovered, pl.concealed) == (0, 0)


def test_recovered_frame_comes_from_the_next_packets_fec_decode():
    """Wiring check that holds on every platform: gap at i -> decode_fec on packet i+1."""
    packets = _packets()
    _, out = _run(packets, {10})
    want = OpusCodec()
    for p in packets[:10]:
        want.decode(p)
    assert out[10] == want.decode(packets[11], decode_fec=True)


def _opus_ctls_work():
    """Check that the encoder's in-band FEC setting can be read back."""
    try:
        codec = OpusCodec()
        return _encoder_get(codec.enc, 4013) == 1
    except RuntimeError:
        return False


@pytest.mark.skipif(not _opus_ctls_work(), reason="in-band FEC CTL unavailable on this platform")
def test_recovered_frames_beat_zero_frames():
    """Same metric as the offline experiment: segmental SNR vs the undamaged decode."""
    packets = _packets()
    ref_codec = OpusCodec()
    ref = [ref_codec.decode(p) for p in packets]
    lost = set(range(5, N - 1, 5))
    _, out = _run(packets, lost)
    zero = bytes(FRAME_SAMPLES * 2)
    base = OpusCodec()
    zeroed = [zero if i in lost else base.decode(p) for i, p in enumerate(packets)]
    pcm = lambda frames: np.frombuffer(b"".join(frames), dtype=np.int16)
    snr_fec = _segmental_snr_db(pcm(ref), pcm(out))
    snr_zero = _segmental_snr_db(pcm(ref), pcm(zeroed))
    assert snr_fec > snr_zero + 5, (snr_fec, snr_zero)


def test_gap_calls_codec_in_exact_order():
    """Pins conceal-vs-FEC: PLC for all but the last missing frame, FEC for the last."""
    calls = []

    class RecordingCodec:
        def decode(self, data, decode_fec=False):
            calls.append(("decode", data, decode_fec))
            return b"pcm"

        def conceal(self):
            calls.append(("conceal",))
            return b"plc"

    pl = Playout(RecordingCodec(), has_lbrr=_ALWAYS)
    for seq in (10, 11, 14):
        pl.frames(seq, b"p%d" % seq)
    assert calls == [
        ("decode", b"p10", False),
        ("decode", b"p11", False),
        ("conceal",),
        ("decode", b"p14", True),
        ("decode", b"p14", False),
    ]


@pytest.mark.parametrize("repair, expected_calls, expected_pcm", [
    ("silence", [("decode", b"p10", False), ("decode", b"p14", False)],
     [b"pcm", bytes(3), bytes(3), bytes(3), b"pcm"]),
    ("plc", [("decode", b"p10", False), ("conceal",), ("conceal",),
              ("conceal",), ("decode", b"p14", False)],
     [b"pcm", b"plc", b"plc", b"plc", b"pcm"]),
    ("fec", [("decode", b"p10", False), ("conceal",), ("conceal",),
              ("decode", b"p14", True), ("decode", b"p14", False)],
     [b"pcm", b"plc", b"plc", b"fec", b"pcm"]),
])
def test_repair_modes_define_gap_audio_and_counters(repair, expected_calls, expected_pcm):
    calls = []

    class RecordingCodec:
        def decode(self, data, decode_fec=False):
            calls.append(("decode", data, decode_fec))
            return b"fec" if decode_fec else b"pcm"

        def conceal(self):
            calls.append(("conceal",))
            return b"plc"

    playout = Playout(RecordingCodec(), repair=repair, has_lbrr=_ALWAYS)
    out = playout.frames(10, b"p10") + playout.frames(14, b"p14")
    assert calls == expected_calls
    assert out == expected_pcm
    assert (playout.recovered, playout.concealed) == {
        "silence": (0, 3), "plc": (0, 3), "fec": (1, 2)
    }[repair]


def test_late_or_duplicate_packet_is_dropped_without_touching_the_decoder():
    calls = []

    class RecordingCodec:
        def decode(self, data, decode_fec=False):
            calls.append(data)
            return b"pcm"

        def conceal(self):
            raise AssertionError("no gap, nothing to conceal")

    pl = Playout(RecordingCodec())
    out = []
    for seq in (0, 1, 2, 1, 3):
        out += pl.frames(seq, b"p%d" % seq)
    assert len(out) == 4
    assert calls == [b"p0", b"p1", b"p2", b"p3"]


def _speech_like(n_frames):
    """Gliding-pitch harmonics with syllable-rate amplitude modulation."""
    t = np.arange(n_frames * FRAME_SAMPLES) / FS
    phase = 2 * np.pi * np.cumsum(120 + 20 * np.sin(2 * np.pi * 0.7 * t)) / FS
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12))
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 3 * t) ** 2
    return (0.125 * voiced * envelope * 32767).astype(np.int16)


@pytest.mark.skipif(not _opus_ctls_work(), reason="in-band FEC CTL unavailable on this platform")
def test_fec_decode_uses_lbrr_data_and_beats_plc():
    """The chirp signal is coded as CELT, where decode_fec == conceal byte-for-byte.
    SILK at 20 kbit/s carries real LBRR, so FEC output must differ from PLC and
    sit closer to the undamaged decode."""
    enc = OpusCodec()
    _encoder_set(enc.enc, 4002, 20000)            # OPUS_SET_BITRATE
    assert _encoder_get(enc.enc, 4003) == 20000
    pcm = _speech_like(60)
    packets = [enc.encode(pcm[i:i + FRAME_SAMPLES].tobytes())
               for i in range(0, len(pcm), FRAME_SAMPLES)]
    assert all(p[0] >> 3 < 12 for p in packets), "expected SILK-only TOC configs"

    def primed(k):
        dec = OpusCodec()
        for p in packets[:k - 1]:
            dec.decode(p)
        return dec

    pcm16 = lambda b: np.frombuffer(b, dtype=np.int16)
    differs, snr_fec, snr_plc = 0, [], []
    probes = range(20, 50, 3)
    for k in probes:                              # frame k-1 is "lost"; packet k carries its LBRR
        good = pcm16(primed(k).decode(packets[k - 1]))
        fec = pcm16(primed(k).decode(packets[k], decode_fec=True))
        plc = pcm16(primed(k).conceal())
        differs += not np.array_equal(fec, plc)
        snr_fec.append(_segmental_snr_db(good, fec))
        snr_plc.append(_segmental_snr_db(good, plc))
    assert differs >= len(probes) // 2, differs
    assert np.mean(snr_fec) > np.mean(snr_plc) + 3, (snr_fec, snr_plc)


def test_huge_gap_is_not_synthesized():
    packets = _packets(MAX_GAP + 10)
    pl = Playout(OpusCodec())
    assert len(pl.frames(0, packets[0])) == 1
    assert len(pl.frames(MAX_GAP + 5, packets[MAX_GAP + 5])) == 1
    assert (pl.recovered, pl.concealed) == (0, 0)
    assert len(pl.frames(MAX_GAP + 6, packets[MAX_GAP + 6])) == 1   # resynced


def _receive(order, depth, packets=None, flags=packet.FLAG_FEC):   # encoder FEC on
    """Send packets `order` (seqs into _packets) over loopback to _receiver_loop."""
    seal, ring = sealer_and_ring()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    class Sink:
        def __init__(self):
            self.frames = []

        def write(self, pcm):
            self.frames.append(pcm)

    sink, stats, stop = Sink(), Stats(), threading.Event()
    t = threading.Thread(
        target=_receiver_loop, args=(rx, ring, sink, CallControls(jitter_depth=depth), stats, stop))
    t.start()
    try:
        packets = packets or _packets(max(order) + 1)
        for seq in order:
            tx.sendto(seal(seq, packets[seq], flags), rx.getsockname())
        for _ in range(100):
            if stats.snapshot()[1] >= len(order):
                break
            threading.Event().wait(0.05)
    finally:
        stop.set()
        t.join(timeout=3)
        tx.close()
        rx.close()
    return stats, sink


def test_receiver_loop_recovers_a_dropped_datagram():
    stats, sink = _receive([s for s in range(12) if s != 5], depth=3)
    assert stats.snapshot()[3] == 1
    assert len(sink.frames) == 12


def test_receiver_counts_all_incoming_loss_and_plays_nothing():
    seal, ring = sealer_and_ring()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    class Sink:
        def __init__(self):
            self.frames = []

        def write(self, pcm):
            self.frames.append(pcm)

    sink, stats, stop = Sink(), Stats(), threading.Event()
    controls = CallControls(incoming_loss_pct=100, jitter_depth=1)
    thread = threading.Thread(target=_receiver_loop,
                              args=(rx, ring, sink, controls, stats, stop))
    thread.start()
    try:
        for seq, opus in enumerate(_packets(5)):
            tx.sendto(seal(seq, opus), rx.getsockname())
        for _ in range(100):
            if stats.incoming_dropped == 5:
                break
            threading.Event().wait(0.01)
    finally:
        stop.set()
        thread.join(timeout=3)
        tx.close()
        rx.close()
    assert stats.incoming_dropped == 5
    assert sink.frames == []


def test_receiver_applies_jitter_depth_change_while_running():
    seal, ring = sealer_and_ring()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    class Sink:
        def __init__(self):
            self.frames = []

        def write(self, pcm):
            self.frames.append(pcm)

    sink, stats, stop = Sink(), Stats(), threading.Event()
    controls = CallControls(jitter_depth=50)
    thread = threading.Thread(target=_receiver_loop,
                              args=(rx, ring, sink, controls, stats, stop))
    thread.start()
    try:
        packets = _packets(4)
        for seq in range(2):
            tx.sendto(seal(seq, packets[seq]), rx.getsockname())
        for _ in range(100):
            if stats.snapshot()[1] == 2:
                break
            threading.Event().wait(0.01)
        assert sink.frames == []
        controls.set(jitter_depth=1)
        for seq in (2, 3):
            tx.sendto(seal(seq, packets[seq]), rx.getsockname())
        for _ in range(100):
            if len(sink.frames) >= 2:
                break
            threading.Event().wait(0.01)
    finally:
        stop.set()
        thread.join(timeout=3)
        tx.close()
        rx.close()
    assert len(sink.frames) >= 2


def test_recovered_counts_frames_missing_at_playout_not_lost_datagrams():
    """Depth 1, packets 4,6,5: 5 was missing when 6 played, so it is rebuilt from
    FEC and the real (late) 5 is discarded. recovered may therefore exceed dropped."""
    stats, sink = _receive([0, 1, 2, 3, 4, 6, 5], depth=1)
    assert stats.snapshot()[3] == 1          # 5 rebuilt via FEC
    assert stats.as_dict()["late"] == 1
    assert len(sink.frames) == 7             # seqs 0..6, 5 exactly once


def test_lowering_jitter_depth_drains_the_backlog_while_running():
    """Depth 15 -> 1 with 14 frames buffered: the next packet must flush the
    buffer down to the new depth, not pop one frame and stay 14 frames behind."""
    seal, ring = sealer_and_ring()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    class Sink:
        def __init__(self):
            self.frames = []

        def write(self, pcm):
            self.frames.append(pcm)

    sink, stats, stop = Sink(), Stats(), threading.Event()
    controls = CallControls(jitter_depth=15)
    thread = threading.Thread(target=_receiver_loop,
                              args=(rx, ring, sink, controls, stats, stop))
    thread.start()
    packets = _packets(17)

    def send_and_wait(seqs, frames):
        for seq in seqs:
            tx.sendto(seal(seq, packets[seq]), rx.getsockname())
        for _ in range(100):
            if stats.received == seqs[-1] + 1 and len(sink.frames) >= frames:
                break
            threading.Event().wait(0.01)

    try:
        send_and_wait(list(range(16)), 2)
        assert len(sink.frames) == 2             # 14 frames buffered at depth 15
        controls.set(jitter_depth=1)
        send_and_wait([16], 16)
        occupancy = 17 - len(sink.frames)        # checked before stop flushes the buffer
    finally:
        stop.set()
        thread.join(timeout=3)
        tx.close()
        rx.close()
    assert occupancy <= 1, occupancy


class _RecordingCodec:
    def __init__(self):
        self.calls = []

    def decode(self, data, decode_fec=False):
        self.calls.append(("decode", data, decode_fec))
        return b"fec" if decode_fec else b"pcm"

    def conceal(self):
        self.calls.append(("conceal",))
        return b"plc"


def test_fec_repair_conceals_when_the_next_packet_carries_no_lbrr():
    codec = _RecordingCodec()
    pl = Playout(codec, has_lbrr=lambda opus, fec_hint: False)
    out = pl.frames(10, b"p10") + pl.frames(14, b"p14")
    assert codec.calls == [("decode", b"p10", False), ("conceal",), ("conceal",),
                           ("conceal",), ("decode", b"p14", False)]
    assert out == [b"pcm", b"plc", b"plc", b"plc", b"pcm"]
    assert (pl.recovered, pl.concealed) == (0, 3)


def _silk_packets(n, fec=True):
    enc = OpusCodec(fec=fec)
    _encoder_set(enc.enc, 4002, 20000)            # 20 kbit/s speech: SILK, LBRR when FEC on
    pcm = _speech_like(n)
    return [enc.encode(pcm[i:i + FRAME_SAMPLES].tobytes())
            for i in range(0, len(pcm), FRAME_SAMPLES)]


_CELT = bytes([31 << 3]) + bytes(40)              # TOC config 31: CELT-only, never LBRR


@pytest.mark.skipif(playout_mod._HAS_LBRR is None or not _opus_ctls_work(),
                    reason="libopus without opus_packet_has_lbrr")
def test_has_lbrr_asks_libopus_and_ignores_the_sender_hint():
    with_fec, without = _silk_packets(30)[10:], _silk_packets(30, fec=False)[10:]
    assert sum(packet_has_lbrr(p, False) for p in with_fec) >= len(with_fec) // 2
    assert not any(packet_has_lbrr(p, True) for p in without)
    assert packet_has_lbrr(_CELT, True) is False


def test_has_lbrr_fallback_trusts_the_sender_flag_except_for_celt(monkeypatch):
    monkeypatch.setattr(playout_mod, "_HAS_LBRR", None)
    silk = _silk_packets(12, fec=False)[-1]
    assert packet_has_lbrr(silk, True) is True    # possibly-LBRR: sender had FEC on
    assert packet_has_lbrr(silk, False) is False
    assert packet_has_lbrr(_CELT, True) is False


@pytest.mark.parametrize("flags, counts", [(0, (0, 1)), (packet.FLAG_FEC, (1, 0))])
def test_receiver_fallback_uses_the_fec_flag(monkeypatch, flags, counts):
    monkeypatch.setattr(playout_mod, "_HAS_LBRR", None)
    stats, sink = _receive([s for s in range(12) if s != 5], depth=3,
                           packets=_silk_packets(12), flags=flags)
    assert (stats.recovered, stats.concealed) == counts
    assert len(sink.frames) == 12


def test_sender_sets_flag_bit_1_while_encoder_fec_is_on():
    import hpqv.demo as demo

    class FakeSock:
        def __init__(self):
            self.sent = []

        def sendto(self, dg, dest):
            self.sent.append(dg)

    class Source:
        def __init__(self, controls):
            self.controls = controls

        def frames(self):
            for i in range(4):
                if i == 2:
                    self.controls.set(fec=False)
                yield b"\x00\x01" * FRAME_SAMPLES

    tx_ring, rx_ring = rings()
    tx_ring.rotate_next()                         # generation 1: parity bit must survive
    rx_ring.rotate_to(1)
    controls, sock = CallControls(), FakeSock()
    demo._sender_loop(sock, ("127.0.0.1", 9), tx_ring, Source(controls), controls,
                      Stats(), threading.Event())
    flags = [rx_ring.open_(dg)[1] for dg in sock.sent]
    assert flags == [5, 5, 1, 1]                 # bit 2 = FEC hint, bits 0-1 = generation tag


def test_extra_flags_cannot_touch_the_generation_tag_bits():
    tx_ring, rx_ring = rings()
    dg = tx_ring.seal(0, b"x", extra_flags=0x07)
    assert rx_ring.open_(dg)[1] == 0x04      # generation 0, FEC bit only
