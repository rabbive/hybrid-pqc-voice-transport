"""Pinned friends, keyed by identity fingerprint (B2).

Only certificates are stored (public data). A friend is added only by a
completed pairing (hpqv.pairing) and is never replaced silently: the same
fingerprint refreshes its record; a different key under a known name becomes a
separate entry that the UI flags. Writes are atomic (temp file + fsync +
os.replace), so a failed write leaves the previous file intact; an unreadable
file raises instead of being overwritten.
"""
import datetime
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hpqv import identity

SCHEMA = 1


class TrustStoreError(Exception):
    """The store could not be read or written; nothing was changed."""


@dataclass(frozen=True)
class Friend:
    fingerprint: str
    label: str           # the peer's self-chosen name (cert CN); a label, not an identity
    cert_pem: bytes
    paired_at: str


def _label(cert_pem: bytes) -> str:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    names = x509.load_pem_x509_certificate(cert_pem).subject.get_attributes_for_oid(
        NameOID.COMMON_NAME)
    text = names[0].value if names else "?"
    return "".join(ch for ch in str(text) if ch.isprintable())[:32] or "?"


class TrustStore:
    def __init__(self, path):
        self.path = Path(path)

    def friends(self) -> list[Friend]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as e:
            raise TrustStoreError(f"cannot read {self.path}: {e}") from e
        try:
            doc = json.loads(raw)
            if doc.get("schema") != SCHEMA:
                raise ValueError(f"unknown schema {doc.get('schema')!r}")
            out = [Friend(f["fingerprint"], f["label"], f["cert_pem"].encode(), f["paired_at"])
                   for f in doc["peers"]]
            for f in out:
                if identity.fingerprint(f.cert_pem) != f.fingerprint:
                    raise ValueError("a record's fingerprint does not match its certificate")
            return out
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise TrustStoreError(f"{self.path} is damaged ({e}); it was left untouched. "
                                  "Restore it or delete it and pair again.") from e

    def trusted_certs(self) -> list[bytes]:
        return [f.cert_pem for f in self.friends()]

    def get(self, fingerprint: str) -> Friend | None:
        return next((f for f in self.friends() if f.fingerprint == fingerprint), None)

    def by_label(self, label: str) -> list[Friend]:
        return [f for f in self.friends() if f.label == label]

    def find(self, query: str) -> list[Friend]:
        """Friends whose label equals `query` or whose fingerprint starts with it."""
        q = query.replace(" ", "").lower()
        return [f for f in self.friends()
                if f.label == query or (q and f.fingerprint.replace(" ", "").startswith(q))]

    def add(self, cert_pem: bytes) -> Friend:
        friends = self.friends()                  # raises on a damaged file: never overwrite it
        fp = identity.fingerprint(cert_pem)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        new = Friend(fp, _label(cert_pem), cert_pem, now)
        self._write([f for f in friends if f.fingerprint != fp] + [new])
        return new

    def remove(self, fingerprint: str) -> bool:
        friends = self.friends()
        keep = [f for f in friends if f.fingerprint != fingerprint]
        if len(keep) != len(friends):
            self._write(keep)
        return len(keep) != len(friends)

    def _write(self, friends: list[Friend]) -> None:
        doc = {"schema": SCHEMA, "peers": [
            {"fingerprint": f.fingerprint, "label": f.label,
             "cert_pem": f.cert_pem.decode(), "paired_at": f.paired_at} for f in friends]}
        data = json.dumps(doc, indent=1).encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".trust-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as f:       # mkstemp creates it 0600 on POSIX
                if identity._IS_WINDOWS:
                    identity._restrict_windows_acl(tmp)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise TrustStoreError(f"could not save {self.path}: {e}") from e
