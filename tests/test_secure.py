"""v2 control channel: TLS 1.3, X25519MLKEM768, mutually pinned ML-DSA-65, over real sockets."""
import logging
import os
import socket
import struct
import threading
import time
import warnings

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from OpenSSL import SSL, crypto

from hpqv import identity, secure
from hpqv.secure import HandshakeError, SecureChannel

T = 3.0                                  # per-test handshake timeout (seconds)
CLIENT, SERVER, MALLORY = identity.new("caller"), identity.new("listener"), identity.new("mallory")


def _listener():
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(4)
    return ls


def _attempt(fn, *args):
    try:
        return fn(*args)
    except Exception as e:           # returned so the test can assert on both sides
        return e


def _run_pair(client_pin=SERVER.cert_pem, server_pin=CLIENT.cert_pem, timeout=T,
              client_ident=CLIENT, server_ident=SERVER, proxy=None):
    """Run establish() on both ends over loopback, optionally through a proxy."""
    ls = _listener()
    out = {}

    def serve():
        s, _ = ls.accept()
        out["server"] = _attempt(SecureChannel.establish, s, "server", server_ident,
                                 server_pin, timeout)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    addr = ls.getsockname() if proxy is None else proxy.start(ls.getsockname())
    c = socket.create_connection(addr)
    out["client"] = _attempt(SecureChannel.establish, c, "client", client_ident,
                             client_pin, timeout)
    t.join(timeout + 5)
    ls.close()
    assert not t.is_alive(), "server establish() hung past its timeout"
    return out["client"], out["server"]


def _close(*chans):
    for ch in chans:
        if isinstance(ch, SecureChannel):
            ch.close()


class RecordProxy:
    """TCP proxy that forwards whole TLS records and lets a hook rewrite each one."""

    def __init__(self):
        self.hooks = {"c2s": None, "s2c": None}   # fn(record) -> bytes to forward
        self.socks = []
        self._ls = _listener()

    def start(self, target):
        def accept():
            a, _ = self._ls.accept()
            b = socket.create_connection(target)
            self.socks += [a, b]
            for src, dst, d in ((a, b, "c2s"), (b, a, "s2c")):
                threading.Thread(target=self._pump, args=(src, dst, d), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()
        return self._ls.getsockname()

    def _pump(self, src, dst, direction):
        buf = b""
        try:
            while data := src.recv(65536):
                buf += data
                while len(buf) >= 5 and len(buf) >= 5 + int.from_bytes(buf[3:5], "big"):
                    n = 5 + int.from_bytes(buf[3:5], "big")
                    rec, buf = buf[:n], buf[n:]
                    hook = self.hooks[direction]
                    dst.sendall(hook(rec) if hook else rec)
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def kill(self):
        for s in self.socks + [self._ls]:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()


def _raw_ctx(ident, *, cert=True, alpn=None, p256_only=False, server=False):
    """A plain pyOpenSSL peer that does NOT follow our rules (for negative tests)."""
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    if cert:
        ctx.use_certificate(x509.load_pem_x509_certificate(ident.cert_pem))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.use_privatekey(crypto.load_privatekey(crypto.FILETYPE_PEM, ident.key_pem))
    ctx.set_verify(SSL.VERIFY_PEER, lambda *a: True)
    if alpn and server:
        ctx.set_alpn_select_callback(lambda conn, protos: alpn if alpn in protos
                                     else SSL.NO_OVERLAPPING_PROTOCOLS)
    elif alpn:
        ctx.set_alpn_protos([alpn])
    if p256_only:
        ctx.set_tmp_ecdh(ec.SECP256R1())
    return ctx


def _raw_peer(sock, ctx, server):
    conn = SSL.Connection(ctx, sock)
    sock.settimeout(None)            # pyOpenSSL needs a blocking socket; we close it
    conn.set_accept_state() if server else conn.set_connect_state()
    try:
        conn.do_handshake()
        conn.recv(1024)                 # hold the connection until the other side closes
    except (SSL.Error, OSError):
        pass
    finally:
        sock.close()


def _ours_vs_raw(our_role, raw_ctx, our_pin, timeout=T):
    """Our establish() against a raw pyOpenSSL peer; returns our result."""
    ls = _listener()
    if our_role == "server":
        c = socket.create_connection(ls.getsockname())
        t = threading.Thread(target=_raw_peer, args=(c, raw_ctx, False), daemon=True)
        t.start()
        s, _ = ls.accept()
        res = _attempt(SecureChannel.establish, s, "server", SERVER, our_pin, timeout)
    else:
        c = socket.create_connection(ls.getsockname())
        s, _ = ls.accept()
        t = threading.Thread(target=_raw_peer, args=(s, raw_ctx, True), daemon=True)
        t.start()
        res = _attempt(SecureChannel.establish, c, "client", CLIENT, our_pin, timeout)
    t.join(T + 2)
    ls.close()
    _close(res)
    return res


def _assert_fail(res, *reasons):
    assert isinstance(res, HandshakeError), f"expected HandshakeError, got {res!r}"
    if reasons:
        assert res.reason in reasons, res.reason


# 1 ---------------------------------------------------------------------------------

def test_mutual_handshake_negotiates_pq_group_and_mirrored_keys():
    client, server = _run_pair()
    try:
        assert isinstance(client, SecureChannel), client
        assert isinstance(server, SecureChannel), server
        assert (client.role, server.role) == ("client", "server")
        for ch in (client, server):
            assert ch.protocol == "TLSv1.3" == secure.PROTOCOL
            assert ch.group == "X25519MLKEM768" == secure.GROUP
            assert ch.cipher.startswith("TLS_")
            assert ch.handshake_ms > 0
            assert ch.handshake_bytes > 10_000      # two ML-DSA-65 certs + signatures
        assert client.peer_fingerprint == identity.fingerprint(SERVER.cert_pem)
        assert server.peer_fingerprint == identity.fingerprint(CLIENT.cert_pem)
        ck, sk = client.media_keys(), server.media_keys()
        assert ck.send_secret == sk.recv_secret and ck.recv_secret == sk.send_secret
        assert len(ck.send_secret) == len(ck.recv_secret) == 32
        assert ck.send_secret != ck.recv_secret
        assert ck.session_id == sk.session_id and len(ck.session_id) == 4
        assert client.sas() == server.sas()
        assert len(client.sas()) == 8 and int(client.sas(), 16) >= 0
        assert ck.send_secret.hex() not in repr(ck)
        client.send(1, b"call start")
        assert server.recv(T) == (1, b"call start")
        server.send(3, b"")
        assert client.recv(T) == (3, b"")
    finally:
        _close(client, server)


def test_sessions_are_independent():
    a = _run_pair()
    b = _run_pair()
    try:
        assert a[0].media_keys().send_secret != b[0].media_keys().send_secret
    finally:
        _close(*a, *b)


def test_local_pair_returns_connected_client_and_server():
    client, server = secure.local_pair()
    try:
        assert (client.role, server.role) == ("client", "server")
        assert client.media_keys().send_secret == server.media_keys().recv_secret
        server.send(2, b"bye")
        assert client.recv(T) == (2, b"bye")
    finally:
        _close(client, server)


# 2, 3, 4 ---------------------------------------------------------------------------

def test_client_rejects_unpinned_server_key():
    client, server = _run_pair(client_pin=MALLORY.cert_pem)
    _close(client, server)
    _assert_fail(client, "bad_peer_cert")
    _assert_fail(server)


def test_server_rejects_unpinned_client_key_and_client_gets_no_channel():
    client, server = _run_pair(server_pin=MALLORY.cert_pem)
    _close(client, server)
    _assert_fail(server, "bad_peer_cert")
    _assert_fail(client, "not_ready", "peer_closed")


def test_server_rejects_client_without_certificate():
    res = _ours_vs_raw("server", _raw_ctx(None, cert=False, alpn=secure.ALPN), CLIENT.cert_pem)
    _assert_fail(res, "bad_peer_cert")


# 5 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("alpn", [None, b"hpqv/1"])
def test_server_requires_alpn(alpn):
    res = _ours_vs_raw("server", _raw_ctx(CLIENT, alpn=alpn), CLIENT.cert_pem)
    _assert_fail(res, "alpn_mismatch")


def test_client_requires_server_to_select_alpn():
    res = _ours_vs_raw("client", _raw_ctx(SERVER, server=True), SERVER.cert_pem)
    _assert_fail(res, "alpn_mismatch")


def test_wrong_group_is_refused_by_client_and_server():
    """We offer only X25519MLKEM768, so a P-256-only peer fails inside the handshake."""
    res = _ours_vs_raw("client", _raw_ctx(SERVER, server=True, alpn=secure.ALPN,
                                          p256_only=True), SERVER.cert_pem)
    _assert_fail(res, "tls_error")
    res = _ours_vs_raw("server", _raw_ctx(CLIENT, alpn=secure.ALPN, p256_only=True),
                       CLIENT.cert_pem)
    _assert_fail(res, "tls_error")


def test_post_handshake_group_check_is_the_backstop(monkeypatch):
    """If the group restriction were ever lost (private binding gone), the runtime check
    still refuses a non-hybrid group after the handshake."""
    monkeypatch.setattr(secure._lib, "SSL_CTX_set1_curves_list", lambda ctx, groups: 1)
    res = _ours_vs_raw("client", _raw_ctx(SERVER, server=True, alpn=secure.ALPN,
                                          p256_only=True), SERVER.cert_pem)
    _assert_fail(res, "wrong_group")
    res = _ours_vs_raw("server", _raw_ctx(CLIENT, alpn=secure.ALPN, p256_only=True),
                       CLIENT.cert_pem)
    _assert_fail(res, "wrong_group")


# 6 ---------------------------------------------------------------------------------

def _flip_first(ctype, offset):
    done = []

    def hook(rec):
        if done or rec[0] != ctype:
            return rec
        done.append(1)
        b = bytearray(rec)
        b[5 + min(offset, len(rec) - 6)] ^= 0x01
        return bytes(b)
    return hook


@pytest.mark.parametrize("direction,ctype,offset", [
    ("c2s", 0x16, 0), ("c2s", 0x16, 10), ("c2s", 0x16, 600),            # ClientHello
    ("c2s", 0x17, 0), ("c2s", 0x17, 4000), ("c2s", 0x17, 10**6),        # client cert/CV/Finished
    ("s2c", 0x16, 0), ("s2c", 0x16, 10), ("s2c", 0x16, 90),             # ServerHello
    ("s2c", 0x17, 0), ("s2c", 0x17, 5000), ("s2c", 0x17, 10**6),        # server flight
])
def test_tampered_handshake_fails_on_both_sides(direction, ctype, offset):
    proxy = RecordProxy()
    proxy.hooks[direction] = _flip_first(ctype, offset)
    client, server = _run_pair(proxy=proxy, timeout=2.0)
    proxy.kill()
    _close(client, server)
    _assert_fail(client)
    _assert_fail(server)


# 7 ---------------------------------------------------------------------------------

def _armed(fn):
    """Hook that rewrites only the first record after it is installed."""
    hits = []

    def hook(rec):
        hits.append(rec)
        return fn(rec, len(hits))
    return hook


def _flip(rec, n):
    b = bytearray(rec)
    b[-3] ^= 0x80
    return bytes(b) if n == 1 else rec


ATTACKS = {
    "flip": (_flip, 1),
    "duplicate": (lambda rec, n: rec + rec if n == 1 else rec, 1),
    "inject_record": (lambda rec, n: b"\x17\x03\x03\x00\x20" + os.urandom(32) + rec
                      if n == 1 else rec, 1),
    "inject_garbage": (lambda rec, n: b"\xff" + os.urandom(63) + rec if n == 1 else rec, 1),
    "drop": (lambda rec, n: b"" if n == 1 else rec, 2),
    "reorder": (None, 2),
}


def _reorder():
    held = []

    def hook(rec):
        if not held:
            held.append(rec)
            return b""
        return rec + held.pop()
    return hook


@pytest.mark.parametrize("direction", ["c2s", "s2c"])
@pytest.mark.parametrize("attack", list(ATTACKS))
def test_tampered_control_records_never_reach_the_app(attack, direction):
    proxy = RecordProxy()
    client, server = _run_pair(proxy=proxy)
    try:
        assert isinstance(client, SecureChannel) and isinstance(server, SecureChannel)
        sender, receiver = (client, server) if direction == "c2s" else (server, client)
        fn, n_msgs = ATTACKS[attack]
        proxy.hooks[direction] = _reorder() if attack == "reorder" else _armed(fn)
        sent = [(1, b"legit-1"), (2, b"legit-2")][:n_msgs]
        for m in sent:
            sender.send(*m)
        got = []
        with pytest.raises((ConnectionError, ValueError)):
            for _ in range(len(sent) + 2):
                got.append(receiver.recv(2.0))
        assert all(g in sent for g in got), f"attacker data reached the app: {got}"
        if attack == "duplicate":
            assert got == [sent[0]]
        with pytest.raises((ConnectionError, ValueError)):
            receiver.recv(0.5)
    finally:
        proxy.kill()
        _close(client, server)


# 8, 9 ------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["client", "server"])
def test_silent_peer_times_out(role):
    ls = _listener()
    a = socket.create_connection(ls.getsockname())
    b, _ = ls.accept()
    mine, pin = (a, SERVER.cert_pem) if role == "client" else (b, CLIENT.cert_pem)
    ident = CLIENT if role == "client" else SERVER
    t0 = time.monotonic()
    res = _attempt(SecureChannel.establish, mine, role, ident, pin, 0.5)
    elapsed = time.monotonic() - t0
    for s in (a, b, ls):
        s.close()
    _assert_fail(res, "timeout")
    assert 0.4 <= elapsed < 1.5


def test_peer_closing_mid_handshake_is_peer_closed():
    ls = _listener()
    a = socket.create_connection(ls.getsockname())
    b, _ = ls.accept()
    a.close()
    res = _attempt(SecureChannel.establish, b, "server", SERVER, CLIENT.cert_pem, T)
    ls.close()
    _assert_fail(res, "peer_closed")


def test_v1_peer_is_detected():
    ls = _listener()
    a = socket.create_connection(ls.getsockname())
    b, _ = ls.accept()
    hello = b"\x01" + os.urandom(80)              # v1: uint32 length, then version 0x01
    a.sendall(struct.pack(">I", len(hello)) + hello)
    res = _attempt(SecureChannel.establish, b, "server", SERVER, CLIENT.cert_pem, T)
    a.close()
    ls.close()
    _assert_fail(res, "protocol_v1_peer")
    assert "v1" in str(res)


def test_establish_rejects_bad_arguments():
    ls = _listener()
    a = socket.create_connection(ls.getsockname())
    with pytest.raises(ValueError):
        SecureChannel.establish(a, "peer", CLIENT, SERVER.cert_pem, T)
    a.close()
    ls.close()


# 10 --------------------------------------------------------------------------------

def test_oversize_control_payload_is_rejected_on_send_and_receive(monkeypatch):
    client, server = secure.local_pair()
    try:
        with pytest.raises(ValueError):
            client.send(1, b"x" * (secure.MAX_CONTROL_PAYLOAD + 1))
        with pytest.raises(ValueError):
            client.send(256, b"")
        client.send(1, b"x" * secure.MAX_CONTROL_PAYLOAD)
        assert server.recv(T) == (1, b"x" * secure.MAX_CONTROL_PAYLOAD)
        # A peer that ignores the limit: the receiver refuses before reading the body.
        monkeypatch.setattr(secure, "MAX_CONTROL_PAYLOAD", 60_000)
        client.send(1, b"y" * 50_000)
        monkeypatch.undo()
        with pytest.raises(ValueError):
            server.recv(T)
    finally:
        _close(client, server)


# 11 --------------------------------------------------------------------------------

def test_concurrent_senders_and_reader_do_not_deadlock_or_interleave():
    client, server = secure.local_pair()
    n = 200
    to_server, to_client, errors = [], [], []

    def guard(fn):
        def run():
            try:
                fn()
            except Exception as e:
                errors.append(e)
        return threading.Thread(target=run, daemon=True)

    def sender(tid):
        for i in range(n):
            client.send(1, f"t{tid}-{i:04d}".encode() + bytes([tid]) * (i % 300))

    def reader(ch, sink, count):
        while len(sink) < count:
            m = ch.recv(5.0)
            assert m is not None, "reader stalled"
            sink.append(m)

    def server_sender():
        for i in range(n):
            server.send(2, f"s-{i:04d}".encode())

    threads = [guard(lambda t=t: sender(t)) for t in range(3)]
    threads += [guard(lambda: reader(server, to_server, 3 * n)),
                guard(lambda: reader(client, to_client, n)), guard(server_sender)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        assert not any(t.is_alive() for t in threads), "deadlock"
        assert not errors, errors
        assert to_client == [(2, f"s-{i:04d}".encode()) for i in range(n)]
        for tid in range(3):
            mine = [p for typ, p in to_server if p.startswith(f"t{tid}-".encode())]
            assert mine == [f"t{tid}-{i:04d}".encode() + bytes([tid]) * (i % 300)
                            for i in range(n)]
    finally:
        _close(client, server)


# 12 --------------------------------------------------------------------------------

def test_close_is_idempotent_and_peer_sees_connection_error():
    client, server = secure.local_pair()
    client.close()
    client.close()
    with pytest.raises(ConnectionError):
        server.recv(T)
    with pytest.raises(ConnectionError):
        client.send(1, b"after close")
    with pytest.raises(ConnectionError):
        client.recv(0.1)
    server.close()
    server.close()


def test_abrupt_disconnect_is_connection_error():
    proxy = RecordProxy()
    client, server = _run_pair(proxy=proxy)
    try:
        proxy.kill()
        with pytest.raises(ConnectionError):
            server.recv(T)
    finally:
        _close(client, server)


def test_close_unblocks_a_waiting_reader():
    client, server = secure.local_pair()
    caught = []

    def read():
        try:
            client.recv(None)
        except ConnectionError as e:
            caught.append(e)
    t = threading.Thread(target=read, daemon=True)
    t.start()
    time.sleep(0.1)
    client.close()
    t.join(3)
    server.close()
    assert not t.is_alive() and caught


def test_recv_timeout_returns_none():
    client, server = secure.local_pair()
    try:
        t0 = time.monotonic()
        assert client.recv(0.2) is None
        assert time.monotonic() - t0 < 1.0
    finally:
        _close(client, server)


# 14 --------------------------------------------------------------------------------

def test_no_secrets_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    client, server = _run_pair()
    bad = _run_pair(client_pin=MALLORY.cert_pem)
    try:
        assert isinstance(client, SecureChannel), client
        ck, sk = client.media_keys(), server.media_keys()
        forbidden = [b"PRIVATE KEY", ck.send_secret.hex().encode(), ck.recv_secret.hex().encode(),
                     client.sas().encode()]
        for ident in (CLIENT, SERVER):
            key = serialization.load_pem_private_key(ident.key_pem, None)
            forbidden.append(key.private_bytes_raw().hex().encode())
            forbidden += [line for line in ident.key_pem.splitlines()[1:-1] if len(line) > 20]
        texts = []
        for rec in caplog.records:
            texts.append(rec.getMessage())
            texts += [repr(v) for v in vars(rec).values()]
        blob = "\n".join(texts).encode()
        assert any(r.name == "hpqv.secure" for r in caplog.records), "nothing was logged"
        for f in forbidden:
            assert f not in blob
    finally:
        _close(client, server, *bad)
