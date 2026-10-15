"""The GUI's loss slider is only meaningful if the sender re-reads it mid-call.

Before LossControl existed, drop_pct was a fixed value captured at call setup,
so changing loss meant hanging up and dialling again — the demo could not show
degradation and recovery in one continuous call.
"""
import socket
import threading

from hpqv import handshake as h
from hpqv.demo import LossControl, Stats, _sender_loop

FRAME = b"\x00\x01" * 960          # one 20 ms frame of PCM


class _Source:
    """Yields `count` frames, flipping the loss control partway through."""

    def __init__(self, count, loss, flip_at, flip_to):
        self.count, self.loss = count, loss
        self.flip_at, self.flip_to = flip_at, flip_to

    def frames(self):
        for i in range(self.count):
            if i == self.flip_at:
                self.loss.pct = self.flip_to
            yield FRAME


def _session():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, b_sec)
    return h.finish(accept, kem, kem_pub, b_pub)


def test_loss_control_clamps_to_valid_range():
    loss = LossControl(0)
    loss.pct = -10
    assert loss.pct == 0
    loss.pct = 250
    assert loss.pct == 100


def test_sender_honours_a_mid_call_loss_change():
    loss = LossControl(0.0)
    stats = Stats()
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Drain the receiver so the socket buffer cannot fill and block the sender.
    stop = threading.Event()
    sink.settimeout(0.2)

    def drain():
        while not stop.is_set():
            try:
                sink.recvfrom(2048)
            except OSError:
                pass

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    try:
        # First half at 0% loss, second half at 100%: nothing should drop before
        # the flip, and nothing should be sent after it.
        source = _Source(count=100, loss=loss, flip_at=50, flip_to=100)
        _sender_loop(tx, sink.getsockname(), _session(), source, loss, stats,
                     threading.Event())
    finally:
        stop.set()
        tx.close()
        sink.close()

    sent, _received, dropped, _recovered = stats.snapshot()
    assert sent == 50, f"expected 50 sent before the flip, got {sent}"
    assert dropped == 50, f"expected 50 dropped after the flip, got {dropped}"


def test_sender_encodes_every_frame_and_drops_only_the_datagram(monkeypatch):
    """A network loss happens after encoding: the encoder must still see the
    dropped frame, or the FEC in packet N+1 describes frame N-1, not N."""
    import hpqv.demo as demo
    from hpqv import packet

    encoded = []

    class SpyCodec(demo.OpusCodec):
        def encode(self, pcm):
            encoded.append(pcm)
            return super().encode(pcm)

    class FakeSock:
        def __init__(self):
            self.sent = []

        def sendto(self, dg, dest):
            self.sent.append(dg)

    class OneLossSource:
        def __init__(self, loss, drop_at, count):
            self.loss, self.drop_at, self.count = loss, drop_at, count

        def frames(self):
            for i in range(self.count):
                self.loss.pct = 100 if i == self.drop_at else 0
                yield FRAME

    monkeypatch.setattr(demo, "OpusCodec", SpyCodec)
    key, send_prefix, recv_prefix = _session()
    loss, stats, sock = LossControl(0.0), Stats(), FakeSock()
    _sender_loop(sock, ("127.0.0.1", 9), (key, send_prefix, recv_prefix),
                 OneLossSource(loss, drop_at=3, count=8), loss, stats,
                 threading.Event())

    assert len(encoded) == 8
    assert len(sock.sent) == 7
    assert [packet.open_(key, send_prefix, dg)[0] for dg in sock.sent] == [0, 1, 2, 4, 5, 6, 7]
    sent, _rx, dropped, _rec = stats.snapshot()
    assert (sent, dropped) == (7, 1)
