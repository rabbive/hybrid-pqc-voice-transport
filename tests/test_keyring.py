"""Keyring: per-direction v2 media keys, replay window and live rotation (ADR-0004).

Rotation must never weaken the AEAD: in-flight packets survive a rotation,
expired keys go away, (key, nonce) never repeats, only an in-sequence
generation moves the ring, and a rejected datagram changes nothing but the
reject counter.
"""
import os
import random
import time
from dataclasses import dataclass

import pytest

from hpqv import keyring as kr, packet
from hpqv.packet import Reject


@dataclass(frozen=True)
class Keys:                     # stand-in for secure.MediaKeys
    send_secret: bytes
    recv_secret: bytes
    session_id: bytes


def _keys_pair():
    c2s, s2c, sid = os.urandom(32), os.urandom(32), os.urandom(4)
    return Keys(c2s, s2c, sid), Keys(s2c, c2s, sid)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _rings(clock=None):
    clock = clock or Clock()
    ka, kb = _keys_pair()
    return kr.Keyring(ka, clock=clock), kr.Keyring(kb, clock=clock), clock


class AeadSpy:
    """Replaces packet.ChaCha20Poly1305 and records every (key, nonce) use."""

    def __init__(self, monkeypatch):
        real = packet.ChaCha20Poly1305
        spy = self
        self.encrypts, self.decrypts = [], []

        class Spy:
            def __init__(self, key):
                self._key, self._real = bytes(key), real(key)

            def encrypt(self, nonce, data, aad):
                spy.encrypts.append((self._key, bytes(nonce)))
                return self._real.encrypt(nonce, data, aad)

            def decrypt(self, nonce, data, aad):
                spy.decrypts.append((self._key, bytes(nonce)))
                return self._real.decrypt(nonce, data, aad)

        monkeypatch.setattr(packet, "ChaCha20Poly1305", Spy)


def _flip(dg, index, mask=1):
    out = bytearray(dg)
    out[index] ^= mask
    return bytes(out)


def _reason(ring, dg):
    with pytest.raises(Reject) as e:
        ring.open_(dg)
    return e.value.reason


# --- ported ADR-0004 rotation tests -------------------------------------

def test_generation_tag_follows_generation_and_peer_decrypts():
    a, b, _ = _rings()
    dg0 = a.seal(0, b"gen0")
    assert dg0[1] & packet.GEN_MASK == 0
    assert b.open_(dg0) == (0, 0, b"gen0")
    assert a.rotate_next() == 1 and b.rotate_to(1)
    dg1 = a.seal(1, b"gen1")
    assert dg1[1] & packet.GEN_MASK == 1
    assert b.open_(dg1) == (1, 1, b"gen1")
    assert a.open_(b.seal(0, b"back")) == (0, 1, b"back")


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

    assert [dg[1] & packet.GEN_MASK for dg in (gen0, gen1, gen2)] == [0, 1, 2]
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
    assert _reason(b, in_flight) == "no_key"


def test_replay_two_generations_later_fails_despite_matching_parity():
    a, b, clock = _rings()
    old = a.seal(3, b"gen0")
    for gen in (1, 2):
        clock.t += kr.RETIRE_AFTER
        a.rotate_next()
        b.rotate_to(gen)
    assert old[1] & 1 == b.generation & 1     # same parity, different key
    assert _reason(b, old) == "no_key"


def test_decrypt_tries_only_the_key_the_generation_tag_names(monkeypatch):
    a, b, _ = _rings()
    a.rotate_next()
    b.rotate_to(1)
    dg = _flip(a.seal(5, b"gen1"), 1, 0x01)    # claim gen 0: must not fall back to gen 1
    spy = AeadSpy(monkeypatch)
    assert _reason(b, dg) == "auth"
    assert len(spy.decrypts) == 1


def test_key_nonce_pair_never_repeats_across_generations_and_directions(monkeypatch):
    a, b, clock = _rings()
    spy = AeadSpy(monkeypatch)
    seq = 0
    for gen in range(6):
        for _ in range(50):
            a.seal(seq, b"x")
            b.seal(seq, b"y")                  # same seq both directions
            seq += 1
        clock.t += kr.RETIRE_AFTER
        a.rotate_next()
        b.rotate_to(gen + 1)
    assert len(spy.encrypts) == 600
    assert len(set(spy.encrypts)) == 600
    assert len({key for key, _ in spy.encrypts}) == 12   # 6 generations x 2 directions


def test_seal_refuses_a_seq_it_already_used():
    ring, _, clock = _rings()
    ring.seal(5, b"x")
    for seq in (5, 4, 0):
        with pytest.raises(ValueError):
            ring.seal(seq, b"x")
    clock.t += kr.RETIRE_AFTER
    ring.rotate_next()                         # seq keeps counting across rotations
    with pytest.raises(ValueError):
        ring.seal(5, b"x")
    ring.seal(6, b"x")


def test_out_of_sequence_rotate_is_ignored():
    a, b, _ = _rings()
    for bogus in (0, 2, 7, 2**32 - 1, -1):
        assert not b.rotate_to(bogus)
    assert b.generation == 0
    assert b.open_(a.seal(0, b"still gen0"))[2] == b"still gen0"


def test_simultaneous_rotate_lands_both_on_next_generation():
    a, b, _ = _rings()
    assert a.rotate_next() == 1 and b.rotate_next() == 1
    assert not a.rotate_to(1) and not b.rotate_to(1)   # each side's ROTATE(1) crosses
    assert a.generation == b.generation == 1
    assert b.open_(a.seal(9, b"agreed"))[2] == b"agreed"
    assert a.open_(b.seal(9, b"agreed"))[2] == b"agreed"


def test_rotation_advances_both_directions_with_next_secret():
    ka, kb = _keys_pair()
    a, b = kr.Keyring(ka), kr.Keyring(kb)
    a.rotate_next()
    b.rotate_to(1)
    nxt = packet.direction_keys(packet.next_secret(ka.send_secret))
    assert packet.open_(*nxt, a.seal(0, b"a->b")) == (0, 1, b"a->b")
    nxt = packet.direction_keys(packet.next_secret(kb.send_secret))
    assert packet.open_(*nxt, b.seal(0, b"b->a")) == (0, 1, b"b->a")
    old = packet.direction_keys(ka.send_secret)
    with pytest.raises(Reject):
        packet.open_(*old, a.seal(1, b"not under gen 0"))


def test_rapid_peer_rotations_reuse_the_tag_slot_and_keep_current_decryptable():
    a, b, _ = _rings()
    a._clock = a_clock = Clock()               # receiver's clock never moves: all gens live
    for gen in range(1, 6):
        a_clock.t += kr.RETIRE_AFTER
        assert a.rotate_next() == gen
        assert b.rotate_to(gen)
    assert b.generation == 5 and not b.rotation_ready()
    assert b.open_(a.seal(1, b"gen5"))[2] == b"gen5"


def test_extra_flags_carry_fec_and_never_override_generation():
    a, b, _ = _rings()
    a.rotate_next()
    b.rotate_to(1)
    dg = a.seal(0, b"x", extra_flags=0x07)
    assert dg[1] == 0x05                       # generation 1 | FEC
    assert b.open_(dg) == (0, 0x05, b"x")
    with pytest.raises(ValueError):
        a.seal(1, b"x", extra_flags=0x08)      # reserved bit


def test_keyring_of_is_gone():
    assert not hasattr(kr.Keyring, "of")


def test_constructor_validates_key_lengths():
    ka, _ = _keys_pair()
    for bad in (Keys(ka.send_secret[:31], ka.recv_secret, ka.session_id),
                Keys(ka.send_secret, ka.recv_secret + b"x", ka.session_id),
                Keys(ka.send_secret, ka.recv_secret, b"abc")):
        with pytest.raises(ValueError):
            kr.Keyring(bad)


# --- directional separation and session binding --------------------------

def test_reflected_datagram_is_rejected():
    a, b, _ = _rings()
    dg = a.seal(0, b"to b")
    assert _reason(a, dg) == "auth"            # A cannot open its own datagram
    assert b.open_(dg)[2] == b"to b"


def test_directions_use_different_keys(monkeypatch):
    a, b, _ = _rings()
    spy = AeadSpy(monkeypatch)
    a.seal(0, b"same")
    b.seal(0, b"same")
    (ka, na), (kb, nb) = spy.encrypts
    assert ka != kb and na != nb


def test_previous_session_datagram_is_rejected():
    ka, kb = _keys_pair()
    a, b = kr.Keyring(ka), kr.Keyring(kb)
    old_a, _, _ = _rings()
    assert _reason(b, old_a.seal(0, b"stale")) == "wrong_session"
    # Same session id but other secrets: the AEAD catches it.
    forged = kr.Keyring(Keys(os.urandom(32), os.urandom(32), kb.session_id))
    assert _reason(b, forged.seal(0, b"stale")) == "auth"
    assert b.open_(a.seal(0, b"live"))[2] == b"live"


# --- every reject reason, through the keyring ----------------------------

def _bad_datagrams(a, b):
    """One crafted datagram per reject reason, as received by b."""
    good = a.seal(500, b"opus")
    other, _, _ = _rings()
    replayed = a.seal(501, b"once")
    assert b.open_(replayed)
    return {
        "oversize": good + bytes(packet.MAX_DATAGRAM + 1 - len(good)),
        "truncated": good[:packet.MIN_DATAGRAM - 1],
        "malformed": bytes([0x11]) + good[1:],
        "malformed_reserved": _flip(good, 1, 0x80),
        "wrong_session": other.seal(500, b"opus"),
        "no_key": _flip(good, 1, 0x02),
        "replay": replayed,
        "auth_ciphertext": _flip(good, 12),
        "auth_tag": _flip(good, len(good) - 1),
        "auth_fec_flag": _flip(good, 1, packet.FLAG_FEC),
        "auth_seq": _flip(good, 6),             # forward jump: passes replay check
    }


def test_every_reject_reason_is_counted():
    a, b, _ = _rings()
    bad = _bad_datagrams(a, b)
    before = b.rejects
    assert set(before) == set(packet.REJECT_REASONS) and not any(before.values())
    for name, dg in bad.items():
        expected = name if name in packet.REJECT_REASONS else name.split("_")[0]
        assert _reason(b, dg) == expected, name


def test_reject_counts_are_exact_and_snapshots_are_copies():
    a, b, _ = _rings()
    bad = _bad_datagrams(a, b)
    for dg in bad.values():
        with pytest.raises(Reject):
            b.open_(dg)
    snap = b.rejects
    assert snap == {"oversize": 1, "truncated": 1, "malformed": 2, "wrong_session": 1,
                    "no_key": 1, "replay": 1, "auth": 4}
    snap["auth"] = 0
    assert b.rejects["auth"] == 4


def test_generation_tag_auth_when_both_generations_are_live():
    a, b, _ = _rings()
    a.rotate_next()
    b.rotate_to(1)
    dg = a.seal(3, b"gen1")
    assert _reason(b, _flip(dg, 1, 0x01)) == "auth"     # names live gen 0
    assert _reason(b, _flip(dg, 1, 0x02)) == "no_key"   # names gen 3
    assert b.open_(dg)[2] == b"gen1"


def test_rejected_datagrams_change_nothing_but_the_counter():
    a, b, _ = _rings()
    a.rotate_next()
    b.rotate_to(1)
    sealed = [a.seal(s, b"v%d" % s) for s in range(400)]
    assert b.open_(sealed[10])[0] == 10
    # Forgery claiming a far-ahead seq: must not slide the window.
    forged = bytearray(sealed[399])
    forged[6:12] = (10**9).to_bytes(6, "big")
    attacks = [bytes(forged), _flip(sealed[20], 12), _flip(sealed[20], 1, 0x01),
               _flip(sealed[20], 1, 0x02), sealed[10], b"\x21" * 3, b"\x21" * 1300]
    for dg in attacks:
        with pytest.raises(Reject):
            b.open_(dg)
    assert b.generation == 1 and not b.rotation_ready()
    assert sum(b.rejects.values()) == len(attacks)
    # seq 20 and the 10..265 range are still fresh: the window did not move,
    # and the gen-1 key still decrypts.
    assert b.open_(sealed[20])[2] == b"v20"
    assert b.open_(sealed[11])[2] == b"v11"


def test_cheap_rejections_never_reach_the_aead(monkeypatch):
    a, b, _ = _rings()
    bad = _bad_datagrams(a, b)
    spy = AeadSpy(monkeypatch)
    for name in ("oversize", "truncated", "malformed", "malformed_reserved",
                 "wrong_session", "no_key", "replay"):
        with pytest.raises(Reject):
            b.open_(bad[name])
    assert spy.decrypts == []


# --- replay across the ring -----------------------------------------------

def test_one_replay_window_covers_every_generation():
    ka, kb = _keys_pair()
    b = kr.Keyring(kb)
    key0, iv0 = packet.direction_keys(ka.send_secret)
    assert b.open_(packet.seal(key0, iv0, ka.session_id, 5, b"g0"))[0] == 5
    assert b.rotate_to(1)
    key1, iv1 = packet.direction_keys(packet.next_secret(ka.send_secret))
    # A valid gen-1 datagram reusing seq 5 is a replay even under a new key.
    assert _reason(b, packet.seal(key1, iv1, ka.session_id, 5, b"g1", flags=1)) == "replay"
    assert b.open_(packet.seal(key1, iv1, ka.session_id, 6, b"g1", flags=1))[0] == 6


def test_reordered_block_accepted_once():
    a, b, _ = _rings()
    sealed = [a.seal(s, b"x") for s in range(200)]
    order = list(range(200))
    random.Random(1).shuffle(order)
    assert [b.open_(sealed[i])[0] for i in order] == order
    for i in order[:50]:
        assert _reason(b, sealed[i]) == "replay"


def test_random_garbage_is_rejected_quickly():
    ka, kb = _keys_pair()
    a, b = kr.Keyring(ka), kr.Keyring(kb)
    rng = random.Random(1234)
    sid = kb.session_id                        # half the garbage carries a plausible header
    start = time.perf_counter()
    for i in range(1000):
        size = rng.randrange(0, 1400)
        dg = bytearray(rng.randbytes(size))
        if i % 2 and size >= 12:
            dg[0], dg[2:6] = 0x21, sid
            dg[1] &= 0x03
        with pytest.raises(Reject):
            b.open_(bytes(dg))
    assert time.perf_counter() - start < 2.0
    assert sum(b.rejects.values()) == 1000
    assert b.open_(a.seal(0, b"still fine"))[2] == b"still fine"
