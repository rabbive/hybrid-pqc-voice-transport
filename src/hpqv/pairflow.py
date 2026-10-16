"""Device identity + trust store + discovery + pairing, wired for the CLI and GUI (B2)."""
import socket
import threading
import time

from hpqv import discovery, identity, pairing, paths
from hpqv.listener import Listener
from hpqv.secure import HandshakeError, SecureChannel
from hpqv.trust import TrustStore

SCAN_SECONDS = 4.0

FAILURE_TEXT = {
    "declined": "you said the codes did not match, so nothing was saved",
    "peer_declined": "the other person said the codes did not match, so nothing was saved",
    "bad_commit": "the other device broke the protocol (possible attacker); nothing was saved",
    "timeout": "pairing timed out; nothing was saved. Start again",
    "peer_closed": "the connection dropped; nothing was saved. Start again",
    "protocol": "the other device sent something unexpected; nothing was saved",
    "self": "that is this device; pair with another one",
    "store_failed": "the friend could not be saved; nothing changed. Start again",
}


def store() -> TrustStore:
    return TrustStore(paths.app_dir() / "trust.json")


def ask_console(code, label, fingerprint, key_changed, cancel) -> bool:
    print("\n  Pairing code:   " + code)
    print(f"  Other device:   {label}  ({fingerprint[-19:]})")
    if key_changed:
        print(f"  WARNING: you already have a friend called {label!r} with a DIFFERENT key.\n"
              "  Only continue if they really reinstalled or changed computer.")
    answer = input("  Does the other screen show exactly this code? [y/N] ")
    return not cancel.is_set() and answer.strip().lower() in ("y", "yes")


def scan(ident, seconds: float = SCAN_SECONDS, *, pairing_only: bool = False):
    """Nearby devices heard on the LAN within `seconds` (untrusted hints)."""
    d = discovery.Discovery(_name(ident), 0, identity.fingerprint(ident.cert_pem))
    d.start()
    try:
        time.sleep(seconds)
        found = d.peers()
    finally:
        d.stop()
    return [s for s in found if s.pairing or not pairing_only]


def resolve(target: str, ident, *, pairing_only: bool, seconds: float = SCAN_SECONDS):
    """HOST:PORT as given, or a nearby device's name / fingerprint prefix."""
    if ":" in target and target.rsplit(":", 1)[1].isdigit():
        host, port = target.rsplit(":", 1)
        return host, int(port)
    q = target.replace(" ", "").lower()
    hits = [s for s in scan(ident, seconds, pairing_only=pairing_only)
            if s.name == target or (q and s.fp_hint.startswith(q[:16]))]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} nearby devices match {target!r}; "
                          "use HOST:PORT or run `peers`")
    return hits[0].address, hits[0].tcp_port


def connect_and_pair(host: str, port: int, ask=ask_console, **kw) -> pairing.PairingResult:
    ident = paths.device_identity()
    sock = socket.create_connection((host, port), timeout=10)
    channel = SecureChannel.establish(sock, "client", ident, None, mode="pair")
    try:
        return pairing.run(channel, store(), ask, **kw)
    finally:
        channel.close()


def allow_pairing(port: int, seconds: float, ask=ask_console, *, announce: bool = True,
                  host: str = "0.0.0.0", on_event=print):
    """Open a pairing window for `seconds`; return the PairingResult or None."""
    ident, st = paths.device_identity(), store()
    window = pairing.PairingWindow(seconds=seconds)
    done, out = threading.Event(), {}

    def on_pair(channel, addr):
        try:
            out["result"] = pairing.run(channel, st, ask)
            done.set()
        except pairing.PairingFailed as e:
            on_event(f"pairing with {addr[0]} failed: {FAILURE_TEXT[e.reason]}")
            raise

    lst = Listener(ident, port, host=host, trusted=st.trusted_certs, window=window,
                   on_pair=on_pair)
    disc = None
    if announce:
        disc = discovery.Discovery(_name(ident), lst.port, identity.fingerprint(ident.cert_pem))
        disc.pairing = True
        disc.start()
    window.open()
    lst.start()
    on_event(f"pairing window open for {seconds:.0f} s on port {lst.port}; "
             f"this device: {_name(ident)} ({identity.fingerprint(ident.cert_pem)[-19:]})")
    try:
        # An attempt already under way finishes on its own timeouts, even if the
        # window's 2 minutes run out meanwhile.
        while not done.is_set() and (window.is_open() or window.busy):
            done.wait(0.2)
    finally:
        lst.stop()
        if disc is not None:
            disc.stop()
    if not done.is_set():
        on_event(f"pairing window closed ({window.closed_reason or 'closed'})")
    return out.get("result")


def _name(ident) -> str:
    from hpqv.trust import _label
    return _label(ident.cert_pem)
