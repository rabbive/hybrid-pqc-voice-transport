"""v2 peer identity: an ML-DSA-65 key and a self-signed X.509 cert, pinned by public key."""
import datetime
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import mldsa
from cryptography.x509.oid import NameOID


_IS_WINDOWS = os.name == "nt"
# Windows principals that mean "other people on this machine". SDDL aliases plus their
# SIDs, so the check does not depend on the display language: Everyone, Authenticated
# Users, Builtin Users, Builtin Guests, Interactive, Network, Anonymous.
_BROAD = {"WD", "AU", "BU", "BG", "IU", "NU", "AN",
          "S-1-1-0", "S-1-5-11", "S-1-5-32-545", "S-1-5-32-546", "S-1-5-4", "S-1-5-2", "S-1-5-7"}
_ACE = re.compile(r"\(([^;()]*);[^;()]*;[^;()]*;[^;()]*;[^;()]*;([^;()]+)\)")


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


def _write_new(path: str, data: bytes, mode: int, private: bool = False) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
    with os.fdopen(fd, "wb") as f:
        if not _IS_WINDOWS:
            os.fchmod(f.fileno(), mode)       # umask cannot widen it; Windows has no fchmod on 3.11
        elif private:
            _restrict_windows_acl(path)       # before any secret byte is written
        f.write(data)


def _current_user_sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, timeout=10)
    m = re.search(r"S-1-[0-9-]+", out.stdout or "")
    if out.returncode != 0 or not m:
        raise PermissionError("could not determine the current Windows user's SID")
    return m.group(0)


def _restrict_windows_acl(path: str) -> None:
    """Drop inherited ACEs and grant only the current user. Windows ignores the POSIX
    mode bits, and a file under a shared folder would otherwise inherit its readers."""
    sid = _current_user_sid()
    out = subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"*{sid}:F"],
                         capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise PermissionError(f"could not restrict access to {path} (icacls exit {out.returncode})")


def _windows_sddl(path: str) -> str:
    """The file's DACL as SDDL (locale-independent SIDs/aliases), via `icacls /save`."""
    with tempfile.TemporaryDirectory() as d:
        dump = os.path.join(d, "acl.txt")
        out = subprocess.run(["icacls", path, "/save", dump, "/q"], capture_output=True,
                             timeout=10)
        if out.returncode != 0:
            raise OSError(f"icacls /save exit {out.returncode}")
        with open(dump, "rb") as f:
            text = f.read().decode("utf-16")
    return text.splitlines()[1] if "\n" in text else text


def _broad_allow_aces(sddl: str) -> list[str]:
    """Principals in allow-ACEs that cover other users of the machine."""
    return [sid for kind, sid in _ACE.findall(sddl) if kind == "A" and sid in _BROAD]


def _check_private_key_file(path: str) -> None:
    """Refuse a private key other local users could read (as ssh does)."""
    if _IS_WINDOWS:
        try:
            broad = _broad_allow_aces(_windows_sddl(path))
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as e:
            raise PermissionError(f"could not check who can read {path}: {e}") from e
        if broad:
            raise PermissionError(
                f"{path} is readable by {', '.join(broad)}; restrict it with "
                f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%":F')
        return
    st = os.stat(path)
    if st.st_uid != os.geteuid():
        raise PermissionError(f"{path} is owned by uid {st.st_uid}, not by you")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise PermissionError(
            f"{path} is accessible by other users (mode {stat.S_IMODE(st.st_mode):o}); "
            f"run: chmod 600 {path}")


def save(ident: Identity, key_path: str, cert_path: str) -> None:
    """Write both files; refuses to overwrite either one and never leaves half an identity."""
    if os.path.lexists(cert_path):
        raise FileExistsError(cert_path)
    try:
        _write_new(key_path, ident.key_pem, 0o600, private=True)
    except PermissionError:
        if os.path.lexists(key_path):
            os.unlink(key_path)
        raise
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
    _check_private_key_file(key_path)
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
