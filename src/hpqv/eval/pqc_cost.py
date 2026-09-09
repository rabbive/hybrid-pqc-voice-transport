import statistics
import time

import oqs

from hpqv.handshake import KEM_ALG, SIG_ALG, KEM_PUB_LEN, SIG_PUB_LEN

SIG_LEN = 3309
KEM_CT_LEN = 1088

HELLO_BYTES = KEM_PUB_LEN + SIG_PUB_LEN + SIG_LEN
ACCEPT_BYTES = KEM_CT_LEN + SIG_LEN
HANDSHAKE_BYTES = HELLO_BYTES + ACCEPT_BYTES


def handshake_bytes(scheme: str) -> int:
    return HANDSHAKE_BYTES


def _run_once() -> float:
    start = time.perf_counter()

    kem = oqs.KeyEncapsulation(KEM_ALG)
    kem_pub = kem.generate_keypair()
    ct, shared_enc = kem.encap_secret(kem_pub)
    shared_dec = kem.decap_secret(ct)

    signer = oqs.Signature(SIG_ALG)
    sig_pub = signer.generate_keypair()
    msg = b"hpqv-pqc-cost-benchmark"
    sig = signer.sign(msg)
    oqs.Signature(SIG_ALG).verify(msg, sig, sig_pub)

    return (time.perf_counter() - start) * 1000.0


def handshake_ms() -> float:
    samples = [_run_once() for _ in range(5)]
    return statistics.median(samples)
