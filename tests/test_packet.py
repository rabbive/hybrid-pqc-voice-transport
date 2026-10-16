"""v2 voice datagram: byte layout, key schedule labels and every Reject reason.

Header (12 B, all AAD): ver_type(1) | flags(1) | session_id(4) | seq(uint48 BE).
nonce = iv XOR (0^6 || seq.to_bytes(6)).
"""
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

from hpqv import packet as p

SID = bytes.fromhex("aabbccdd")


def _keys():
    return p.direction_keys(os.urandom(32))


def _expand(secret, info, n):
    return HKDFExpand(hashes.SHA256(), n, info).derive(secret)


def test_constants_match_contract():
    assert (p.VERSION, p.TYPE_VOICE, p.VER_TYPE) == (2, 1, 0x21)
    assert (p.HEADER_LEN, p.TAG_LEN, p.MAX_DATAGRAM) == (12, 16, 1200)
    assert p.MIN_DATAGRAM == 29
    assert p.MAX_SEQ == 2**48 - 1
    assert (p.GEN_MASK, p.FLAG_FEC) == (0x03, 0x04)
    assert p.REJECT_REASONS == ("oversize", "truncated", "malformed", "wrong_session",
                                "no_key", "replay", "auth")


def test_key_schedule_uses_the_contract_hkdf_labels():
    secret = os.urandom(32)
    key, iv = p.direction_keys(secret)
    assert key == _expand(secret, b"hpqv2 media key", 32)
    assert iv == _expand(secret, b"hpqv2 media iv", 12)
    nxt = p.next_secret(secret)
    assert nxt == _expand(secret, b"hpqv2 media upd", 32)
    assert len(nxt) == 32 and nxt != secret
    assert p.direction_keys(nxt) != (key, iv)


def test_seal_open_round_trip():
    key, iv = _keys()
    dg = p.seal(key, iv, SID, 42, b"opusdata", flags=p.FLAG_FEC | 1)
    assert p.open_(key, iv, dg) == (42, p.FLAG_FEC | 1, b"opusdata")
    assert p.parse_header(dg) == (SID, 42, p.FLAG_FEC | 1)


def test_header_bytes_big_endian_seq_and_nonce_layout():
    key, iv = _keys()
    seq = 0x010203040506
    dg = p.seal(key, iv, SID, seq, b"frame", flags=0x05)
    assert dg[:12] == bytes.fromhex("21" "05" "aabbccdd" "010203040506")
    assert len(dg) == 12 + len(b"frame") + 16
    # Independent decrypt pins nonce = iv XOR seq (right-aligned) and AAD = header.
    nonce = bytes(a ^ b for a, b in zip(iv, bytes(6) + seq.to_bytes(6, "big")))
    assert ChaCha20Poly1305(key).decrypt(nonce, dg[12:], dg[:12]) == b"frame"


@pytest.mark.parametrize("seq", [0, 1, p.MAX_SEQ])
def test_seq_boundaries_round_trip(seq):
    key, iv = _keys()
    dg = p.seal(key, iv, SID, seq, b"x")
    assert p.open_(key, iv, dg)[0] == seq


def test_seal_refuses_bad_inputs():
    key, iv = _keys()
    for seq in (p.MAX_SEQ + 1, 2**64, -1):
        with pytest.raises(ValueError):
            p.seal(key, iv, SID, seq, b"x")
    largest = p.MAX_DATAGRAM - p.HEADER_LEN - p.TAG_LEN
    assert len(p.seal(key, iv, SID, 1, b"x" * largest)) == p.MAX_DATAGRAM
    with pytest.raises(ValueError):
        p.seal(key, iv, SID, 1, b"x" * (largest + 1))
    with pytest.raises(ValueError):
        p.seal(key, iv, SID, 1, b"")                     # below MIN_DATAGRAM
    for flags in (0x08, 0x10, 0x80, 0x100, -1):
        with pytest.raises(ValueError):
            p.seal(key, iv, SID, 1, b"x", flags=flags)
    for sid in (b"abc", b"abcde"):
        with pytest.raises(ValueError):
            p.seal(key, iv, sid, 1, b"x")


def _reason(fn, *args):
    with pytest.raises(p.Reject) as e:
        fn(*args)
    assert isinstance(e.value, ValueError)
    return e.value.reason


def test_size_checks_come_first():
    good = p.seal(*_keys(), SID, 1, b"x")
    assert _reason(p.parse_header, good + bytes(1201 - len(good))) == "oversize"
    assert _reason(p.parse_header, b"\xff" * 1201) == "oversize"
    assert _reason(p.parse_header, good[:p.MIN_DATAGRAM - 1]) == "truncated"
    assert _reason(p.parse_header, b"") == "truncated"
    assert _reason(p.parse_header, b"\xff" * (p.MIN_DATAGRAM - 1)) == "truncated"


def test_malformed_version_type_and_reserved_bits():
    good = p.seal(*_keys(), SID, 1, b"x")
    for ver_type in (0x11, 0x22, 0x20, 0x31, 0x00, 0xFF):
        assert _reason(p.parse_header, bytes([ver_type]) + good[1:]) == "malformed"
    for bit in range(3, 8):
        dg = bytearray(good)
        dg[1] |= 1 << bit
        assert _reason(p.parse_header, bytes(dg)) == "malformed"


def test_reject_reason_is_constrained():
    with pytest.raises(ValueError):
        p.Reject("something_else")
    assert p.Reject("auth").reason == "auth"


def _flip(dg, index, mask=1):
    out = bytearray(dg)
    out[index] ^= mask
    return bytes(out)


def test_any_flipped_bit_fails_authentication():
    key, iv = _keys()
    dg = p.seal(key, iv, SID, 7, b"some opus bytes")
    cases = {
        "ciphertext": _flip(dg, 12),
        "tag": _flip(dg, len(dg) - 1),
        "generation": _flip(dg, 1, 0x01),
        "generation_hi": _flip(dg, 1, 0x02),
        "fec_flag": _flip(dg, 1, p.FLAG_FEC),
        **{f"session_id[{i}]": _flip(dg, 2 + i) for i in range(4)},
        **{f"seq[{i}]": _flip(dg, 6 + i) for i in range(6)},
    }
    for name, bad in cases.items():
        assert _reason(p.open_, key, iv, bad) == "auth", name


def test_wrong_key_or_iv_fails_authentication():
    key, iv = _keys()
    dg = p.seal(key, iv, SID, 7, b"x")
    other_key, other_iv = _keys()
    assert _reason(p.open_, other_key, iv, dg) == "auth"
    assert _reason(p.open_, key, other_iv, dg) == "auth"


def test_open_size_and_header_checks_precede_the_aead():
    key, iv = _keys()
    assert _reason(p.open_, key, iv, b"\x21" * 5) == "truncated"
    assert _reason(p.open_, key, iv, b"\x21" * 1201) == "oversize"
    assert _reason(p.open_, key, iv, b"\x11" + bytes(40)) == "malformed"
