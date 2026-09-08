import pytest
from hpqv import handshake as h

def test_both_sides_derive_same_key():
    sig_pub, sig_sec = h.make_identity()
    hello, kem = h.build_hello(sig_pub, sig_sec)
    accept, resp_session = h.accept_hello(hello, sig_pub)
    init_session = h.finish(accept, kem)
    assert init_session == resp_session
    assert len(init_session[0]) == 32 and len(init_session[1]) == 4

def test_tampered_signature_rejected():
    sig_pub, sig_sec = h.make_identity()
    hello, _ = h.build_hello(sig_pub, sig_sec)
    bad = bytearray(hello); bad[-1] ^= 0xFF
    with pytest.raises(ValueError):
        h.accept_hello(bytes(bad), sig_pub)
