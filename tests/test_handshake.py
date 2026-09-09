import pytest
from hpqv import handshake as h


def test_mutual_auth_both_derive_matching_session():
    a_pub, a_sec = h.make_identity()   # initiator identity
    b_pub, b_sec = h.make_identity()   # responder identity
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)     # responder signs
    sess_a = h.finish(accept, kem, kem_pub, b_pub)           # initiator verifies b
    assert sess_a[0] == sess_b[0] and len(sess_a[0]) == 32
    assert sess_a[1] == sess_b[2] and sess_a[2] == sess_b[1]  # directions cross over
    assert len(sess_a[1]) == 4 and len(sess_a[2]) == 4


def test_responder_rejects_tampered_hello():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, _, _ = h.build_hello(a_pub, a_sec)
    bad = bytearray(hello); bad[-1] ^= 0xFF
    with pytest.raises(ValueError):
        h.accept_hello(bytes(bad), a_pub, b_sec)


def test_initiator_rejects_forged_accept():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    _evil_pub, evil_sec = h.make_identity()                 # attacker identity
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, evil_sec)      # signed by attacker, not b
    with pytest.raises(ValueError):
        h.finish(accept, kem, kem_pub, b_pub)               # pinned to b -> reject


def test_initiator_rejects_tampered_ciphertext():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, b_sec)
    bad = bytearray(accept); bad[10] ^= 0xFF                # flip a ciphertext byte
    with pytest.raises(ValueError):
        h.finish(bytes(bad), kem, kem_pub, b_pub)
