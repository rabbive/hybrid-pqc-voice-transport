import os, pytest
from hpqv import packet as p

def test_seal_open_roundtrip():
    key, prefix = os.urandom(32), os.urandom(4)
    dg = p.seal(key, prefix, 42, b"opusdata", flags=1)
    assert len(dg) <= p.MAX_DATAGRAM
    seq, flags, frame = p.open_(key, prefix, dg)
    assert (seq, flags, frame) == (42, 1, b"opusdata")

def test_oversize_frame_rejected():
    key, prefix = os.urandom(32), os.urandom(4)
    with pytest.raises(ValueError):
        p.seal(key, prefix, 1, b"x" * 1300)

def test_bad_version_rejected():
    key, prefix = os.urandom(32), os.urandom(4)
    dg = bytearray(p.seal(key, prefix, 1, b"opusdata"))
    dg[0] ^= 0xFF
    with pytest.raises(ValueError):
        p.open_(key, prefix, bytes(dg))
