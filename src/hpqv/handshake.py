import oqs
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

KEM_ALG, SIG_ALG = "ML-KEM-768", "ML-DSA-65"
VERSION = 1

KEM_PUB_LEN = 1184
SIG_PUB_LEN = 1952


def derive(shared_secret: bytes) -> tuple[bytes, bytes, bytes]:
    okm = HKDF(algorithm=hashes.SHA256(), length=40, salt=None,
               info=b"hpqv v1").derive(shared_secret)
    return okm[:32], okm[32:36], okm[36:40]


def make_identity() -> tuple[bytes, bytes]:
    sig = oqs.Signature(SIG_ALG)
    pub = sig.generate_keypair()
    return pub, sig.export_secret_key()


def build_hello(sig_pub: bytes, sig_secret: bytes):
    kem = oqs.KeyEncapsulation(KEM_ALG)
    kem_pub = kem.generate_keypair()
    signer = oqs.Signature(SIG_ALG, secret_key=sig_secret)
    sig = signer.sign(kem_pub + sig_pub)
    hello = bytes([VERSION]) + kem_pub + sig_pub + sig
    return hello, kem


def accept_hello(hello: bytes, trusted_sig_pub: bytes):
    kem_pub = hello[1:1 + KEM_PUB_LEN]
    sig_pub = hello[1 + KEM_PUB_LEN:1 + KEM_PUB_LEN + SIG_PUB_LEN]
    sig = hello[1 + KEM_PUB_LEN + SIG_PUB_LEN:]
    if sig_pub != trusted_sig_pub:
        raise ValueError("unpinned identity")
    verifier = oqs.Signature(SIG_ALG)
    if not verifier.verify(kem_pub + sig_pub, sig, sig_pub):
        raise ValueError("bad signature")
    kem = oqs.KeyEncapsulation(KEM_ALG)
    ct, shared = kem.encap_secret(kem_pub)
    accept = bytes([VERSION]) + ct
    key, prefix_i2r, prefix_r2i = derive(shared)
    return accept, (key, prefix_r2i, prefix_i2r)


def finish(accept: bytes, kem) -> tuple[bytes, bytes, bytes]:
    ct = accept[1:]
    shared = kem.decap_secret(ct)
    key, prefix_i2r, prefix_r2i = derive(shared)
    return key, prefix_i2r, prefix_r2i
