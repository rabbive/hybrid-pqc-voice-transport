from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def rotate(session):
    """Ratchet a session to the next generation. Both peers call this on
    KEY_ROTATE and land on the same new key with mirrored direction prefixes.

    A session is (key, send_prefix, recv_prefix). Both peers hold mirrored
    sessions (A.send_prefix == B.recv_prefix). We derive the new key + two
    prefixes from the shared current key, then order them by a stable
    comparison of THIS side's prefixes — the two peers evaluate opposite
    conditions, so A.new_send == B.new_recv and vice versa.
    """
    key, send_prefix, recv_prefix = session
    okm = HKDF(algorithm=hashes.SHA256(), length=40, salt=None,
               info=b"hpqv rotate v1").derive(key)
    new_key = okm[:32]
    p0, p1 = okm[32:36], okm[36:40]
    if send_prefix < recv_prefix:
        new_send, new_recv = p0, p1
    else:
        new_send, new_recv = p1, p0
    return new_key, new_send, new_recv
