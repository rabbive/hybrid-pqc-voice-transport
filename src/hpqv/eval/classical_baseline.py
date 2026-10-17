"""Classical-only TLS 1.3 handshake (x25519, Ed25519 certs).

BENCHMARK ONLY, NOT SECURE AGAINST QUANTUM ADVERSARIES. It exists to compare the
hybrid handshake against in scripts/run_handshake_bench.py. Production code never
imports it, and production `secure` refuses it (tests/eval/test_handshake_bench.py).

Every handshake runs in a child process whose OPENSSL_CONF limits the groups to x25519
and signatures to ed25519, so the caller's OpenSSL is never reconfigured. The child
mirrors secure.local_pair and SecureChannel.establish: fresh identities, loopback TCP,
server in a thread, TLS 1.3 only, no tickets or session cache, mutual auth against a
pinned key, memory BIOs, and a 3-byte READY record the client waits for. handshake_ms,
handshake_bytes and cpu_ms are measured over the same span as in the hybrid mode. Each
child discards one warm-up handshake first (OpenSSL's lazy algorithm fetch, imports).
"""
import contextlib
import datetime
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
from OpenSSL import SSL, crypto

MODE = "classical-BENCHMARK-ONLY"
GROUP = "x25519"
MAX_N = 1000
TIMEOUT = 10.0
_ALPN = b"hpqv/B"            # same length as secure.ALPN, so the hellos stay comparable
_READY = bytes([4, 0, 0])    # same 3-byte frame as secure's MSG_READY
_READ_CHUNK = 65536
_CONF = f"""openssl_conf = openssl_init
[openssl_init]
ssl_conf = ssl_sect
[ssl_sect]
system_default = system_default_sect
[system_default_sect]
MinProtocol = TLSv1.3
MaxProtocol = TLSv1.3
Groups = {GROUP}
SignatureAlgorithms = ed25519
"""


def run(n: int) -> list[dict]:
    """n classical handshakes in an isolated child process, one row each (after one
    discarded warm-up handshake)."""
    if not 1 <= n <= MAX_N:
        raise ValueError(f"n must be 1..{MAX_N}")
    with _conf() as env:
        out = subprocess.run([sys.executable, "-m", __name__, str(n)], env=env,
                             capture_output=True, text=True, check=True,
                             timeout=n * TIMEOUT + 30).stdout
    return json.loads(out)


@contextlib.contextmanager
def server():
    """A baseline server for one connection, in a child process.

    Yields ((host, port), result); result() waits for the child and returns
    "established" or "refused"."""
    with _conf() as env:
        p = subprocess.Popen([sys.executable, "-m", __name__, "--serve"], env=env,
                             stdout=subprocess.PIPE, text=True)
        try:
            port = int(p.stdout.readline(16))

            def result() -> str:
                return p.communicate(timeout=TIMEOUT + 5)[0][:32].strip()
            yield ("127.0.0.1", port), result
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()


@contextlib.contextmanager
def _conf():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "classical-benchmark-only.cnf")
        with open(path, "w") as f:
            f.write(_CONF)
        yield {**os.environ, "OPENSSL_CONF": path}


# -- child process only -------------------------------------------------------------

def _ident(name: str) -> dict:
    """Ed25519 self-signed cert, built like identity.new builds the ML-DSA-65 one."""
    key = ed25519.Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .sign(key, None))
    return {"key_pem": key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()),
            "cert_pem": cert.public_bytes(serialization.Encoding.PEM),
            "raw": key.public_key().public_bytes_raw()}


def _context(ident: dict, pin: bytes | None, role: str) -> SSL.Context:
    # Groups and signature algorithms come from OPENSSL_CONF, not from code.
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_options(SSL.OP_NO_TICKET)
    ctx.set_session_cache_mode(SSL.SESS_CACHE_OFF)
    ctx.use_certificate(x509.load_pem_x509_certificate(ident["cert_pem"]))
    ctx.use_privatekey(crypto.load_privatekey(crypto.FILETYPE_PEM, ident["key_pem"]))
    ctx.check_privatekey()

    def verify(conn, cert, errno, depth, ok):
        key = cert.to_cryptography().public_key()
        return (depth == 0 and isinstance(key, ed25519.Ed25519PublicKey)
                and key.public_bytes_raw() == pin)

    ctx.set_verify(SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, verify)
    if role == "client":
        ctx.set_alpn_protos([_ALPN])
    else:
        ctx.set_alpn_select_callback(lambda conn, offered: offered[0])
    return ctx


def _establish(sock: socket.socket, role: str, ident: dict, pin: bytes | None) -> dict:
    sock.settimeout(TIMEOUT)
    t0 = time.perf_counter()
    deadline = time.monotonic() + TIMEOUT
    conn = SSL.Connection(_context(ident, pin, role), None)
    conn.set_connect_state() if role == "client" else conn.set_accept_state()
    nbytes = 0

    def flush():
        nonlocal nbytes
        out = b""
        with contextlib.suppress(SSL.WantReadError):
            while True:
                out += conn.bio_read(_READ_CHUNK)
        if out:
            sock.sendall(out)
            nbytes += len(out)

    def read():
        nonlocal nbytes
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sock], [], [], remaining)[0]:
            raise TimeoutError("classical handshake timed out")
        data = sock.recv(_READ_CHUNK)
        if not data:
            raise ConnectionError("peer closed")
        nbytes += len(data)
        conn.bio_write(data)

    while True:
        try:
            conn.do_handshake()
            flush()
            break
        except SSL.WantReadError:
            flush()
        except SSL.Error:
            with contextlib.suppress(OSError, SSL.Error):
                flush()                                 # deliver our alert
            raise
        read()
    if role == "server":
        conn.sendall(_READY)
        flush()
    else:
        got = b""
        while len(got) < len(_READY):
            try:
                got += conn.recv(len(_READY) - len(got))
            except SSL.WantReadError:
                read()
        if got != _READY:
            raise ConnectionError("first message was not READY")
    group = conn.get_group_name()
    if group != GROUP:              # rows labelled classical must really be classical
        raise RuntimeError(f"negotiated {group}; OPENSSL_CONF was not applied")
    return {"handshake_ms": (time.perf_counter() - t0) * 1000, "handshake_bytes": nbytes,
            "group": group}


def _pair_once() -> dict:
    caller, listener = _ident("caller"), _ident("listener")
    cpu0 = time.process_time()      # same span as the hybrid row: local_pair() minus keygen
    with socket.create_server(("127.0.0.1", 0)) as ls:
        csock = socket.create_connection(ls.getsockname(), timeout=TIMEOUT)
        ssock, _ = ls.accept()
    srv: dict = {}

    def serve():
        try:
            srv["ok"] = _establish(ssock, "server", listener, caller["raw"])
        except Exception as e:
            srv["err"] = e

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        row = _establish(csock, "client", caller, listener["raw"])
        t.join(TIMEOUT + 1)
        cpu_ms = (time.process_time() - cpu0) * 1000
    finally:
        csock.close()
        ssock.close()
    if "ok" not in srv:
        raise srv.get("err", TimeoutError("classical server did not finish"))
    return {"mode": MODE, **row, "cpu_ms": cpu_ms}


def _serve_once() -> str:
    with socket.create_server(("127.0.0.1", 0)) as ls:
        ls.settimeout(TIMEOUT)
        print(ls.getsockname()[1], flush=True)
        sock, _ = ls.accept()
    with sock:
        try:
            _establish(sock, "server", _ident("listener"), None)
        except (SSL.Error, OSError):
            return "refused"
    return "established"


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        print(_serve_once())
    else:
        _pair_once()                # warm-up, discarded
        print(json.dumps([_pair_once() for _ in range(int(sys.argv[1]))]))
