"""v2 voice datagram (docs/superpowers/plans/2026-10-16-secure-session-v2.md).

Header (12 B, the whole header is AAD):
    ver_type(1) = 0x21 | flags(1) | session_id(4) | seq(uint48 BE)
flags bits 0-1 = key generation mod 4 (keyring), bit 2 = sender's encoder FEC
was on, bits 3-7 reserved and must be 0.
ChaCha20-Poly1305 with nonce = iv XOR (0^6 || seq.to_bytes(6)).
"""
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

VERSION, TYPE_VOICE = 2, 1
VER_TYPE = (VERSION << 4) | TYPE_VOICE
HEADER_LEN, TAG_LEN = 12, 16
MAX_DATAGRAM = 1200
MIN_DATAGRAM = HEADER_LEN + TAG_LEN + 1
MAX_SEQ = 2**48 - 1
GEN_MASK, FLAG_FEC = 0x03, 0x04
_RESERVED = 0xFF & ~(GEN_MASK | FLAG_FEC)
SESSION_ID_LEN = 4

REJECT_REASONS = ("oversize", "truncated", "malformed", "wrong_session",
                  "no_key", "replay", "auth")


class Reject(ValueError):
    """A received datagram was dropped. `reason` is one of REJECT_REASONS."""

    def __init__(self, reason: str):
        if reason not in REJECT_REASONS:
            raise ValueError(f"unknown reject reason {reason!r}")
        super().__init__(reason)
        self.reason = reason


def _expand(secret: bytes, info: bytes, length: int) -> bytes:
    return HKDFExpand(hashes.SHA256(), length, info).derive(secret)


def direction_keys(secret: bytes) -> tuple[bytes, bytes]:
    """(key32, iv12) for one direction of one generation."""
    return _expand(secret, b"hpqv2 media key", 32), _expand(secret, b"hpqv2 media iv", 12)


def next_secret(secret: bytes) -> bytes:
    """The next generation's secret for the same direction."""
    return _expand(secret, b"hpqv2 media upd", 32)


def _nonce(iv: bytes, seq: int) -> bytes:
    return (int.from_bytes(iv, "big") ^ seq).to_bytes(12, "big")


def parse_header(dg: bytes) -> tuple[bytes, int, int]:
    """(session_id, seq, flags). Size checks first, then ver_type, then reserved bits."""
    if len(dg) > MAX_DATAGRAM:
        raise Reject("oversize")
    if len(dg) < MIN_DATAGRAM:
        raise Reject("truncated")
    if dg[0] != VER_TYPE or dg[1] & _RESERVED:
        raise Reject("malformed")
    return bytes(dg[2:6]), int.from_bytes(dg[6:12], "big"), dg[1]


def seal(key, iv, session_id, seq, frame, flags=0) -> bytes:
    if not 0 <= seq <= MAX_SEQ:
        raise ValueError("seq out of range")
    if not 0 <= flags <= 0xFF or flags & _RESERVED:
        raise ValueError("reserved flag bits set")
    if len(session_id) != SESSION_ID_LEN:
        raise ValueError("session_id must be 4 bytes")
    size = HEADER_LEN + len(frame) + TAG_LEN
    if not MIN_DATAGRAM <= size <= MAX_DATAGRAM:
        raise ValueError(f"datagram {size} B outside [{MIN_DATAGRAM}, {MAX_DATAGRAM}]")
    header = bytes([VER_TYPE, flags]) + bytes(session_id) + seq.to_bytes(6, "big")
    return header + ChaCha20Poly1305(key).encrypt(_nonce(iv, seq), frame, header)


def open_(key, iv, dg) -> tuple[int, int, bytes]:
    """(seq, flags, frame). Does not check session_id or replay: Keyring does."""
    _, seq, flags = parse_header(dg)
    try:
        frame = ChaCha20Poly1305(key).decrypt(_nonce(iv, seq), dg[HEADER_LEN:], dg[:HEADER_LEN])
    except InvalidTag:
        raise Reject("auth") from None
    return seq, flags, frame
