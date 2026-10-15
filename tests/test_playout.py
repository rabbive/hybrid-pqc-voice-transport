"""Live FEC: the receiver must call decode_fec when a gap appears.

The encoder always enabled in-band FEC, but the receive path only ever did a
normal decode, so a lost frame was skipped and the redundancy was never used.
"""
import socket
import threading

import numpy as np
import pytest

from hpqv import handshake as h, packet
from hpqv.audio import FRAME_SAMPLES, FS, OpusCodec, _encoder_get, _encoder_set
from hpqv.demo import Stats, _receiver_loop
from hpqv.eval.fec_recovery import _frames, _segmental_snr_db, _test_signal
from hpqv.playout import MAX_GAP, Playout

N = 40


def _session():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, b_sec)
    return h.finish(accept, kem, kem_pub, b_pub)


def _packets(n=N):
    enc = OpusCodec()
    return [enc.encode(f.tobytes()) for f in _frames(_test_signal(n * 0.02))]


def _run(packets, dropped):
    pl = Playout(OpusCodec())
    out = []
    for seq, opus in enumerate(packets):
        if seq not in dropped:
            out += pl.frames(seq, opus)
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

    pl = Playout(RecordingCodec())
    for seq in (10, 11, 14):
        pl.frames(seq, b"p%d" % seq)
    assert calls == [
        ("decode", b"p10", False),
        ("decode", b"p11", False),
        ("conceal",),
        ("decode", b"p14", True),
        ("decode", b"p14", False),
    ]


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


def _receive(order, depth):
    """Send packets `order` (seqs into _packets) over loopback to _receiver_loop."""
    key, send_prefix, recv_prefix = _session()
    session = (key, None, send_prefix)       # receiver opens what we seal
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
        target=_receiver_loop, args=(rx, session, sink, depth, stats, stop))
    t.start()
    try:
        packets = _packets(max(order) + 1)
        for seq in order:
            tx.sendto(packet.seal(key, send_prefix, seq, packets[seq]),
                      rx.getsockname())
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


def test_recovered_counts_frames_missing_at_playout_not_lost_datagrams():
    """Depth 1, packets 4,6,5: 5 was missing when 6 played, so it is rebuilt from
    FEC and the real (late) 5 is discarded. recovered may therefore exceed dropped."""
    stats, sink = _receive([0, 1, 2, 3, 4, 6, 5], depth=1)
    assert stats.snapshot()[3] == 1          # 5 rebuilt via FEC
    assert len(sink.frames) == 7             # seqs 0..6, 5 exactly once
