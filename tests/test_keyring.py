"""Live key rotation must never weaken the AEAD.

The keyring keeps each unexpired generation and picks one by the header tag.
These tests pin that in-flight packets survive rotation, expired keys go away,
nonces never repeat, and only an in-sequence generation moves the ring.
"""
import pytest

from hpqv import handshake as h, keyring as kr, packet


def _pair():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)
    return h.finish(accept, kem, kem_pub, b_pub), sess_b


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _rings(clock=None):
    clock = clock or Clock()
    sa, sb = _pair()
    return kr.Keyring(sa, clock=clock), kr.Keyring(sb, clock=clock), clock


def test_parity_bit_follows_generation_and_peer_decrypts():
    a, b, _ = _rings()
    dg0 = a.seal(0, b"gen0")
    assert dg0[10] & 1 == 0
    assert b.open_(dg0) == (0, 0, b"gen0")
    assert a.rotate_next() == 1 and b.rotate_to(1)
    dg1 = a.seal(1, b"gen1")
    assert dg1[10] & 1 == 1
    assert b.open_(dg1)[2] == b"gen1"
    assert a.fingerprint() == b.fingerprint()
    assert len(a.fingerprint()) == 8


def test_in_flight_old_generation_packet_decrypts_inside_window():
    a, b, clock = _rings()
    in_flight = a.seal(7, b"sent before rotate")
    a.rotate_next()
    b.rotate_to(1)
    clock.t += kr.RETIRE_AFTER - 0.1
    assert b.open_(in_flight)[2] == b"sent before rotate"


def test_peer_rotation_keeps_every_live_generation_decryptable():
    a, b, _ = _rings()
    b_clock = Clock()
    b._clock = b_clock
    gen0 = a.seal(1, b"gen0")
    a.rotate_next()
    assert b.rotate_to(1)
    gen1 = a.seal(2, b"gen1")

    # The sender's own window has elapsed, but the receiver's has not.
    a._clock.t += kr.RETIRE_AFTER
    a.rotate_next()
    gen2 = a.seal(3, b"gen2")
    assert b.rotate_to(2)

    assert [dg[10] & 0x3 for dg in (gen0, gen1, gen2)] == [0, 1, 2]
    assert b.open_(gen0)[2] == b"gen0"
    assert b.open_(gen1)[2] == b"gen1"
    assert b.open_(gen2)[2] == b"gen2"


def test_rotation_ready_tracks_previous_key_retirement():
    ring, _, clock = _rings()
    assert ring.rotation_ready()
    ring.rotate_next()
    assert not ring.rotation_ready()
    assert ring.rotate_next() == 1
    assert ring.generation == 1
    clock.t += kr.RETIRE_AFTER - 0.01
    assert not ring.rotation_ready()
    clock.t += 0.01
    assert ring.rotation_ready()


def test_old_generation_packet_fails_after_retirement():
    a, b, clock = _rings()
    in_flight = a.seal(7, b"old")
    a.rotate_next()
    b.rotate_to(1)
    clock.t += kr.RETIRE_AFTER + 0.01
    with pytest.raises(Exception):
        b.open_(in_flight)


def test_replay_two_generations_later_fails_despite_matching_parity():
    a, b, clock = _rings()
    old = a.seal(3, b"gen0")
    for gen in (1, 2):
        clock.t += kr.RETIRE_AFTER
        a.rotate_next()
        b.rotate_to(gen)
    assert old[10] & 1 == b.generation & 1     # same parity, different key
    with pytest.raises(Exception):
        b.open_(old)


def test_decrypt_tries_only_the_key_the_generation_tag_names(monkeypatch):
    a, b, _ = _rings()
    a.rotate_next()
    b.rotate_to(1)
    dg = bytearray(a.seal(5, b"gen1"))
    dg[10] ^= 1                                  # claim gen 0: must not fall back to gen 1
    calls = []
    real = packet.open_
    monkeypatch.setattr(kr.packet, "open_",
                        lambda *args: calls.append(args) or real(*args))
    with pytest.raises(Exception):
        b.open_(bytes(dg))
    assert len(calls) == 1


def test_key_nonce_pair_never_repeats_across_generations(monkeypatch):
    a, b, clock = _rings()
    used = []
    real = packet.seal
    monkeypatch.setattr(kr.packet, "seal", lambda key, prefix, seq, frame, flags=0:
                        used.append((key, prefix + seq.to_bytes(8, "big")))
                        or real(key, prefix, seq, frame, flags))
    for gen in range(4):
        for seq in range(5):                     # same seqs every gen, both directions
            a.seal(seq, b"x")
            b.seal(seq, b"y")
        clock.t += kr.RETIRE_AFTER
        a.rotate_next()
        b.rotate_to(gen + 1)
    assert len(used) == 40
    assert len(set(used)) == len(used)


def test_out_of_sequence_rotate_is_ignored():
    a, b, _ = _rings()
    before = b.fingerprint()
    for bogus in (0, 2, 7, 2**32 - 1):
        assert not b.rotate_to(bogus)
    assert b.generation == 0 and b.fingerprint() == before
    assert b.open_(a.seal(0, b"still gen0"))[2] == b"still gen0"


def test_simultaneous_rotate_lands_both_on_next_generation():
    a, b, _ = _rings()
    assert a.rotate_next() == 1 and b.rotate_next() == 1
    assert not a.rotate_to(1) and not b.rotate_to(1)   # each side's ROTATE(1) crosses
    assert a.generation == b.generation == 1
    assert a.fingerprint() == b.fingerprint()
    assert b.open_(a.seal(9, b"agreed"))[2] == b"agreed"
    assert a.open_(b.seal(9, b"agreed"))[2] == b"agreed"


def test_loops_accept_a_plain_session_tuple():
    sa, _ = _pair()
    ring = kr.Keyring.of(sa)
    assert kr.Keyring.of(ring) is ring
    assert ring.generation == 0
