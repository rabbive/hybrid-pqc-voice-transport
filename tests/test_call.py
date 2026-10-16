import threading

import pytest

from hpqv import call, packet
from hpqv.udp import open_udp

from v2pair import rings


def test_end_to_end_voice_over_localhost():
    ring_a, ring_b = rings()
    ua, ub = open_udp(), open_udp()
    pcm = b"\x11\x22" * 960
    frames = [pcm] * 5
    out = {}
    rx = threading.Thread(target=lambda: out.__setitem__(
        "frames", call.recv_stream(ub, ring_b, expected=len(frames))))
    rx.start()
    sent = call.send_stream(ua, ub.getsockname(), ring_a, frames)
    rx.join()
    ua.close()
    ub.close()
    assert sent == len(frames)
    assert len(out["frames"]) == len(frames)
    assert all(len(f) == len(pcm) for f in out["frames"])


def test_forged_datagrams_are_counted_and_do_not_end_the_stream_early():
    ring_a, ring_b = rings()
    ua, ub = open_udp(), open_udp()
    pcm = b"\x11\x22" * 960
    junk = b"\x00" * 5
    out = {}
    rx = threading.Thread(target=lambda: out.__setitem__(
        "frames", call.recv_stream(ub, ring_b, expected=3)))
    rx.start()
    ua.sendto(junk, ub.getsockname())
    ua.sendto(b"\x21" + b"\x00" * 40, ub.getsockname())          # right size, wrong key
    call.send_stream(ua, ub.getsockname(), ring_a, [pcm] * 3)
    rx.join()
    ua.close()
    ub.close()
    assert len(out["frames"]) == 3
    assert ring_b.rejects["truncated"] == 1
    assert sum(ring_b.rejects.values()) == 2


def test_the_two_directions_use_different_keys():
    ring_a, ring_b = rings()
    dg = ring_a.seal(0, b"x" * 20)
    with pytest.raises(packet.Reject) as e:
        ring_a.open_(dg)                      # a sender cannot open its own datagrams
    assert e.value.reason == "auth"
    assert ring_b.open_(dg)[2] == b"x" * 20
