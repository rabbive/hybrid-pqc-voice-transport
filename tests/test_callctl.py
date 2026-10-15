"""Rotate and hang-up travel over the TCP control channel while audio flows.

The unit tests drive two CallHandles over a socketpair; the end-to-end test
runs two full peers (TCP handshake + UDP voice from WAV files) in one process.
"""
import math
import socket
import struct
import threading
import time
import wave

from hpqv import control, demo, handshake as h
from hpqv.audio import FS
from hpqv.callctl import CallHandle
from hpqv.demo import Stats


def _pair():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)
    return h.finish(accept, kem, kem_pub, b_pub), sess_b


def _wait(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _handles():
    sa, sb = _pair()
    ta, tb = socket.socketpair()
    peers = []
    for sock, sess in ((ta, sa), (tb, sb)):
        handle, stats, stop = CallHandle(), Stats(), threading.Event()
        handle.start(sock, sess, stats, stop)
        peers.append((handle, stats, stop, sock))
    return peers


def _close(peers):
    for handle, _stats, stop, sock in peers:
        stop.set()
        handle.finish()
        sock.close()


def test_rotate_reaches_peer_and_mirrors_into_stats():
    peers = _handles()
    (ha, sa, _, _), (hb, sb, _, _) = peers
    try:
        assert ha.rotate_keys() == 1
        assert _wait(lambda: hb.key_generation() == 1)
        assert sa.key_gen == sb.key_gen == 1
        assert ha.key_fingerprint() == hb.key_fingerprint()
    finally:
        _close(peers)


def test_local_rotate_is_refused_until_previous_key_retires():
    from hpqv import keyring

    peers = _handles()
    (ha, _, _, _), (hb, _, _, _) = peers

    class Clock:
        t = 100.0

        def __call__(self):
            return self.t

    clock = Clock()
    ha._ring._clock = clock
    try:
        assert ha.rotate_keys() == 1
        assert _wait(lambda: hb.key_generation() == 1)
        assert not ha.rotation_ready()
        assert ha.rotate_keys() == 1
        assert _wait(lambda: hb.key_generation() == 1)
        clock.t += keyring.RETIRE_AFTER
        assert ha.rotation_ready()
    finally:
        _close(peers)


def test_simultaneous_rotate_from_both_sides_rotates_once():
    peers = _handles()
    (ha, _, _, _), (hb, _, _, _) = peers
    try:
        go = threading.Barrier(2)
        threads = [threading.Thread(target=lambda hd=hd: (go.wait(), hd.rotate_keys()))
                   for hd in (ha, hb)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.3)                  # let both crossing ROTATE(1)s arrive
        # Truly simultaneous -> both at 1. If one ROTATE lands before the other
        # side's own rotate_keys() ran, that side legitimately goes on to 2.
        # Either way the peers must agree.
        assert ha.key_generation() == hb.key_generation() in (1, 2)
        assert ha.key_fingerprint() == hb.key_fingerprint()
    finally:
        _close(peers)


def test_garbage_or_out_of_sequence_rotate_is_ignored():
    peers = _handles()
    (ha, _, stop_a, sock_a), (hb, _, _, sock_b) = peers
    try:
        before = ha.key_fingerprint()
        control.send_control(sock_b, control.MSG_KEY_ROTATE, b"\x01")           # short
        control.send_control(sock_b, control.MSG_KEY_ROTATE, struct.pack(">I", 5))
        control.send_control(sock_b, control.MSG_KEY_ROTATE, struct.pack(">Q", 1))  # long
        control.send_control(sock_b, 99, b"unknown type")
        time.sleep(0.3)
        assert ha.key_generation() == 0 and ha.key_fingerprint() == before
        assert not stop_a.is_set()
        control.send_control(sock_b, control.MSG_KEY_ROTATE, struct.pack(">I", 1))
        assert _wait(lambda: ha.key_generation() == 1)
    finally:
        _close(peers)


def test_hang_up_ends_the_peer():
    peers = _handles()
    (ha, _, stop_a, _), (hb, _, stop_b, _) = peers
    try:
        hb.hang_up()
        assert stop_b.is_set()
        assert _wait(lambda: stop_a.is_set(), timeout=1.0)
    finally:
        _close(peers)


def test_tcp_close_ends_the_call():
    peers = _handles()
    (_, _, stop_a, _), (_, _, _, sock_b) = peers
    try:
        sock_b.close()
        assert _wait(lambda: stop_a.is_set(), timeout=1.0)
    finally:
        _close(peers)


def _write_tone(path, seconds):
    n = int(FS * seconds)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / FS)))
                   for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm)


def test_live_call_rotates_mid_call_and_hang_up_ends_both(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_tone(wav, 8)
    srv = socket.create_server(("127.0.0.1", 0))
    caller_tcp = socket.create_connection(srv.getsockname())
    listener_tcp, _ = srv.accept()
    srv.close()

    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    sessions = {}
    t = threading.Thread(target=lambda: sessions.__setitem__(
        "listener", control.run_responder(listener_tcp, b_pub, b_sec, a_pub)))
    t.start()
    sessions["caller"] = control.run_initiator(caller_tcp, a_pub, a_sec, b_pub)
    t.join()

    udp = {}
    for role in ("caller", "listener"):
        udp[role] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp[role].bind(("127.0.0.1", 0))
    peers = {}
    for role, tcp, other in (("caller", caller_tcp, "listener"),
                             ("listener", listener_tcp, "caller")):
        args = demo.call_args(input="file", wav=str(wav), seconds=8, output="file",
                              out_wav=str(tmp_path / f"{role}.wav"))
        handle, stats, stop = CallHandle(), Stats(), threading.Event()
        thread = threading.Thread(target=demo._run_call, args=(
            tcp, udp[role], udp[other].getsockname(), sessions[role], args),
            kwargs=dict(stats=stats, stop_event=stop, call_handle=handle))
        peers[role] = (handle, stats, stop, thread)
    try:
        for *_, thread in peers.values():
            thread.start()
        (hc, sc, stop_c, tc), (hl, sl, stop_l, tl) = peers["caller"], peers["listener"]
        assert _wait(lambda: sc.received > 20 and sl.received > 20, timeout=3)

        fp0 = hc.key_fingerprint()
        assert hc.rotate_keys() == 1
        assert _wait(lambda: sc.key_gen == 1 and sl.key_gen == 1)
        assert hc.key_fingerprint() == hl.key_fingerprint() != fp0
        rc, rl = sc.received, sl.received
        time.sleep(0.6)                  # ~30 frames each way, all under generation 1
        assert sc.received - rc > 15 and sl.received - rl > 15
        assert not stop_c.is_set() and not stop_l.is_set()

        hl.hang_up()
        t0 = time.monotonic()
        assert _wait(lambda: stop_c.is_set(), timeout=1.0)
        assert time.monotonic() - t0 < 1.0
        tc.join(timeout=3)
        tl.join(timeout=3)
        assert not tc.is_alive() and not tl.is_alive()
    finally:
        for *_, stop, thread in peers.values():
            stop.set()
            thread.join(timeout=3)
        for s in (caller_tcp, listener_tcp, *udp.values()):
            s.close()
