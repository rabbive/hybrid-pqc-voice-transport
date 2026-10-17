"""v2 identities: ML-DSA-65 key + self-signed cert, stored safely, pinned by public key."""
import datetime
import hashlib
import json
import os
import re
import stat

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa
from cryptography.x509.oid import NameOID

from hpqv import identity


def _raw_pub(cert_pem):
    return x509.load_pem_x509_certificate(cert_pem).public_key().public_bytes_raw()


def test_new_is_mldsa65_self_signed_with_cn():
    ident = identity.new("caller")
    cert = x509.load_pem_x509_certificate(ident.cert_pem)
    assert isinstance(cert.public_key(), mldsa.MLDSA65PublicKey)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "caller"
    assert cert.issuer == cert.subject
    key = serialization.load_pem_private_key(ident.key_pem, None)
    assert isinstance(key, mldsa.MLDSA65PrivateKey)
    assert key.public_key().public_bytes_raw() == _raw_pub(ident.cert_pem)
    assert b"PRIVATE KEY" not in repr(ident).encode()


def assert_private_key_file(path):
    if os.name == "nt":   # Windows ignores mode bits; the key is protected by its ACL
        identity._check_private_key_file(str(path))
    else:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_save_creates_private_key_file_and_round_trips(tmp_path):
    ident = identity.new("listener")
    kp, cp = tmp_path / "listener.key", tmp_path / "listener.crt"
    identity.save(ident, str(kp), str(cp))
    assert_private_key_file(kp)
    loaded = identity.load(str(kp), str(cp))
    assert loaded == ident


@pytest.mark.parametrize("existing", ["key", "cert"])
def test_save_refuses_to_overwrite_either_file(tmp_path, existing):
    kp, cp = tmp_path / "a.key", tmp_path / "a.crt"
    victim = kp if existing == "key" else cp
    victim.write_bytes(b"existing secret")
    with pytest.raises(FileExistsError):
        identity.save(identity.new("a"), str(kp), str(cp))
    assert victim.read_bytes() == b"existing secret"
    other = cp if existing == "key" else kp
    assert not other.exists(), "a refused save must not leave a half-written identity"


def test_load_rejects_key_that_does_not_match_cert(tmp_path):
    a, b = identity.new("a"), identity.new("b")
    (tmp_path / "a.key").write_bytes(a.key_pem)
    os.chmod(tmp_path / "a.key", 0o600)          # about key contents, not permissions
    (tmp_path / "b.crt").write_bytes(b.cert_pem)
    with pytest.raises(ValueError):
        identity.load(str(tmp_path / "a.key"), str(tmp_path / "b.crt"))


def test_load_rejects_non_mldsa_key(tmp_path):
    ed = ed25519.Ed25519PrivateKey.generate()
    (tmp_path / "ed.key").write_bytes(ed.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(tmp_path / "ed.key", 0o600)         # about key type, not permissions
    (tmp_path / "ed.crt").write_bytes(_ed25519_cert(ed))
    with pytest.raises(ValueError):
        identity.load(str(tmp_path / "ed.key"), str(tmp_path / "ed.crt"))


def test_load_peer_cert_returns_pem_with_same_key(tmp_path):
    ident = identity.new("peer")
    p = tmp_path / "peer.crt"
    p.write_bytes(ident.cert_pem)
    pem = identity.load_peer_cert(str(p))
    assert _raw_pub(pem) == _raw_pub(ident.cert_pem)


def test_load_peer_cert_refuses_v1_identity_json(tmp_path):
    p = tmp_path / "identity.json"
    p.write_text(json.dumps({
        "listener": {"sig_pub": "ab" * 32, "sig_secret": "cd" * 32},
        "caller": {"sig_pub": "ef" * 32, "sig_secret": "01" * 32},
    }))
    with pytest.raises(identity.LegacyIdentityError):
        identity.load_peer_cert(str(p))
    assert issubclass(identity.LegacyIdentityError, ValueError)


def _ed25519_cert(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ed")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, None))
    return cert.public_bytes(serialization.Encoding.PEM)


def test_load_peer_cert_rejects_non_mldsa_cert(tmp_path):
    p = tmp_path / "ed.crt"
    p.write_bytes(_ed25519_cert(ed25519.Ed25519PrivateKey.generate()))
    with pytest.raises(ValueError):
        identity.load_peer_cert(str(p))


def test_load_peer_cert_rejects_garbage_and_private_keys(tmp_path):
    p = tmp_path / "x.crt"
    p.write_bytes(b"not a certificate")
    with pytest.raises(ValueError):
        identity.load_peer_cert(str(p))
    p.write_bytes(identity.new("k").key_pem)
    with pytest.raises(ValueError):
        identity.load_peer_cert(str(p))


def test_fingerprint_is_grouped_sha256_of_raw_public_key():
    ident = identity.new("fp")
    fp = identity.fingerprint(ident.cert_pem)
    assert re.fullmatch(r"[0-9a-f]{4}( [0-9a-f]{4}){15}", fp)
    assert fp.replace(" ", "") == hashlib.sha256(_raw_pub(ident.cert_pem)).hexdigest()
    assert identity.fingerprint(identity.new("fp").cert_pem) != fp
