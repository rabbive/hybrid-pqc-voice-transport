"""No-files pairing over an already established hpqv-pair/1 TLS channel (B2).

EXPERIMENTAL: our own application of the commit-then-reveal pattern used by
Bluetooth Numeric Comparison and ZRTP. Not formally verified or externally
reviewed. See docs/reviews/2026-10-16-b2-pairing-design.md (section 3a lists
what this step does and does not provide).

  R -> I  COMMIT   C = SHA-256(LBL_C || CB || fpR || fpI || nR)
  I -> R  NONCE    nI (drawn only after C arrived)
  R -> I  REVEAL   nR (I checks it against C)
  both    code   = SHA-256(LBL_V || CB || fpI || fpR || nI || nR)[0:4] mod 10^6
  both    CONFIRM  1 = this user pressed "Codes match", 0 = no / cancel / timeout

CB is the RFC 9266 channel binding of this TLS session; fp is SHA-256 of the
raw ML-DSA-65 key TLS authenticated. The friend is saved only after the local
user approved AND the peer's CONFIRM = 1 arrived.
"""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from hpqv import identity
from hpqv.trust import Friend, TrustStore, TrustStoreError, _label

log = logging.getLogger(__name__)

MSG_COMMIT, MSG_NONCE, MSG_REVEAL, MSG_CONFIRM = 0x12, 0x13, 0x14, 0x15
LBL_C = b"hpqv-pair/1 commit\x00"
LBL_V = b"hpqv-pair/1 code\x00"
NONCE_LEN = 32
STEP_TIMEOUT = 10.0          # each protocol message
DECISION_TIMEOUT = 60.0      # the human compares the codes
WINDOW_SECONDS = 120.0
MAX_ATTEMPTS = 3

# (code "ddd ddd", peer label, peer fingerprint, key_changed, cancel) -> approved?
AskUser = Callable[[str, str, str, bool, threading.Event], bool]


class PairingFailed(Exception):
    REASONS = ("declined", "peer_declined", "bad_commit", "timeout", "peer_closed",
               "protocol", "self", "store_failed")

    def __init__(self, reason: str, detail: str = ""):
        assert reason in self.REASONS, reason
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason

    @property
    def mismatch(self) -> bool:
        """Outcomes that suggest an attacker: the window must close."""
        return self.reason in ("declined", "peer_declined", "bad_commit")


@dataclass(frozen=True)
class PairingResult:
    friend: Friend
    code: str


def commitment(cb: bytes, fp_r: bytes, fp_i: bytes, n_r: bytes) -> bytes:
    return hashlib.sha256(LBL_C + cb + fp_r + fp_i + bytes(n_r)).digest()


def comparison_code(cb: bytes, fp_i: bytes, fp_r: bytes, n_i: bytes, n_r: bytes) -> str:
    v = hashlib.sha256(LBL_V + cb + fp_i + fp_r + bytes(n_i) + bytes(n_r)).digest()
    code = int.from_bytes(v[:4], "big") % 1_000_000
    return f"{code // 1000:03d} {code % 1000:03d}"


def _fp(cert_pem: bytes) -> bytes:
    return hashlib.sha256(identity.raw_public_key(cert_pem)).digest()


class PairingWindow:
    """Listener side: pairing only while the user has opened it, one at a time,
    at most MAX_ATTEMPTS per opening; a mismatch closes it."""

    def __init__(self, seconds: float = WINDOW_SECONDS, max_attempts: int = MAX_ATTEMPTS,
                 clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock, self._seconds, self._max = clock, seconds, max_attempts
        self._until = None
        self._busy = False
        self.attempts = 0
        self.closed_reason = ""

    def open(self) -> None:
        with self._lock:
            self._until = self._clock() + self._seconds
            self.attempts, self._busy, self.closed_reason = 0, False, ""

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            self._until, self.closed_reason = None, reason

    def is_open(self) -> bool:
        with self._lock:
            return self._open_locked()

    def _open_locked(self) -> bool:
        if self._until is not None and self._clock() >= self._until:
            self._until, self.closed_reason = None, "expired"
        return self._until is not None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def try_begin(self) -> bool:
        with self._lock:
            if not self._open_locked() or self._busy or self.attempts >= self._max:
                return False
            self._busy = True
            self.attempts += 1
            return True

    def finish(self, failure: "PairingFailed | None") -> None:
        with self._lock:
            self._busy = False
        if failure is not None and failure.mismatch:
            self.close("codes did not match")


def _recv(channel, msg_type: int, length: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise PairingFailed("timeout", f"waiting for message {msg_type:#x}")
        try:
            msg = channel.recv(timeout=min(left, 0.2))
        except ConnectionError as e:
            raise PairingFailed("peer_closed", str(e)) from e
        except ValueError as e:
            raise PairingFailed("protocol", str(e)) from e
        if msg is None:
            continue
        got, payload = msg
        if got != msg_type or len(payload) != length:
            raise PairingFailed("protocol", f"expected {msg_type:#x}/{length} B, "
                                            f"got {got:#x}/{len(payload)} B")
        return payload


def _send(channel, msg_type: int, payload: bytes) -> None:
    try:
        channel.send(msg_type, payload)
    except (ConnectionError, OSError) as e:
        raise PairingFailed("peer_closed", str(e)) from e


def _decide(channel, ask_user: AskUser, code: str, label: str, fingerprint: str,
            key_changed: bool, decision_timeout: float) -> None:
    """Ask the local user while watching for the peer's CONFIRM; both must be 1."""
    cancel = threading.Event()
    answer: dict = {}

    def ask():
        try:
            answer["ok"] = bool(ask_user(code, label, fingerprint, key_changed, cancel))
        except Exception:                    # a broken UI is a "no", never a "yes"
            answer["ok"] = False
    threading.Thread(target=ask, daemon=True).start()

    deadline = time.monotonic() + decision_timeout
    sent = peer = None
    try:
        while True:
            if sent is None and "ok" in answer:
                sent = answer["ok"]
                _send(channel, MSG_CONFIRM, b"\x01" if sent else b"\x00")
                if not sent:
                    raise PairingFailed("declined", "this user said the codes differ")
            if sent and peer:
                return
            left = deadline - time.monotonic()
            if left <= 0:
                if sent is None:
                    _send(channel, MSG_CONFIRM, b"\x00")
                raise PairingFailed("timeout", "no decision in time")
            if peer is None:
                try:
                    msg = channel.recv(timeout=min(left, 0.1))
                except ConnectionError as e:
                    raise PairingFailed("peer_closed", str(e)) from e
                except ValueError as e:
                    raise PairingFailed("protocol", str(e)) from e
                if msg is not None:
                    if msg[0] != MSG_CONFIRM or msg[1] not in (b"\x00", b"\x01"):
                        raise PairingFailed("protocol", f"unexpected message {msg[0]:#x}")
                    peer = msg[1] == b"\x01"
                    if not peer:
                        raise PairingFailed("peer_declined", "the other user said no")
            else:
                time.sleep(0.02)
    finally:
        cancel.set()                         # dismiss a still-open prompt


def run(channel, store: TrustStore, ask_user: AskUser, *, step_timeout: float = STEP_TIMEOUT,
        decision_timeout: float = DECISION_TIMEOUT) -> PairingResult:
    """Run the pairing exchange on an established hpqv-pair/1 channel.

    Raises PairingFailed; on any failure nothing is saved and every nonce is wiped.
    The caller closes the channel afterwards in every case.
    """
    if getattr(channel, "mode", None) != "pair":
        raise PairingFailed("protocol", "not a pairing channel")
    own_cert = channel.local_cert_pem
    peer_cert = channel.peer_cert_pem
    fp_self, fp_peer = _fp(own_cert), _fp(peer_cert)
    if fp_self == fp_peer:
        raise PairingFailed("self", "cannot pair a device with itself")
    initiator = channel.role == "client"
    fp_i, fp_r = (fp_self, fp_peer) if initiator else (fp_peer, fp_self)
    cb = channel.channel_binding()
    n_i = bytearray(NONCE_LEN)
    n_r = bytearray(NONCE_LEN)
    try:
        if initiator:
            c = _recv(channel, MSG_COMMIT, 32, step_timeout)
            n_i[:] = secrets.token_bytes(NONCE_LEN)      # only now: after C is fixed
            _send(channel, MSG_NONCE, bytes(n_i))
            n_r[:] = _recv(channel, MSG_REVEAL, NONCE_LEN, step_timeout)
            if not hmac.compare_digest(c, commitment(cb, fp_r, fp_i, n_r)):
                raise PairingFailed("bad_commit", "revealed nonce does not match commitment")
        else:
            n_r[:] = secrets.token_bytes(NONCE_LEN)
            _send(channel, MSG_COMMIT, commitment(cb, fp_r, fp_i, n_r))
            n_i[:] = _recv(channel, MSG_NONCE, NONCE_LEN, step_timeout)
            _send(channel, MSG_REVEAL, bytes(n_r))
        code = comparison_code(cb, fp_i, fp_r, n_i, n_r)
        label = _label(peer_cert)
        fingerprint = identity.fingerprint(peer_cert)
        try:
            known = store.by_label(label)
        except TrustStoreError as e:
            raise PairingFailed("store_failed", str(e)) from e
        key_changed = bool(known) and all(f.fingerprint != fingerprint for f in known)
        _decide(channel, ask_user, code, label, fingerprint, key_changed, decision_timeout)
        try:
            friend = store.add(peer_cert)
        except TrustStoreError as e:
            raise PairingFailed("store_failed", str(e)) from e
        log.info("paired", extra={"peer_fingerprint": fingerprint, "role": channel.role})
        return PairingResult(friend, code)
    except PairingFailed as e:
        log.warning("pairing failed", extra={"reason": e.reason, "role": channel.role})
        raise
    finally:
        n_i[:] = bytes(NONCE_LEN)                        # best effort: Python may copy
        n_r[:] = bytes(NONCE_LEN)
