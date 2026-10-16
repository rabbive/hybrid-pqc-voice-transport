"""v2 control channel: TLS 1.3 over TCP via pyOpenSSL.

X25519MLKEM768 key exchange, mutual auth with pinned self-signed ML-DSA-65 certs,
ALPN hpqv/2, and UDP media secrets from the TLS exporter. The SSL object runs on
memory BIOs so this module owns every socket read and write: that gives one
place for timeouts, byte counts and the v1-peer check, and keeps SSL calls off
the socket so a reader thread can wait in select() without holding the lock.
"""
import logging
import select
import socket
import threading
import time
import warnings
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import mldsa
from OpenSSL import SSL, crypto

from hpqv import identity

log = logging.getLogger(__name__)

GROUP = "X25519MLKEM768"
ALPN = b"hpqv/2"
PROTOCOL = "TLSv1.3"
HANDSHAKE_TIMEOUT = 10.0
MAX_CONTROL_PAYLOAD = 1024

MSG_READY = 4               # == control.MSG_READY (control.py gains it in wave 2)
SEND_TIMEOUT = 10.0         # a peer that stops reading cannot stall a sender forever
_READ_CHUNK = 65536
_TLS_HANDSHAKE_RECORD = 0x16
_WARNINGS_LOCK = threading.Lock()   # catch_warnings() mutates process-global state

_LABEL_C2S = b"EXPORTER-hpqv2 media c2s"
_LABEL_S2C = b"EXPORTER-hpqv2 media s2c"
_LABEL_SESSION_ID = b"EXPORTER-hpqv2 session id"
_LABEL_SAS = b"EXPORTER-hpqv2 sas"


class HandshakeError(Exception):
    REASONS = ("timeout", "peer_closed", "bad_peer_cert", "wrong_group",
               "alpn_mismatch", "protocol_v1_peer", "tls_error", "not_ready")

    def __init__(self, reason: str, detail: str = ""):
        assert reason in self.REASONS, reason
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class MediaKeys:
    send_secret: bytes = field(repr=False)   # 32 B, exporter, this side -> peer
    recv_secret: bytes = field(repr=False)   # 32 B, exporter, peer -> this side
    session_id: bytes                        # 4 B, same on both sides


def _raw_key(cert) -> bytes | None:
    key = cert.public_key()
    return key.public_bytes_raw() if isinstance(key, mldsa.MLDSA65PublicKey) else None


def _context(ident: identity.Identity, peer_cert_pem: bytes, role: str,
             rejected: list) -> SSL.Context:
    pin = identity.raw_public_key(peer_cert_pem)        # ValueError unless ML-DSA-65
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    # No resumption: no stateless tickets, and no session cache for a stateful one to
    # name (OpenSSL still sends stateful tickets; nothing can redeem them).
    ctx.set_options(SSL.OP_NO_TICKET)
    ctx.set_session_cache_mode(SSL.SESS_CACHE_OFF)
    ctx.use_certificate(x509.load_pem_x509_certificate(ident.cert_pem))
    with _WARNINGS_LOCK, warnings.catch_warnings():
        # pyOpenSSL rejects cryptography ML-DSA keys ("Unsupported key type"), so the key
        # goes in through OpenSSL's own PEM loader, in memory (no temp file).
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.use_privatekey(crypto.load_privatekey(crypto.FILETYPE_PEM, ident.key_pem))
    ctx.check_privatekey()

    def verify(conn, cert, errno, depth, ok):
        # The pin replaces chain validation: only the exact pinned key at depth 0 passes,
        # so the expected self-signed error is ignored for that key and nothing else.
        try:
            good = depth == 0 and _raw_key(cert.to_cryptography()) == pin
        except Exception:
            good = False
        if not good:
            rejected.append(depth)
        return good

    ctx.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, verify)
    if role == "client":
        ctx.set_alpn_protos([ALPN])
    else:
        ctx.set_alpn_select_callback(
            lambda conn, offered: ALPN if ALPN in offered else SSL.NO_OVERLAPPING_PROTOCOLS)
    return ctx


class SecureChannel:
    """One TLS connection. Build with establish(); send() is thread-safe; one reader thread."""

    def __init__(self, sock: socket.socket, conn: SSL.Connection, role: str):
        self._sock, self._conn, self.role = sock, conn, role
        # ponytail: one lock covers SSL calls *and* socket writes, which keeps TLS records
        # in order. A sender blocked by TCP backpressure holds it for up to SEND_TIMEOUT;
        # fine for small control messages, use an outbound queue if this carries bulk data.
        self._lock = threading.Lock()
        self._rbuf = bytearray()       # at most one control frame of plaintext
        self._closed = False
        self._broken = ""              # why the stream became unusable, if it did
        self._nbytes = 0
        self.group = self.cipher = self.protocol = self.peer_fingerprint = ""
        self.handshake_ms = 0.0
        self.handshake_bytes = 0

    # -- establish ----------------------------------------------------------------

    @classmethod
    def establish(cls, sock, role, ident: identity.Identity, peer_cert_pem: bytes,
                  timeout: float = HANDSHAKE_TIMEOUT) -> "SecureChannel":
        if role not in ("client", "server"):
            raise ValueError(f"role must be 'client' or 'server', not {role!r}")
        t0 = time.perf_counter()
        deadline = time.monotonic() + timeout
        rejected: list = []
        conn = SSL.Connection(_context(ident, peer_cert_pem, role, rejected), None)
        conn.set_connect_state() if role == "client" else conn.set_accept_state()
        ch = cls(sock, conn, role)
        sock.settimeout(timeout)
        try:
            try:
                ch._handshake(deadline, rejected)
                ch._check_negotiated(peer_cert_pem)
                if role == "server":
                    ch._send_frame(bytes([MSG_READY, 0, 0]))
                else:
                    ch._await_ready(deadline)
            except TimeoutError as e:
                raise HandshakeError("timeout", "socket write timed out") from e
            except OSError as e:
                raise HandshakeError("peer_closed", type(e).__name__) from e
        except HandshakeError as e:
            log.warning("secure channel handshake failed",
                        extra={"role": role, "reason": e.reason})
            ch._abort()
            raise
        sock.settimeout(SEND_TIMEOUT)
        c2s = conn.export_keying_material(_LABEL_C2S, 32, b"")
        s2c = conn.export_keying_material(_LABEL_S2C, 32, b"")
        send, recv = (c2s, s2c) if role == "client" else (s2c, c2s)
        ch._keys = MediaKeys(send_secret=send, recv_secret=recv,
                             session_id=conn.export_keying_material(_LABEL_SESSION_ID, 4, b""))
        ch._sas = conn.export_keying_material(_LABEL_SAS, 4, b"").hex()
        ch.group = conn.get_group_name()
        ch.cipher = conn.get_cipher_name()
        ch.protocol = conn.get_protocol_version_name()
        ch.peer_fingerprint = identity.fingerprint(peer_cert_pem)
        ch.handshake_ms = (time.perf_counter() - t0) * 1000
        ch.handshake_bytes = ch._nbytes
        log.info("secure channel established", extra={
            "role": role, "group": ch.group, "cipher": ch.cipher, "protocol": ch.protocol,
            "handshake_ms": round(ch.handshake_ms, 2), "handshake_bytes": ch.handshake_bytes,
            "peer_fingerprint": ch.peer_fingerprint})
        return ch

    def _handshake(self, deadline: float, rejected: list) -> None:
        first = True
        while True:
            try:
                self._conn.do_handshake()
                self._flush()
                return
            except SSL.WantReadError:
                self._flush()
            except SSL.Error as e:
                self._flush_quietly()               # deliver our alert to the peer
                if isinstance(e, SSL.ZeroReturnError):
                    raise HandshakeError("peer_closed") from e
                text = str(e)
                if rejected or "did not return a certificate" in text:
                    raise HandshakeError("bad_peer_cert", "peer key is not the pinned key") from e
                raise HandshakeError("tls_error", text) from e
            data = self._read_raw(deadline)
            if first and self.role == "server" and data[0] != _TLS_HANDSHAKE_RECORD:
                raise HandshakeError(
                    "protocol_v1_peer",
                    "peer speaks the v1 protocol (length-prefixed records, not TLS 1.3); "
                    "upgrade both peers to v2")
            first = False
            self._conn.bio_write(data)

    def _check_negotiated(self, peer_cert_pem: bytes) -> None:
        conn = self._conn
        if conn.get_protocol_version_name() != PROTOCOL:
            raise HandshakeError("tls_error", f"negotiated {conn.get_protocol_version_name()}")
        if conn.get_group_name() != GROUP:
            raise HandshakeError("wrong_group", f"negotiated {conn.get_group_name()}")
        if conn.get_alpn_proto_negotiated() != ALPN:
            raise HandshakeError("alpn_mismatch", "peer did not agree on hpqv/2")
        peer = conn.get_peer_certificate(as_cryptography=True)
        if peer is None or _raw_key(peer) != identity.raw_public_key(peer_cert_pem):
            raise HandshakeError("bad_peer_cert", "peer key is not the pinned key")

    def _await_ready(self, deadline: float) -> None:
        # A TLS 1.3 client finishes before the server has checked the client cert;
        # READY is the server saying it did.
        while True:
            try:
                frame = self._try_frame()
            except SSL.ZeroReturnError as e:
                raise HandshakeError("peer_closed") from e
            except (SSL.Error, ValueError) as e:
                raise HandshakeError("not_ready", "server refused the session") from e
            if frame is not None:
                if frame != (MSG_READY, b""):
                    raise HandshakeError("not_ready", "first message was not READY")
                return
            self._conn.bio_write(self._read_raw(deadline))

    def _read_raw(self, deadline: float) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self._sock], [], [], remaining)[0]:
            raise HandshakeError("timeout")
        data = self._sock.recv(_READ_CHUNK)
        if not data:
            raise HandshakeError("peer_closed")
        self._nbytes += len(data)
        return data

    # -- record I/O (callers of these hold self._lock once established) ----------

    def _pending_out(self) -> bytes:
        chunks = []
        while True:
            try:
                chunks.append(self._conn.bio_read(_READ_CHUNK))
            except SSL.WantReadError:
                return b"".join(chunks)

    def _flush(self) -> None:
        out = self._pending_out()
        if out:
            self._sock.sendall(out)
            self._nbytes += len(out)

    def _flush_quietly(self) -> None:
        try:
            self._flush()
        except (OSError, SSL.Error):
            pass

    def _send_frame(self, frame: bytes) -> None:
        self._conn.sendall(frame)
        self._flush()

    def _try_frame(self) -> tuple[int, bytes] | None:
        """Next complete frame from the SSL object, or None if it needs more input."""
        buf = self._rbuf
        while True:
            if len(buf) >= 3:
                length = int.from_bytes(buf[1:3], "big")
                if length > MAX_CONTROL_PAYLOAD:
                    raise ValueError(f"control payload of {length} B exceeds "
                                     f"{MAX_CONTROL_PAYLOAD} B")
                need = 3 + length - len(buf)
                if need == 0:
                    frame = (buf[0], bytes(buf[3:]))
                    buf.clear()
                    return frame
            else:
                need = 3 - len(buf)
            try:
                buf += self._conn.recv(need)
            except SSL.WantReadError:
                return None

    def _usable(self) -> None:
        if self._closed:
            raise ConnectionError("secure channel is closed")
        if self._broken:
            raise ConnectionError(f"secure channel is unusable: {self._broken}")

    def _fail(self, why: str) -> None:
        self._broken = self._broken or why
        self._flush_quietly()                          # e.g. our fatal alert

    # -- public API ---------------------------------------------------------------

    def send(self, msg_type: int, payload: bytes = b"") -> None:
        if not 0 <= msg_type <= 255:
            raise ValueError(f"msg_type {msg_type} is not one byte")
        if len(payload) > MAX_CONTROL_PAYLOAD:
            raise ValueError(f"control payload of {len(payload)} B exceeds "
                             f"{MAX_CONTROL_PAYLOAD} B")
        frame = bytes([msg_type]) + len(payload).to_bytes(2, "big") + payload
        with self._lock:
            self._usable()
            try:
                self._send_frame(frame)
            except (OSError, SSL.Error) as e:
                self._fail(f"send failed ({type(e).__name__})")
                raise ConnectionError(self._broken) from e

    def recv(self, timeout: float | None = None) -> tuple[int, bytes] | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._usable()
                try:
                    frame = self._try_frame()
                    self._flush()                  # anything TLS wants to answer
                except ValueError:
                    self._fail("malformed control frame")
                    raise
                except SSL.ZeroReturnError as e:
                    self._fail("peer closed")
                    raise ConnectionError("peer closed") from e
                except (SSL.Error, OSError) as e:
                    self._fail(f"TLS record rejected ({type(e).__name__})")
                    raise ConnectionError(self._broken) from e
                if frame is not None:
                    return frame
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                if not select.select([self._sock], [], [], wait)[0]:
                    return None
                data = self._sock.recv(_READ_CHUNK)
            except (OSError, ValueError):              # reset, or closed under us
                data = b""
            with self._lock:
                self._usable()
                if not data:
                    self._broken = self._broken or "peer closed"
                    raise ConnectionError("peer closed")
                self._conn.bio_write(data)

    def media_keys(self) -> MediaKeys:
        return self._keys

    def sas(self) -> str:
        return self._sas

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._sock.settimeout(1.0)
                self._conn.shutdown()                  # close_notify, best effort
                self._flush()
            except (OSError, SSL.Error):
                pass
        self._close_socket()

    def _abort(self) -> None:
        self._closed = True
        self._close_socket()

    def _close_socket(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)      # wakes a reader blocked in select()
        except OSError:
            pass
        self._sock.close()


def local_pair(timeout: float = HANDSHAKE_TIMEOUT) -> tuple[SecureChannel, SecureChannel]:
    """(client, server) with fresh in-memory identities over a 127.0.0.1 TCP pair."""
    caller, listener = identity.new("caller"), identity.new("listener")
    with socket.create_server(("127.0.0.1", 0)) as ls:
        csock = socket.create_connection(ls.getsockname(), timeout=timeout)
        ssock, _ = ls.accept()
    result: dict = {}

    def serve():
        try:
            result["server"] = SecureChannel.establish(ssock, "server", listener,
                                                       caller.cert_pem, timeout)
        except Exception as e:
            result["server"] = e

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        client = SecureChannel.establish(csock, "client", caller, listener.cert_pem, timeout)
    except Exception as e:
        client = e
    t.join(timeout + 1)
    server = result.get("server", HandshakeError("timeout"))
    if isinstance(client, SecureChannel) and isinstance(server, SecureChannel):
        return client, server
    for ch in (client, server):
        if isinstance(ch, SecureChannel):
            ch.close()
    raise client if isinstance(client, Exception) else server
