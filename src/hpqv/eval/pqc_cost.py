"""Cost of the v1 PQC handshake material (ML-KEM-768 + ML-DSA-65).

Sizes are the v1 message sizes (see v1_handshake). The timing runs the same
primitives through `cryptography`; results/*.csv were produced with the original
C binding, so a re-run gives different (not comparable) milliseconds.
"""
import statistics
import time

from cryptography.hazmat.primitives.asymmetric import mldsa, mlkem

from hpqv.eval.v1_handshake import KEM_CT_LEN, KEM_PUB_LEN, SIG_LEN, SIG_PUB_LEN

HELLO_BYTES = KEM_PUB_LEN + SIG_PUB_LEN + SIG_LEN
ACCEPT_BYTES = KEM_CT_LEN + SIG_LEN
HANDSHAKE_BYTES = HELLO_BYTES + ACCEPT_BYTES


def handshake_bytes(scheme: str) -> int:
    return HANDSHAKE_BYTES


def _run_once() -> float:
    start = time.perf_counter()

    kem = mlkem.MLKEM768PrivateKey.generate()
    shared_enc, ct = kem.public_key().encapsulate()
    shared_dec = kem.decapsulate(ct)

    signer = mldsa.MLDSA65PrivateKey.generate()
    msg = b"hpqv-pqc-cost-benchmark"
    sig = signer.sign(msg)
    signer.public_key().verify(sig, msg)

    return (time.perf_counter() - start) * 1000.0


def handshake_ms() -> float:
    samples = [_run_once() for _ in range(5)]
    return statistics.median(samples)
