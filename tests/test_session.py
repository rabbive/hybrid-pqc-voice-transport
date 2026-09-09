from hpqv import handshake as h, session as s
from hpqv import packet


def _pair():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)
    sess_a = h.finish(accept, kem, kem_pub, b_pub)
    return sess_a, sess_b


def test_rotate_keeps_peers_in_sync_and_changes_key():
    sess_a, sess_b = _pair()
    ra, rb = s.rotate(sess_a), s.rotate(sess_b)
    assert ra[0] == rb[0] and ra[0] != sess_a[0]     # new shared key, changed
    assert ra[1] == rb[2] and ra[2] == rb[1]         # directions still mirror
    assert len(ra[1]) == 4 and len(ra[2]) == 4


def test_frame_sealed_after_rotation_fails_under_old_key():
    sess_a, sess_b = _pair()
    ra, rb = s.rotate(sess_a), s.rotate(sess_b)
    # A seals with the rotated key; B opens with the rotated key -> ok.
    dg = packet.seal(ra[0], ra[1], 0, b"voice")
    seq, _flags, frame = packet.open_(rb[0], rb[2], dg)
    assert frame == b"voice"
    # Opening the same datagram with the OLD key must fail (key actually changed).
    import pytest
    with pytest.raises(Exception):
        packet.open_(sess_b[0], sess_b[2], dg)
