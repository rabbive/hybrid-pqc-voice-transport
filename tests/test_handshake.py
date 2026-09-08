import pytest
from hpqv import handshake as h

def test_both_sides_derive_same_key():
    sig_pub, sig_sec = h.make_identity()
    hello, kem = h.build_hello(sig_pub, sig_sec)
    accept, resp_session = h.accept_hello(hello, sig_pub)
    init_session = h.finish(accept, kem)
    ki, si, ri = init_session
    kr, sr, rr = resp_session
    assert ki == kr and len(ki) == 32
    assert si == rr and ri == sr          # directions cross over
    assert len({len(si), len(ri)}) == 1 and len(si) == 4

def test_tampered_signature_rejected():
    sig_pub, sig_sec = h.make_identity()
    hello, _ = h.build_hello(sig_pub, sig_sec)
    bad = bytearray(hello); bad[-1] ^= 0xFF
    with pytest.raises(ValueError):
        h.accept_hello(bytes(bad), sig_pub)
