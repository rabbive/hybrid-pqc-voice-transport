"""Rotate, hang-up and failure handling over the authenticated control channel.

Unit tests drive two CallHandles over a real TLS pair (secure.local_pair); the
end-to-end tests run two full peers (UDP voice from WAV files) in one process.
"""
import math
import socket
import struct
import threading
import time
import wave

import pytest

from hpqv import control, demo
from hpqv.audio import FS
from hpqv.callctl import CallHandle
from hpqv.demo import Stats

from v2pair import channels, rings

BOUND = 3.0          # every thread must be gone within this many seconds


def _wait(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _handles():
    client, server = channels()
    peers = []
    for ch in (client, server):
        handle, stats, stop = CallHandle(), Stats(), threading.Event()
        handle.start(ch, stats, stop)
        peers.append((handle, stats, stop, ch))
    return peers


def _close(peers):
    for handle, _stats, stop, ch in peers:
        stop.set()
        handle.finish()
        ch.close()


def test_unknown_message_type_after_authentication_ends_the_call():
    peers = _handles()
    (ha, _, stop_a, ch_a), (hb, _, stop_b, ch_b) = peers
    try:
        ch_b.send(99, b"unknown type")
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.protocol_error and not ha.peer_ended and not ha.connection_lost
        # the other side learns it through the CALL_END A sends while finishing
        ha.finish()
        assert _wait(stop_b.is_set, timeout=BOUND)
    finally:
        _close(peers)


@pytest.mark.parametrize("payload", [b"\x01", b"\x00" * 8, b""])
def test_malformed_rotate_payload_ends_the_call(payload):
    peers = _handles()
    (ha, _, stop_a, _), (_, _, _, ch_b) = peers
    try:
        ch_b.send(control.MSG_KEY_ROTATE, payload)
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.protocol_error and ha.key_generation() == 0
    finally:
        _close(peers)


def test_call_end_with_a_payload_is_malformed():
    peers = _handles()
    (ha, _, stop_a, _), (_, _, _, ch_b) = peers
    try:
        ch_b.send(control.MSG_CALL_END, b"x")
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.protocol_error and not ha.peer_ended
    finally:
        _close(peers)


def test_rotate_to_a_generation_beyond_next_ends_the_call():
    peers = _handles()
    (ha, _, stop_a, _), (_, _, _, ch_b) = peers
    try:
        ch_b.send(control.MSG_KEY_ROTATE, struct.pack(">I", 5))
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.protocol_error and ha.key_generation() == 0
    finally:
        _close(peers)


def test_stale_or_crossing_rotate_is_a_harmless_no_op():
    peers = _handles()
    (ha, _, stop_a, _), (hb, _, _, ch_b) = peers
    try:
        ch_b.send(control.MSG_KEY_ROTATE, struct.pack(">I", 0))      # not ahead of us
        ch_b.send(control.MSG_KEY_ROTATE, struct.pack(">I", 1))
        assert _wait(lambda: ha.key_generation() == 1)
        ch_b.send(control.MSG_KEY_ROTATE, struct.pack(">I", 1))      # crossing duplicate
        time.sleep(0.3)
        assert ha.key_generation() == 1 and not stop_a.is_set()
        assert not ha.protocol_error
    finally:
        _close(peers)


def test_rotate_reaches_peer_and_mirrors_into_stats():
    peers = _handles()
    (ha, sa, _, _), (hb, sb, _, _) = peers
    try:
        assert ha.rotate_keys() == 1
        assert _wait(lambda: hb.key_generation() == 1)
        assert sa.key_gen == sb.key_gen == 1
        assert ha.sas() == hb.sas() != ""
    finally:
        _close(peers)


def test_sas_is_the_channels_and_empty_before_start():
    assert CallHandle().sas() == ""
    peers = _handles()
    (ha, _, _, ch_a), (hb, _, _, ch_b) = peers
    try:
        assert ha.sas() == ch_a.sas() == ch_b.sas() == hb.sas()
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
        assert ha.key_generation() == hb.key_generation() in (1, 2)
        assert not ha.protocol_error and not hb.protocol_error
    finally:
        _close(peers)


def test_hang_up_ends_the_peer_as_peer_ended_not_connection_lost():
    peers = _handles()
    (ha, _, stop_a, _), (hb, _, stop_b, _) = peers
    try:
        hb.hang_up()
        assert stop_b.is_set()
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.peer_ended and not ha.connection_lost and not ha.protocol_error
        assert not hb.peer_ended
    finally:
        _close(peers)


def test_tcp_close_is_connection_lost_not_peer_ended():
    peers = _handles()
    (ha, _, stop_a, _), (_, _, _, ch_b) = peers
    try:
        ch_b.close()
        assert _wait(stop_a.is_set, timeout=1.0)
        assert ha.connection_lost and not ha.peer_ended
    finally:
        _close(peers)


def test_hang_up_before_the_call_starts_is_remembered():
    handle, stats, stop = CallHandle(), Stats(), threading.Event()
    handle.hang_up()
    client, server = channels()
    try:
        handle.start(client, stats, stop)
        assert stop.is_set()
    finally:
        stop.set()
        handle.finish()
        client.close()
        server.close()


def _write_tone(path, seconds):
    n = int(FS * seconds)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / FS)))
                   for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm)


class _Tap:
    """A UDP socket that remembers every datagram it sends (what an eavesdropper sees)."""

    def __init__(self, sock):
        self._sock, self.sent = sock, []

    def sendto(self, data, addr):
        self.sent.append(bytes(data))
        return self._sock.sendto(data, addr)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class _Live:
    """Two full peers in one process: real TLS pair, UDP voice from a WAV, WAV out."""

    def __init__(self, tmp_path, seconds=8, tap_listener=False, **overrides):
        self.wav = tmp_path / "tone.wav"
        _write_tone(self.wav, seconds)
        self.channels = dict(zip(("caller", "listener"), channels()))
        self.udp = {}
        for role in self.channels:
            self.udp[role] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp[role].bind(("127.0.0.1", 0))
        if tap_listener:
            self.udp["listener"] = _Tap(self.udp["listener"])
        self.peers = {}
        for role, other in (("caller", "listener"), ("listener", "caller")):
            args = demo.call_args(input="file", wav=str(self.wav), seconds=seconds,
                                  output="file", out_wav=str(tmp_path / f"{role}.wav"),
                                  **overrides)
            handle, stats, stop = CallHandle(), Stats(), threading.Event()
            thread = threading.Thread(target=demo._run_call, args=(
                self.channels[role], self.udp[role], self.udp[other].getsockname(), args),
                kwargs=dict(stats=stats, stop_event=stop, call_handle=handle))
            self.peers[role] = (handle, stats, stop, thread)

    def start(self):
        for *_, thread in self.peers.values():
            thread.start()
        return self.peers["caller"], self.peers["listener"]

    def close(self):
        for *_, stop, thread in self.peers.values():
            stop.set()
            thread.join(timeout=BOUND)
        for s in (*self.channels.values(), *self.udp.values()):
            s.close()


def test_live_call_rotates_mid_call_and_hang_up_ends_both(tmp_path):
    live = _Live(tmp_path)
    try:
        (hc, sc, stop_c, tc), (hl, sl, stop_l, tl) = live.start()
        assert _wait(lambda: sc.received > 20 and sl.received > 20, timeout=3)

        sas0 = hc.sas()
        assert hc.rotate_keys() == 1
        assert _wait(lambda: sc.key_gen == 1 and sl.key_gen == 1)
        assert hc.sas() == hl.sas() == sas0            # the SAS names the session, not the key generation
        rc, rl = sc.received, sl.received
        time.sleep(0.6)                  # ~30 frames each way, all under generation 1
        assert sc.received - rc > 15 and sl.received - rl > 15
        assert not stop_c.is_set() and not stop_l.is_set()

        hl.hang_up()
        t0 = time.monotonic()
        assert _wait(stop_c.is_set, timeout=1.0)
        assert time.monotonic() - t0 < 1.0
        tc.join(timeout=BOUND)
        tl.join(timeout=BOUND)
        assert not tc.is_alive() and not tl.is_alive()
        assert hc.peer_ended and not hc.connection_lost
        assert sum(sc.rejects.values()) == sum(sl.rejects.values()) == 0
    finally:
        live.close()


def _frames_in(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


def _call_to_the_end(tmp_path, attack=False):
    """A 3 s call, optionally with an attacker socket feeding the caller. Returns
    (caller stats, listener stats, caller wav frames, attack reasons injected)."""
    tmp_path.mkdir()
    live = _Live(tmp_path, seconds=3, tap_listener=True, loop=False)
    tap = live.udp["listener"]
    attacker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    injected = {}
    try:
        (hc, sc, _, tc), (hl, sl, _, tl) = live.start()
        if attack:
            victim = live.udp["caller"].getsockname()
            assert _wait(lambda: sc.received > 20, timeout=3)
            genuine = tap.sent[0]                       # captured off the wire, already delivered
            bent = bytearray(tap.sent[-1])
            bent[8] ^= 0x10                             # one bit of seq: moves it ahead of the window
            _, foreign_ring = rings()
            injected = {
                "replay": genuine,
                "auth": bytes(bent),
                "wrong_session": foreign_ring.seal(0, b"z" * 40),   # another session's keys
                "oversize": genuine + b"\x00" * (1300 - len(genuine)),
                "truncated": b"\x21\x00\x00\x00\x00",
            }
            before = sc.rejects
            for dg in injected.values():
                attacker.sendto(dg, victim)
            assert _wait(lambda: sum(sc.rejects.values()) - sum(before.values()) >= len(injected))
            for reason in injected:
                assert sc.rejects[reason] - before[reason] == 1, (reason, sc.rejects, before)
        tc.join(timeout=3 + BOUND)
        tl.join(timeout=3 + BOUND)
        assert not tc.is_alive() and not tl.is_alive()
        return sc, sl, _frames_in(tmp_path / "caller.wav"), injected
    finally:
        attacker.close()
        live.close()


def test_attacker_datagrams_are_rejected_counted_and_do_not_disturb_the_call(tmp_path):
    clean_c, clean_l, clean_frames, _ = _call_to_the_end(tmp_path / "clean")
    sc, sl, frames, injected = _call_to_the_end(tmp_path / "attacked", attack=True)
    assert len(injected) == 5
    # each reason counted exactly once (checked live), nothing else was rejected
    assert sum(sc.rejects.values()) == 5 and sum(sl.rejects.values()) == 0
    assert sum(clean_c.rejects.values()) == 0
    # none of the five reached received, the meter or the audio: same as the clean run
    assert sc.received == clean_c.received == sl.sent - sl.dropped
    assert sc.rx_expected == clean_c.rx_expected
    assert frames == clean_frames


def test_abrupt_tcp_close_ends_both_sides_without_deadlock(tmp_path):
    live = _Live(tmp_path)
    try:
        (hc, sc, stop_c, tc), (hl, sl, stop_l, tl) = live.start()
        assert _wait(lambda: sc.received > 10 and sl.received > 10, timeout=3)
        live.channels["listener"]._sock.close()          # the raw socket, under the TLS layer
        assert _wait(lambda: stop_c.is_set() and stop_l.is_set(), timeout=BOUND)
        tc.join(timeout=BOUND)
        tl.join(timeout=BOUND)
        assert not tc.is_alive() and not tl.is_alive()
        assert hc.connection_lost and not hc.peer_ended
        assert hl.connection_lost
    finally:
        live.close()


def test_authenticated_unknown_message_mid_call_ends_both_peers(tmp_path):
    live = _Live(tmp_path)
    try:
        (hc, sc, stop_c, tc), (hl, sl, stop_l, tl) = live.start()
        assert _wait(lambda: sc.received > 10 and sl.received > 10, timeout=3)
        live.channels["listener"].send(99, b"not a message")
        t0 = time.monotonic()
        tc.join(timeout=BOUND)
        tl.join(timeout=BOUND)
        assert not tc.is_alive() and not tl.is_alive()
        assert time.monotonic() - t0 < 2 * BOUND
        assert hc.protocol_error and stop_c.is_set() and stop_l.is_set()
    finally:
        live.close()
