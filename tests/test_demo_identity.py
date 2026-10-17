"""v2 identities and the CLI around them: keygen, pinning, the v1 refusal, CALL_START."""
import json
import os
import socket
import struct
import threading
import time

import pytest

from hpqv import control, demo, identity, secure

from test_identity import assert_private_key_file
from v2pair import channels


def _keygen(tmp_path, name):
    key, cert = str(tmp_path / f"{name}.key"), str(tmp_path / f"{name}.crt")
    demo.main(["keygen", "--name", name, "--key-out", key, "--cert-out", cert])
    return key, cert


# ---------------- keygen ----------------

def test_keygen_writes_a_private_key_0600_and_prints_the_fingerprint(tmp_path, capsys):
    key, cert = _keygen(tmp_path, "alice")
    assert_private_key_file(key)
    ident = identity.load(key, cert)
    out = capsys.readouterr().out
    assert identity.fingerprint(ident.cert_pem) in out
    assert b"PRIVATE KEY" not in open(cert, "rb").read()      # the cert is the public half
    assert "PRIVATE KEY" not in out                           # never echoed


def test_keygen_refuses_to_overwrite_either_file(tmp_path):
    key, cert = _keygen(tmp_path, "alice")
    before = (open(key, "rb").read(), open(cert, "rb").read())
    with pytest.raises(SystemExit) as e:
        demo.main(["keygen", "--name", "alice", "--key-out", key, "--cert-out", cert])
    assert "refusing to overwrite" in str(e.value)
    # only the certificate path is taken: still refused, key untouched
    with pytest.raises(SystemExit):
        demo.main(["keygen", "--name", "x", "--key-out", str(tmp_path / "new.key"),
                   "--cert-out", cert])
    assert not os.path.exists(tmp_path / "new.key")
    assert (open(key, "rb").read(), open(cert, "rb").read()) == before


def test_keygen_has_no_shared_identity_file_mode(tmp_path):
    for flag in ("--out", "--identity-file"):
        with pytest.raises(SystemExit):
            demo.main(["keygen", flag, str(tmp_path / "identity.json")])


def test_create_identity_returns_the_fingerprint_and_preserves_existing_files(tmp_path):
    key, cert = str(tmp_path / "me.key"), str(tmp_path / "me.crt")
    fp = demo.create_identity("me", key, cert)
    assert fp == identity.fingerprint(identity.load(key, cert).cert_pem)
    assert len(fp.split()) == 16
    with pytest.raises(FileExistsError):
        demo.create_identity("me", key, cert)


def test_two_created_identities_authenticate_each_other_over_tls(tmp_path):
    ka, ca = _keygen(tmp_path, "a")
    kb, cb = _keygen(tmp_path, "b")
    a, a_peer = demo._load_identity(ka, ca, cb)
    b, b_peer = demo._load_identity(kb, cb, ca)
    with socket.create_server(("127.0.0.1", 0)) as ls:
        csock = socket.create_connection(ls.getsockname())
        ssock, _ = ls.accept()
    box = {}
    t = threading.Thread(target=lambda: box.update(
        server=demo.establish_timed("server", ssock, b, b_peer, demo.Stats())))
    t.start()
    stats = demo.Stats()
    client = demo.establish_timed("client", csock, a, a_peer, stats)
    t.join(5)
    try:
        assert client.sas() == box["server"].sas()
        assert stats.sas == client.sas() and stats.group == secure.GROUP
        assert stats.peer_fingerprint == identity.fingerprint(b.cert_pem)
        assert stats.handshake_ms > 0 and stats.handshake_bytes > 0
        assert stats.cipher
    finally:
        client.close()
        box["server"].close()


# ---------------- CLI refusals ----------------

def _legacy_json(tmp_path):
    p = tmp_path / "identity.json"
    p.write_text(json.dumps({"listener": {"sig_pub": "00", "sig_secret": "11"},
                             "caller": {"sig_pub": "22", "sig_secret": "33"}}))
    return str(p)


@pytest.mark.parametrize("verb", ["listen", "call"])
def test_v1_identity_json_as_peer_cert_exits_with_the_legacy_message(tmp_path, verb):
    key, cert = _keygen(tmp_path, "me")
    legacy = _legacy_json(tmp_path)
    argv = [verb, "--key", key, "--cert", cert, "--peer-cert", legacy,
            "--input", "file", "--wav", "x.wav", "--output", "file", "--out-wav", "y.wav"]
    argv += ["--port", "0"] if verb == "listen" else ["127.0.0.1:1"]
    with pytest.raises(SystemExit) as e:
        demo.main(argv)
    assert "v1 identity.json" in str(e.value)
    assert "peer's certificate" in str(e.value)


def test_a_private_key_as_peer_cert_is_refused(tmp_path):
    key, cert = _keygen(tmp_path, "me")
    with pytest.raises(SystemExit) as e:
        demo.main(["listen", "--port", "0", "--key", key, "--cert", cert, "--peer-cert", key,
                   "--input", "file", "--wav", "x", "--output", "file", "--out-wav", "y"])
    assert "private key" in str(e.value)


@pytest.mark.parametrize("verb", ["listen", "call"])
def test_identity_files_go_together_or_friend_mode_is_used(verb, tmp_path, monkeypatch):
    """B2: no identity files = this device's identity + paired friends; some = error."""
    monkeypatch.setenv("HPQV_HOME", str(tmp_path))
    argv = [verb, "--port", "0", "--no-discovery"] if verb == "listen" else [verb, "127.0.0.1:1"]
    with pytest.raises(SystemExit) as e:
        demo.main(argv)                            # friend mode, but nobody paired yet
    assert "no paired friends yet" in str(e.value.code)
    with pytest.raises(SystemExit) as e:
        demo.main(argv + ["--key", "me.key"])
    assert "go together" in str(e.value.code)
    with pytest.raises(SystemExit) as e:
        demo.main(argv + ["--identity-file", "identity.json"])
    assert e.value.code == 2                       # the v1 flag no longer exists


# ---------------- the listener waits for CALL_START ----------------

def test_wait_call_start_returns_the_peer_ip_and_announced_port():
    client, server = channels()
    try:
        client.send(control.MSG_CALL_START, struct.pack(">H", 40123))
        assert demo.wait_call_start(server, "10.1.2.3") == ("10.1.2.3", 40123)
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("first", [
    (control.MSG_KEY_ROTATE, struct.pack(">I", 1)),
    (control.MSG_CALL_END, b""),
    (control.MSG_READY, b""),
    (99, b"x"),
    (control.MSG_CALL_START, b""),                       # missing port
    (control.MSG_CALL_START, struct.pack(">I", 7)),      # wrong length
    (control.MSG_CALL_START, struct.pack(">H", 0)),      # port 0
])
def test_listener_rejects_a_first_message_that_is_not_a_valid_call_start(first):
    client, server = channels()
    try:
        client.send(*first)
        with pytest.raises(demo.CallSetupError):
            demo.wait_call_start(server, "127.0.0.1", timeout=2)
    finally:
        client.close()
        server.close()


def test_listener_gives_up_when_the_caller_stays_silent():
    client, server = channels()
    try:
        t0 = time.monotonic()
        with pytest.raises(demo.CallSetupError, match="in time"):
            demo.wait_call_start(server, "127.0.0.1", timeout=0.5)
        assert time.monotonic() - t0 < 2
    finally:
        client.close()
        server.close()


def test_hang_up_ends_the_wait_for_call_start():
    client, server = channels()
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    try:
        t0 = time.monotonic()
        with pytest.raises(demo.CallSetupError, match="hung up"):
            demo.wait_call_start(server, "127.0.0.1", timeout=10, stop_event=stop)
        assert time.monotonic() - t0 < 2
    finally:
        client.close()
        server.close()


def test_closed_connection_while_waiting_for_call_start_is_a_connection_error():
    client, server = channels()
    client.close()
    try:
        with pytest.raises(ConnectionError):
            demo.wait_call_start(server, "127.0.0.1", timeout=2)
    finally:
        server.close()


# ---------------- plain-words failures, static info ----------------

def test_every_handshake_failure_reason_has_plain_words():
    for reason in secure.HandshakeError.REASONS:
        text = demo.handshake_failure_text(reason)
        assert text and reason not in text and text != "handshake failed"
    assert "v1" in demo.handshake_failure_text("protocol_v1_peer")


def test_handshake_info_is_static_and_names_the_v2_suite():
    assert demo.handshake_info() == {
        "protocol": "TLSv1.3", "group": "X25519MLKEM768", "sig": "ML-DSA-65",
        "transport": "TCP", "udp_fragments": 0, "datagram_cap": 1200}


def test_demo_does_not_import_liboqs_or_the_v1_handshake():
    import subprocess
    import sys
    code = "import sys, hpqv.demo; sys.exit('oqs' in sys.modules or 'hpqv.handshake' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": "src"},
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
