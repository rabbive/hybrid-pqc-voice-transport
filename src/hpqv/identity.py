"""v2 peer identity: an ML-DSA-65 key and a self-signed X.509 cert, pinned by public key."""
import datetime
import hashlib
import os
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import mldsa
from cryptography.x509.oid import NameOID


class LegacyIdentityError(ValueError):
    """A v1 identity.json (both peers' secret keys in one file) was offered as a peer cert."""


@dataclass(frozen=True)
class Identity:
    key_pem: bytes = field(repr=False)   # PKCS#8 PEM, unencrypted, ML-DSA-65 (never logged)
    cert_pem: bytes                      # self-signed ML-DSA-65 X.509, CN = name, public


def new(name: str, days: int = 3650) -> Identity:
    key = mldsa.MLDSA65PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=days))
            .sign(key, None))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return Identity(key_pem=key_pem, cert_pem=cert.public_bytes(serialization.Encoding.PEM))


def _write_new(path: str, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(data)


def save(ident: Identity, key_path: str, cert_path: str) -> None:
    """Write both files; refuses to overwrite either one and never leaves half an identity."""
    if os.path.lexists(cert_path):
        raise FileExistsError(cert_path)
    _write_new(key_path, ident.key_pem, 0o600)
    try:
        _write_new(cert_path, ident.cert_pem, 0o644)
    except BaseException:
        os.unlink(key_path)
        raise


def _mldsa_cert(cert_pem: bytes) -> x509.Certificate:
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError as e:
        raise ValueError("not a PEM X.509 certificate") from e
    if not isinstance(cert.public_key(), mldsa.MLDSA65PublicKey):
        raise ValueError("certificate key is not ML-DSA-65")
    return cert


def load(key_path: str, cert_path: str) -> Identity:
    with open(key_path, "rb") as f:
        key_pem = f.read()
    with open(cert_path, "rb") as f:
        cert_pem = f.read()
    try:
        key = serialization.load_pem_private_key(key_pem, None)
    except (ValueError, TypeError) as e:
        raise ValueError("not an unencrypted PEM private key") from e
    if not isinstance(key, mldsa.MLDSA65PrivateKey):
        raise ValueError("private key is not ML-DSA-65")
    cert = _mldsa_cert(cert_pem)
    if key.public_key().public_bytes_raw() != cert.public_key().public_bytes_raw():
        raise ValueError("private key does not match certificate")
    return Identity(key_pem=key_pem, cert_pem=cert_pem)


def load_peer_cert(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if data.lstrip()[:1] == b"{":
        raise LegacyIdentityError(
            f"{path} is a v1 identity.json; v2 needs the peer's certificate file "
            "(each side runs keygen and exchanges only its .crt)")
    if b"PRIVATE KEY" in data:
        raise ValueError(f"{path} holds a private key, expected the peer's certificate")
    return _mldsa_cert(data).public_bytes(serialization.Encoding.PEM)


def raw_public_key(cert_pem: bytes) -> bytes:
    return _mldsa_cert(cert_pem).public_key().public_bytes_raw()


def fingerprint(cert_pem: bytes) -> str:
    h = hashlib.sha256(raw_public_key(cert_pem)).hexdigest()
    return " ".join(h[i:i + 4] for i in range(0, 64, 4))
