"""Live FEC: the receiver must call decode_fec when a gap appears.

The encoder always enabled in-band FEC, but the receive path only ever did a
normal decode, so a lost frame was skipped and the redundancy was never used.
"""
import socket
import threading

import numpy as np
import opuslib
import pytest

from hpqv import handshake as h, packet
from hpqv.audio import FRAME_SAMPLES, OpusCodec
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
    """opuslib calls the variadic opus_encoder_ctl through ctypes; on Apple arm64
    that ABI mismatch makes every CTL fail, so in-band FEC is silently off there."""
    try:
        opuslib.api.encoder.encoder_ctl(
            OpusCodec().enc.encoder_state, opuslib.api.ctl.get_inband_fec)
    except opuslib.OpusError:
        return False
    return True


@pytest.mark.skipif(not _opus_ctls_work(), reason="opus CTLs unusable on this platform")
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


def test_huge_gap_is_not_synthesized():
    packets = _packets(MAX_GAP + 10)
    pl = Playout(OpusCodec())
    assert len(pl.frames(0, packets[0])) == 1
    assert len(pl.frames(MAX_GAP + 5, packets[MAX_GAP + 5])) == 1
    assert (pl.recovered, pl.concealed) == (0, 0)
    assert len(pl.frames(MAX_GAP + 6, packets[MAX_GAP + 6])) == 1   # resynced


def test_receiver_loop_recovers_a_dropped_datagram():
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
        target=_receiver_loop, args=(rx, session, sink, 3, stats, stop))
    t.start()
    try:
        for seq, opus in enumerate(_packets(12)):
            if seq != 5:
                tx.sendto(packet.seal(key, send_prefix, seq, opus),
                          rx.getsockname())
        for _ in range(100):
            if stats.snapshot()[1] >= 11:
                break
            threading.Event().wait(0.05)
    finally:
        stop.set()
        t.join(timeout=3)
        tx.close()
        rx.close()
    assert stats.snapshot()[3] == 1
    assert len(sink.frames) == 12
