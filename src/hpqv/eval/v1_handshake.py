"""HISTORICAL v1 protocol, insecure (see docs/PROTOCOL-v2.md section 1), used only to reproduce results/*.csv; never import from production code.

The v1 hybrid-handshake message functions and record framing, ported from
the original C-library binding to `cryptography`'s ML-KEM-768 and ML-DSA-65.
The algorithms and every byte length are identical to the original (kem_pub 1184, sig_pub 1952,
sig 3309, ct 1088; HELLO 6,446 B, ACCEPT 4,398 B), so the experiments that
produced results/*.csv measure the same bytes.

Differences from the original, none of which change what is on the wire:
- lengths of HELLO / ACCEPT are checked exactly (v1 sliced blindly);
- `send_record` / `recv_record` cap the length at MAX_RECORD (v1 trusted a
  32-bit length, finding F8).
The unchecked version byte, the thin transcript and the single shared key are
kept as they were: they are the v1 behaviour the experiments measured.
"""
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import mldsa, mlkem
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
KEM_PUB_LEN = 1184
SIG_PUB_LEN = 1952
SIG_LEN = 3309
KEM_CT_LEN = 1088
HELLO_LEN = 1 + KEM_PUB_LEN + SIG_PUB_LEN + SIG_LEN      # 6446
ACCEPT_LEN = 1 + KEM_CT_LEN + SIG_LEN                    # 4398
MAX_RECORD = 16384


def derive(shared_secret: bytes) -> tuple[bytes, bytes, bytes]:
    okm = HKDF(algorithm=hashes.SHA256(), length=40, salt=None,
               info=b"hpqv v1").derive(shared_secret)
    return okm[:32], okm[32:36], okm[36:40]


def make_identity() -> tuple[bytes, bytes]:
    """(public key, secret seed): the secret is the 32-byte ML-DSA-65 seed."""
    sk = mldsa.MLDSA65PrivateKey.generate()
    return sk.public_key().public_bytes_raw(), sk.private_bytes_raw()


def _verify(pub: bytes, sig: bytes, msg: bytes, what: str) -> None:
    try:
        mldsa.MLDSA65PublicKey.from_public_bytes(pub).verify(sig, msg)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(what) from exc


def build_hello(sig_pub: bytes, sig_secret: bytes):
    """Returns (hello, kem private key, kem_pub); the key is the initiator's state."""
    kem = mlkem.MLKEM768PrivateKey.generate()
    kem_pub = kem.public_key().public_bytes_raw()
    sig = mldsa.MLDSA65PrivateKey.from_seed_bytes(sig_secret).sign(kem_pub + sig_pub)
    return bytes([VERSION]) + kem_pub + sig_pub + sig, kem, kem_pub


def accept_hello(hello: bytes, trusted_peer_sig_pub: bytes, my_sig_secret: bytes):
    if len(hello) != HELLO_LEN:
        raise ValueError("bad hello length")
    kem_pub = hello[1:1 + KEM_PUB_LEN]
    sig_pub = hello[1 + KEM_PUB_LEN:1 + KEM_PUB_LEN + SIG_PUB_LEN]
    sig = hello[1 + KEM_PUB_LEN + SIG_PUB_LEN:]
    if sig_pub != trusted_peer_sig_pub:
        raise ValueError("unpinned identity")
    _verify(sig_pub, sig, kem_pub + sig_pub, "bad signature")
    shared, ct = mlkem.MLKEM768PublicKey.from_public_bytes(kem_pub).encapsulate()
    resp_sig = mldsa.MLDSA65PrivateKey.from_seed_bytes(my_sig_secret).sign(kem_pub + ct)
    accept = bytes([VERSION]) + ct + resp_sig
    key, prefix_i2r, prefix_r2i = derive(shared)
    return accept, (key, prefix_r2i, prefix_i2r)


def finish(accept: bytes, kem, kem_pub: bytes, trusted_peer_sig_pub: bytes) -> tuple[bytes, bytes, bytes]:
    if len(accept) != ACCEPT_LEN:
        raise ValueError("bad accept length")
    ct = accept[1:1 + KEM_CT_LEN]
    resp_sig = accept[1 + KEM_CT_LEN:]
    _verify(trusted_peer_sig_pub, resp_sig, kem_pub + ct, "bad responder signature")
    key, prefix_i2r, prefix_r2i = derive(kem.decapsulate(ct))
    return key, prefix_i2r, prefix_r2i


def send_record(sock, payload: bytes) -> None:
    if len(payload) > MAX_RECORD:
        raise ValueError("record too large")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_record(sock) -> bytes:
    (length,) = struct.unpack(">I", _recvn(sock, 4))
    if length > MAX_RECORD:
        raise ValueError("record too large")
    return _recvn(sock, length)


def run_initiator(sock, sig_pub, sig_secret, peer_sig_pub):
    hello, kem, kem_pub = build_hello(sig_pub, sig_secret)
    send_record(sock, hello)
    return finish(recv_record(sock), kem, kem_pub, peer_sig_pub)


def run_responder(sock, sig_pub, sig_secret, peer_sig_pub):
    accept, session = accept_hello(recv_record(sock), peer_sig_pub, sig_secret)
    send_record(sock, accept)
    return session
