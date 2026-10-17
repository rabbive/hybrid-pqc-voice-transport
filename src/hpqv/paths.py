"""Per-user application directory and this device's own identity (B2).

macOS ~/Library/Application Support/hpqv, Linux $XDG_DATA_HOME/hpqv (default
~/.local/share/hpqv), Windows %LOCALAPPDATA%\\hpqv (local, so a private key does
not roam with a domain profile). HPQV_HOME overrides it, for tests and demos.
"""
import os
import socket
import sys
from pathlib import Path

from hpqv import identity

HOME_ENV = "HPQV_HOME"


def app_dir() -> Path:
    if os.environ.get(HOME_ENV):
        d = Path(os.environ[HOME_ENV])
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "hpqv"
    elif os.name == "nt":
        d = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hpqv"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "hpqv"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def device_identity(name: str | None = None) -> identity.Identity:
    """Load this device's identity, creating it on first use (name: the cert CN)."""
    key, crt = app_dir() / "identity.key", app_dir() / "identity.crt"
    if not key.exists():
        identity.save(identity.new(name or socket.gethostname()[:32] or "hpqv"), str(key), str(crt))
    return identity.load(str(key), str(crt))
