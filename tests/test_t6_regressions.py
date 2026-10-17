"""Regression tests for the T6 security review (docs/reviews/2026-10-16-t6-security-review.md).

One section per Phase B1 fix. Wire-level checks parse the real TLS records that
cross a loopback proxy; Windows-only behaviour is exercised with fakes because no
Windows host is available here (see docs/reviews/2026-10-16-b1-verification.md).
"""
import errno
import os
import socket
import stat
import sys
import threading
import time

import pytest

from hpqv import call, demo, identity, packet, secure, udp
from hpqv.controls import CallControls
from hpqv.demo import Stats
from hpqv.keyring import Keyring
from hpqv.secure import HandshakeError, SecureChannel
from test_secure import CLIENT, SERVER, RecordProxy, _close, _run_pair

X25519MLKEM768, MLDSA65 = 0x11EC, 0x0905


# -- 1. only the hybrid PQ group and ML-DSA-65 are offered --------------------------------

def _extensions(body, pos):
    end = pos + 2 + int.from_bytes(body[pos:pos + 2], "big")
    pos += 2
    out = {}
    while pos < end:
        etype, elen = int.from_bytes(body[pos:pos + 2], "big"), int.from_bytes(body[pos + 2:pos + 4], "big")
        out[etype] = body[pos + 4:pos + 4 + elen]
        pos += 4 + elen
    return out


def _u16s(data):
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]


def parse_client_hello(record):
    assert record[0] == 0x16 and record[5] == 0x01, "not a ClientHello record"
    body = record[9:]
    pos = 2 + 32
    pos += 1 + body[pos]                                        # session id
    pos += 2 + int.from_bytes(body[pos:pos + 2], "big")         # cipher suites
    pos += 1 + body[pos]                                        # compression
    ext = _extensions(body, pos)
    shares, ks = [], ext[51][2:]
    while ks:
        group, n = int.from_bytes(ks[:2], "big"), int.from_bytes(ks[2:4], "big")
        shares.append((group, n))
        ks = ks[4 + n:]
    return {"groups": _u16s(ext[10][2:]), "key_shares": shares,
            "sigalgs": _u16s(ext[13][2:]),
            "sigalgs_cert": _u16s(ext[50][2:]) if 50 in ext else None}


def parse_server_hello(record):
    assert record[0] == 0x16 and record[5] == 0x02, "not a ServerHello record"
    body = record[9:]
    pos = 2 + 32
    pos += 1 + body[pos]
    pos += 2 + 1                                                # cipher suite, compression
    ks = _extensions(body, pos)[51]
    return {"group": int.from_bytes(ks[:2], "big"), "share_len": int.from_bytes(ks[2:4], "big")}


def _captured_handshake():
    proxy, seen = RecordProxy(), {"c2s": [], "s2c": []}
    for d in seen:
        proxy.hooks[d] = lambda rec, d=d: (seen[d].append(rec), rec)[1]
    client, server = _run_pair(proxy=proxy)
    proxy.kill()
    return client, server, seen


def test_t6_clienthello_offers_only_x25519mlkem768_and_mldsa65():
    client, server, seen = _captured_handshake()
    try:
        hello = parse_client_hello(seen["c2s"][0])
        assert hello["groups"] == [X25519MLKEM768]
        assert hello["key_shares"] == [(X25519MLKEM768, 1216)]     # ML-KEM ek 1184 || X25519 32
        assert hello["sigalgs"] == [MLDSA65]
        assert hello["sigalgs_cert"] in (None, [MLDSA65])
        reply = parse_server_hello(seen["s2c"][0])
        assert reply == {"group": X25519MLKEM768, "share_len": 1120}  # ML-KEM ct 1088 || X25519 32
        assert client.group == server.group == secure.GROUP           # runtime check still applies
    finally:
        _close(client, server)


def test_t6_group_restriction_failure_is_fatal(monkeypatch):
    """If OpenSSL refuses the group list we must not fall back to its defaults."""
    monkeypatch.setattr(secure._lib, "SSL_CTX_set1_curves_list", lambda ctx, groups: 0)
    with pytest.raises(RuntimeError, match="X25519MLKEM768"):
        secure._context(SERVER, CLIENT.cert_pem, "server", [])


# -- 2. identical local and peer identities are refused -----------------------------------

def test_t6_load_identity_refuses_own_certificate_as_peer(tmp_path):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    with pytest.raises(ValueError, match="your own certificate"):
        demo._load_identity(str(key), str(crt), str(crt))


def test_t6_establish_refuses_own_identity_and_closes_socket():
    a, b = socket.socketpair()
    try:
        with pytest.raises(HandshakeError) as e:
            SecureChannel.establish(a, "client", CLIENT, CLIENT.cert_pem, timeout=1)
        assert e.value.reason == "bad_peer_cert"
        assert a.fileno() == -1                    # socket closed, not left half-open
    finally:
        b.close()


def test_t6_same_identity_copied_to_both_machines_never_connects():
    client, server = _run_pair(client_pin=CLIENT.cert_pem, server_pin=CLIENT.cert_pem,
                               client_ident=CLIENT, server_ident=CLIENT)
    assert isinstance(client, HandshakeError) and isinstance(server, HandshakeError)


# -- 3. private-key file permissions ------------------------------------------------------

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


@posix_only
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o666, 0o660])
def test_t6_load_refuses_group_or_world_accessible_key(tmp_path, mode):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    os.chmod(key, mode)
    with pytest.raises(PermissionError, match="chmod 600"):
        identity.load(str(key), str(crt))


@posix_only
def test_t6_load_accepts_owner_only_key(tmp_path):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    os.chmod(key, 0o400)
    assert identity.load(str(key), str(crt)).cert_pem == CLIENT.cert_pem


@posix_only
def test_t6_load_refuses_key_owned_by_someone_else(tmp_path, monkeypatch):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(key).st_uid + 1)
    with pytest.raises(PermissionError, match="owned by"):
        identity.load(str(key), str(crt))


SDDL_PRIVATE = "D:PAI(A;;FA;;;S-1-5-21-1-2-3-1001)(A;;FA;;;SY)(A;;FA;;;BA)"
SDDL_EVERYONE = "D:AI(A;;FA;;;S-1-5-21-1-2-3-1001)(A;ID;0x1200a9;;;WD)"
SDDL_USERS = "D:AI(A;ID;FA;;;SY)(A;ID;0x1200a9;;;BU)"
SDDL_AUTH = "D:AI(A;ID;GR;;;AU)"
SDDL_DENY_ONLY = "D:P(D;;FA;;;WD)(A;;FA;;;S-1-5-21-1-2-3-1001)"


@pytest.mark.parametrize("sddl, broad", [
    (SDDL_PRIVATE, []), (SDDL_EVERYONE, ["WD"]), (SDDL_USERS, ["BU"]),
    (SDDL_AUTH, ["AU"]), (SDDL_DENY_ONLY, []),
    ("D:(A;;FA;;;S-1-1-0)", ["S-1-1-0"]), ("D:(A;;FA;;;S-1-5-32-545)", ["S-1-5-32-545"]),
])
def test_t6_windows_acl_parser_finds_broad_allow_aces(sddl, broad):
    assert identity._broad_allow_aces(sddl) == broad


def test_t6_windows_save_restricts_acl_to_current_user(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            returncode, stdout = 0, '"desktop\\me","S-1-5-21-1-2-3-1001"\r\n'
        return R()

    monkeypatch.setattr(identity, "_IS_WINDOWS", True)
    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    icacls = [c for c in calls if c[0] == "icacls"]
    assert icacls == [["icacls", str(key), "/inheritance:r", "/grant:r", "*S-1-5-21-1-2-3-1001:F"]]


def test_t6_windows_save_fails_closed_if_acl_cannot_be_set(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0 if cmd[0] == "whoami" else 5
            stdout = '"desktop\\me","S-1-5-21-1-2-3-1001"\r\n'
        return R()

    monkeypatch.setattr(identity, "_IS_WINDOWS", True)
    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    with pytest.raises(PermissionError):
        identity.save(CLIENT, str(key), str(crt))
    assert not key.exists() and not crt.exists()      # no half-protected key left behind


@pytest.mark.parametrize("sddl, ok", [(SDDL_PRIVATE, True), (SDDL_EVERYONE, False), (SDDL_USERS, False)])
def test_t6_windows_load_rejects_broadly_readable_key(tmp_path, monkeypatch, sddl, ok):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    monkeypatch.setattr(identity, "_IS_WINDOWS", True)
    monkeypatch.setattr(identity, "_windows_sddl", lambda path: sddl)
    if ok:
        assert identity.load(str(key), str(crt)).cert_pem == CLIENT.cert_pem
    else:
        with pytest.raises(PermissionError, match="readable by"):
            identity.load(str(key), str(crt))


def test_t6_windows_load_refuses_when_acl_unreadable(tmp_path, monkeypatch):
    key, crt = tmp_path / "me.key", tmp_path / "me.crt"
    identity.save(CLIENT, str(key), str(crt))
    monkeypatch.setattr(identity, "_IS_WINDOWS", True)

    def broken(path):
        raise OSError("icacls not available")
    monkeypatch.setattr(identity, "_windows_sddl", broken)
    with pytest.raises(PermissionError, match="could not check"):
        identity.load(str(key), str(crt))


# -- 4. voice keys are handed out exactly once, thread-safely -----------------------------

def test_t6_media_keys_single_use_under_concurrency():
    client, server = secure.local_pair()
    try:
        got, errors, barrier = [], [], threading.Barrier(8)

        def take():
            barrier.wait()
            try:
                got.append(client.media_keys())
            except RuntimeError as e:
                errors.append(e)
        threads = [threading.Thread(target=take) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert len(got) == 1 and len(errors) == 7
        assert all("already taken" in str(e) for e in errors)
        assert server.media_keys().recv_secret == got[0].send_secret
        with pytest.raises(RuntimeError):
            server.media_keys()
    finally:
        _close(client, server)


# -- 5. malformed UDP traffic never ends the call (Windows WSAEMSGSIZE / WSAECONNRESET) ---

class WindowsLikeUDP:
    """recvfrom() as Windows does it: a datagram longer than the buffer raises
    WSAEMSGSIZE (10040) instead of being truncated, and ICMP port-unreachable
    surfaces as WSAECONNRESET (10054)."""

    def __init__(self, items):
        self.items = list(items)      # bytes, or an exception instance to raise

    def settimeout(self, t):
        pass

    def recvfrom(self, n):
        if not self.items:
            time.sleep(0.02)
            raise socket.timeout()
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if len(item) > n:
            raise OSError(10040, "WSAEMSGSIZE: message longer than buffer")
        return item, ("127.0.0.1", 9)


def _ring_pair():
    client, server = secure.local_pair()
    rings = Keyring(client.media_keys()), Keyring(server.media_keys())
    _close(client, server)
    return rings


class _Sink:
    def __init__(self):
        self.frames = []

    def write(self, pcm):
        self.frames.append(pcm)


def _run_receiver(items):
    tx, rx = _ring_pair()
    from hpqv.audio import OpusCodec
    codec = OpusCodec()
    real = [tx.seal(i, codec.encode(bytes(1920))) for i in range(5)]
    stats, stop = Stats(), threading.Event()
    sock = WindowsLikeUDP([real[0], *items, *real[1:]])
    t = threading.Thread(target=demo._receiver_loop,
                         args=(sock, rx, _Sink(), CallControls(), stats, stop), daemon=True)
    t.start()
    deadline = time.monotonic() + 3
    while stats.received < 5 and time.monotonic() < deadline and t.is_alive():
        time.sleep(0.01)
    alive, stopped = t.is_alive(), stop.is_set()
    stop.set()
    t.join(3)
    return stats, alive, stopped, rx


@pytest.mark.parametrize("junk", [bytes(1300), bytes(1202), b"\x21" + bytes(9000), bytes(65507)])
def test_t6_oversize_datagram_never_ends_call(junk):
    stats, alive, stopped, rx = _run_receiver([junk])
    assert alive and not stopped, "one unauthenticated datagram ended the call"
    assert stats.received == 5                     # every real packet still arrived
    assert rx.rejects["oversize"] == 1


def test_t6_icmp_connreset_never_ends_call():
    stats, alive, stopped, _ = _run_receiver(
        [ConnectionResetError(10054, "WSAECONNRESET"), ConnectionResetError(10054, "again")])
    assert alive and not stopped and stats.received == 5


def test_t6_wsaemsgsize_error_never_ends_call():
    """Belt and braces: even if the OS reports WSAEMSGSIZE, one datagram is lost, not the call."""
    err = OSError(10040, "WSAEMSGSIZE")
    stats, alive, stopped, _ = _run_receiver([err])
    assert alive and not stopped and stats.received == 5


def test_t6_closed_socket_still_ends_the_call():
    """A socket that is really gone (EBADF) must end the loop, not spin."""
    stats, stop = Stats(), threading.Event()
    sock = WindowsLikeUDP([OSError(errno.EBADF, "Bad file descriptor")])
    tx, rx = _ring_pair()
    t = threading.Thread(target=demo._receiver_loop,
                         args=(sock, rx, _Sink(), CallControls(), stats, stop), daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive() and stop.is_set()


def test_t6_recv_stream_survives_oversize_and_connreset():
    tx, rx = _ring_pair()
    from hpqv.audio import OpusCodec
    codec = OpusCodec()
    real = [tx.seal(i, codec.encode(bytes(1920))) for i in range(4)]
    sock = WindowsLikeUDP([real[0], bytes(1300), ConnectionResetError(10054, "x"), *real[1:]])
    out = call.recv_stream(sock, rx, expected=4)
    assert len(out) == 4 and rx.rejects["oversize"] == 1


def test_t6_open_udp_disables_windows_connreset(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "SIO_UDP_CONNRESET", 0x9800000C, raising=False)
    monkeypatch.setattr(socket.socket, "ioctl", lambda self, code, val: calls.append((code, val)),
                        raising=False)
    s = udp.open_udp(("127.0.0.1", 0))
    s.close()
    assert calls == [(0x9800000C, False)]


def test_t6_receive_buffer_holds_any_udp_payload():
    assert demo.UDP_RECV_BUFFER >= 65507 and call.UDP_RECV_BUFFER >= 65507
