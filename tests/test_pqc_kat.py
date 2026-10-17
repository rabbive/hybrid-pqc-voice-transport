"""NIST ACVP known-answer tests for the `cryptography` ML-KEM-768 and ML-DSA-65
that protocol v2 uses in place of liboqs.

tests/vectors/acvp-mlkem768-mldsa65.json is a subset of usnistgov/ACVP-Server
(commit pinned in its "source" field): every ML-KEM-768 / ML-DSA-65 group whose
inputs the `cryptography` API can accept. Not covered: ML-KEM encapsulation
AFT and ML-DSA sigGen, which need the derandomized internals (`m`, `rnd`) that
`cryptography` does not expose; expanded-key formats, which v2 never stores.
"""
import json
import os

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import mldsa, mlkem

with open(os.path.join(os.path.dirname(__file__), "vectors", "acvp-mlkem768-mldsa65.json")) as f:
    ACVP = json.load(f)


def _h(s):
    return bytes.fromhex(s)


def _ids(cases):
    return [str(c["tcId"]) for c in cases]


@pytest.mark.parametrize("t", ACVP["mlkem768_keygen"], ids=_ids(ACVP["mlkem768_keygen"]))
def test_mlkem768_keygen(t):
    sk = mlkem.MLKEM768PrivateKey.from_seed_bytes(_h(t["d"]) + _h(t["z"]))
    assert sk.public_key().public_bytes_raw() == _h(t["ek"])


@pytest.mark.parametrize("t", ACVP["mlkem768_decap_seed"], ids=_ids(ACVP["mlkem768_decap_seed"]))
def test_mlkem768_decapsulation(t):
    """Includes modified ciphertexts: implicit rejection must give NIST's K-bar."""
    sk = mlkem.MLKEM768PrivateKey.from_seed_bytes(_h(t["d"]) + _h(t["z"]))
    assert sk.decapsulate(_h(t["c"])) == _h(t["k"])


@pytest.mark.parametrize("t", ACVP["mlkem768_ek_check"], ids=_ids(ACVP["mlkem768_ek_check"]))
def test_mlkem768_encapsulation_key_check(t):
    if t["testPassed"]:
        mlkem.MLKEM768PublicKey.from_public_bytes(_h(t["ek"])).encapsulate()
    else:
        with pytest.raises(ValueError):
            mlkem.MLKEM768PublicKey.from_public_bytes(_h(t["ek"])).encapsulate()


@pytest.mark.parametrize("t", ACVP["mldsa65_keygen"], ids=_ids(ACVP["mldsa65_keygen"]))
def test_mldsa65_keygen(t):
    sk = mldsa.MLDSA65PrivateKey.from_seed_bytes(_h(t["seed"]))
    assert sk.public_key().public_bytes_raw() == _h(t["pk"])


@pytest.mark.parametrize("t", ACVP["mldsa65_sigver_pure"], ids=_ids(ACVP["mldsa65_sigver_pure"]))
def test_mldsa65_sigver_pure_with_context(t):
    pk = mldsa.MLDSA65PublicKey.from_public_bytes(_h(t["pk"]))
    args = (_h(t["signature"]), _h(t["message"]), _h(t["context"]))
    if t["testPassed"]:
        pk.verify(*args)
    else:
        with pytest.raises(InvalidSignature):
            pk.verify(*args)


@pytest.mark.parametrize("t", ACVP["mldsa65_sigver_mu"], ids=_ids(ACVP["mldsa65_sigver_mu"]))
def test_mldsa65_sigver_external_mu(t):
    pk = mldsa.MLDSA65PublicKey.from_public_bytes(_h(t["pk"]))
    if t["testPassed"]:
        pk.verify_mu(_h(t["signature"]), _h(t["mu"]))
    else:
        with pytest.raises(InvalidSignature):
            pk.verify_mu(_h(t["signature"]), _h(t["mu"]))


def test_mldsa65_context_separates_signatures():
    """v2 signs with a protocol context string; a different context must fail."""
    sk = mldsa.MLDSA65PrivateKey.generate()
    sig = sk.sign(b"transcript", b"hpqv v2 initiator")
    sk.public_key().verify(sig, b"transcript", b"hpqv v2 initiator")
    with pytest.raises(InvalidSignature):
        sk.public_key().verify(sig, b"transcript", b"hpqv v2 responder")
