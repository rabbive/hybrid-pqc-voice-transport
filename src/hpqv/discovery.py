"""LAN discovery by UDP broadcast (B2). Untrusted: a phonebook, never a trust signal.

Each announcement is <= 512 B of JSON with a display name, the TCP port, the first
16 hex characters of the identity fingerprint and whether a pairing window is
open. Entries expire after 6 s; at most 64 are kept; our own is ignored. Trust is
decided only by the pinned key in TLS.
"""
import json
import socket
import threading
import time
from dataclasses import dataclass

PORT = 47123
ANNOUNCE_EVERY = 2.0
EXPIRE_AFTER = 6.0
MAX_PEERS = 64
MAX_ANNOUNCE = 512


@dataclass(frozen=True)
class Seen:
    name: str
    address: str
    tcp_port: int
    fp_hint: str
    pairing: bool
    last_seen: float


def _hint(fingerprint: str) -> str:
    return fingerprint.replace(" ", "")[:16]


def encode(name: str, tcp_port: int, fingerprint: str, pairing: bool) -> bytes:
    name = "".join(ch for ch in name if ch.isprintable())[:32]
    data = json.dumps({"app": "hpqv", "v": 2, "name": name, "port": tcp_port,
                       "fp": _hint(fingerprint), "pairing": bool(pairing)}).encode()
    assert len(data) <= MAX_ANNOUNCE
    return data


def decode(data: bytes, address: str, now: float) -> Seen | None:
    if len(data) > MAX_ANNOUNCE:
        return None
    try:
        d = json.loads(data)
        name, port, fp, pairing = d["name"], d["port"], d["fp"], d["pairing"]
        ok = (d.get("app") == "hpqv" and d.get("v") == 2 and isinstance(name, str)
              and len(name) <= 32 and isinstance(port, int) and 0 < port < 65536
              and isinstance(fp, str) and len(fp) == 16
              and all(c in "0123456789abcdef" for c in fp) and isinstance(pairing, bool))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return Seen(name, address, port, fp, pairing, now) if ok else None


class Discovery:
    def __init__(self, name: str, tcp_port: int, fingerprint: str, *, port: int = PORT,
                 send_to=("255.255.255.255", PORT), clock=time.monotonic):
        self.name, self.tcp_port, self._fp = name, tcp_port, _hint(fingerprint)
        self._fingerprint = fingerprint
        self._port, self._send_to, self._clock = port, send_to, clock
        self._lock = threading.Lock()
        self._seen: dict = {}
        self.pairing = False
        self._stop = threading.Event()
        self._thread = None
        self._sock = None

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", self._port))
        s.settimeout(0.5)
        self._sock = s
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        if self._sock is not None:
            self._sock.close()

    def peers(self) -> list[Seen]:
        now = self._clock()
        with self._lock:
            live = [s for s in self._seen.values() if now - s.last_seen < EXPIRE_AFTER]
        return sorted(live, key=lambda s: -s.last_seen)

    def observe(self, data: bytes, address: str) -> None:
        seen = decode(data, address, self._clock())
        if seen is None or seen.fp_hint == self._fp:
            return
        with self._lock:
            now = self._clock()
            for k in [k for k, v in self._seen.items() if now - v.last_seen >= EXPIRE_AFTER]:
                del self._seen[k]
            key = (seen.fp_hint, seen.address, seen.tcp_port)
            if key not in self._seen and len(self._seen) >= MAX_PEERS:
                return
            self._seen[key] = seen

    def _run(self) -> None:
        next_announce = 0.0
        while not self._stop.is_set():
            if self._clock() >= next_announce:
                try:
                    self._sock.sendto(encode(self.name, self.tcp_port, self._fingerprint,
                                             self.pairing), self._send_to)
                except OSError:
                    pass                  # broadcast blocked (isolated Wi-Fi, firewall)
                next_announce = self._clock() + ANNOUNCE_EVERY
            try:
                data, (addr, _port) = self._sock.recvfrom(MAX_ANNOUNCE + 1)
            except (socket.timeout, OSError):
                continue
            self.observe(data, addr)
