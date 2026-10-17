"""Interop: our TLS channel (pyOpenSSL on cryptography's OpenSSL) against Go's crypto/tls.

Go's TLS stack shares no code with OpenSSL, so a completed handshake with equal
exporter output on both sides is evidence that both implement the same TLS 1.3 key
schedule, X25519MLKEM768 combination (RFC 10024) and ML-DSA-65 CertificateVerify
(draft-ietf-tls-mldsa) -- not proof of full conformance. Skipped when `go` is absent.
"""
import json
import os
import shutil
import socket
import subprocess
import threading

import pytest

from hpqv import identity, secure
from hpqv.secure import HandshakeError, SecureChannel

GO = shutil.which("go")
pytestmark = pytest.mark.skipif(GO is None, reason="Go toolchain not installed")
SRC = os.path.join(os.path.dirname(__file__), "interop", "go_peer")


@pytest.fixture(scope="module")
def go_peer(tmp_path_factory):
    out = tmp_path_factory.mktemp("gobuild")
    env = dict(os.environ, GOCACHE=str(out / "cache"), GOPATH=str(out / "gopath"),
               GOFLAGS="-mod=mod", GO111MODULE="off")
    binary = out / "go_peer"
    subprocess.run([GO, "build", "-o", str(binary), "main.go"], cwd=SRC, env=env,
                   check=True, capture_output=True, timeout=300)
    return str(binary)


def _files(tmp_path, name, ident):
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    identity.save(ident, str(key), str(crt))
    return str(key), str(crt)


def _run_go(binary, *args):
    return subprocess.Popen([binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def _check(go, ours, role):
    assert go["version"] == "0x0304"                       # TLS 1.3
    assert go["curve_id"] == "0x11ec"                      # X25519MLKEM768, RFC 10024
    assert go["alpn"] == "hpqv/2"
    # Go names the key family "ML-DSA"; the parameter set shows in the cert's signature
    # algorithm, and our side only ever loads ML-DSA-65 (identity._mldsa_cert).
    assert go["peer_key_alg"] == "ML-DSA" and go["peer_sig_alg"] == "ML-DSA-65"
    assert go["did_resume"] is False
    assert ours.group == secure.GROUP and ours.protocol == "TLSv1.3"
    keys = ours.media_keys()
    c2s, s2c = (keys.send_secret, keys.recv_secret) if role == "client" else \
               (keys.recv_secret, keys.send_secret)
    assert go["exp_c2s"] == c2s.hex() and go["exp_s2c"] == s2c.hex()
    assert go["exp_session_id"] == keys.session_id.hex()
    assert go["exp_sas"] == ours.sas()


def test_ours_as_client_vs_go_server(go_peer, tmp_path):
    me, peer = identity.new("ours"), identity.new("go")
    gk, gc = _files(tmp_path, "go", peer)
    _, mc = _files(tmp_path, "ours", me)
    proc = _run_go(go_peer, "-role", "server", "-cert", gc, "-key", gk, "-peer", mc)
    host, port = proc.stdout.readline().strip().rsplit(":", 1)
    ch = SecureChannel.establish(socket.create_connection((host, int(port))), "client",
                                 me, peer.cert_pem, timeout=10)
    go = json.loads(proc.stdout.readline())
    proc.wait(10)
    try:
        _check(go, ch, "client")
    finally:
        ch.close()


def test_go_client_vs_ours_as_server(go_peer, tmp_path):
    me, peer = identity.new("ours"), identity.new("go")
    gk, gc = _files(tmp_path, "go", peer)
    _, mc = _files(tmp_path, "ours", me)
    ls = socket.create_server(("127.0.0.1", 0))
    out = {}

    def serve():
        s, _ = ls.accept()
        out["ch"] = SecureChannel.establish(s, "server", me, peer.cert_pem, timeout=10)
    t = threading.Thread(target=serve)
    t.start()
    host, port = ls.getsockname()
    proc = _run_go(go_peer, "-role", "client", "-addr", f"{host}:{port}", "-cert", gc,
                   "-key", gk, "-peer", mc)
    go = json.loads(proc.stdout.readline())
    proc.wait(10)
    t.join(10)
    ls.close()
    try:
        _check(go, out["ch"], "server")
    finally:
        out["ch"].close()


def test_go_classical_only_client_is_refused(go_peer, tmp_path):
    me, peer = identity.new("ours"), identity.new("go")
    gk, gc = _files(tmp_path, "go", peer)
    _, mc = _files(tmp_path, "ours", me)
    ls = socket.create_server(("127.0.0.1", 0))
    out = {}

    def serve():
        s, _ = ls.accept()
        try:
            out["ch"] = SecureChannel.establish(s, "server", me, peer.cert_pem, timeout=10)
        except HandshakeError as e:
            out["err"] = e
    t = threading.Thread(target=serve)
    t.start()
    host, port = ls.getsockname()
    proc = _run_go(go_peer, "-role", "client", "-addr", f"{host}:{port}", "-cert", gc,
                   "-key", gk, "-peer", mc, "-groups", "classical")
    go = json.loads(proc.stdout.readline())
    proc.wait(10)
    t.join(10)
    ls.close()
    assert "error" in go and "ch" not in out
    assert out["err"].reason == "tls_error"


def test_go_server_with_unpinned_key_is_refused(go_peer, tmp_path):
    me, peer, mallory = identity.new("ours"), identity.new("go"), identity.new("mallory")
    mk, mcrt = _files(tmp_path, "mallory", mallory)
    _, mc = _files(tmp_path, "ours", me)
    proc = _run_go(go_peer, "-role", "server", "-cert", mcrt, "-key", mk, "-peer", mc)
    host, port = proc.stdout.readline().strip().rsplit(":", 1)
    with pytest.raises(HandshakeError) as e:
        SecureChannel.establish(socket.create_connection((host, int(port))), "client",
                                me, peer.cert_pem, timeout=10)
    proc.kill()
    assert e.value.reason == "bad_peer_cert"
