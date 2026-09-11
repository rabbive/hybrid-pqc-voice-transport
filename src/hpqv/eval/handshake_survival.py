"""Does the handshake actually get through?

Counting fragments proves a structural property. This measures its consequence:
how often a PQC handshake COMPLETES under packet loss.

- naive: the whole 10.8 KB handshake goes out as ONE oversized UDP datagram. The
  IP layer splits it into ~8 fragments; losing ANY one destroys the datagram, so
  survival decays as (1-p)^n.
- hybrid: the same bytes go over TCP, app-framed under the MTU. Nothing
  fragments and TCP retransmits, so it completes — paying time, not failure.
"""
import os
import socket
import struct
import time

from hpqv.eval import pqc_cost

# Payload per IPv4 fragment on a 1500-byte MTU link (1500 - 20 byte IP header),
# rounded down to the 8-byte boundary fragmentation uses.
_FRAG_PAYLOAD = 1480


def expected_fragments(blob_size: int) -> int:
    """How many IP fragments an oversized UDP datagram of this size becomes."""
    return -(-(blob_size + 8) // _FRAG_PAYLOAD)   # +8 for the UDP header


def _blob(size: int) -> bytes:
    return os.urandom(size)


def naive_attempt(blob: bytes, timeout: float = 2.0) -> tuple:
    """One fire-and-forget oversized UDP handshake. Returns (success, elapsed_ms)."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(timeout)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        start = time.perf_counter()
        tx.sendto(blob, rx.getsockname())
        try:
            data, _ = rx.recvfrom(len(blob) + 64)
        except (socket.timeout, OSError):
            return False, 0.0
        elapsed = (time.perf_counter() - start) * 1000.0
        return len(data) == len(blob), elapsed
    finally:
        tx.close()
        rx.close()


def hybrid_attempt(blob: bytes, timeout: float = 20.0) -> tuple:
    """One handshake over TCP, length-prefixed like the real control channel.

    Runs the server in a thread so send and receive overlap as they do in a
    real call. Returns (success, elapsed_ms).
    """
    import threading

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(timeout)
    box = {}

    def server():
        try:
            conn, _ = srv.accept()
            conn.settimeout(timeout)
            hdr = conn.recv(4)
            if len(hdr) < 4:
                box["ok"] = False
                return
            (n,) = struct.unpack(">I", hdr)
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(min(65536, n - len(buf)))
                if not chunk:
                    break
                buf += chunk
            box["ok"] = len(buf) == n
            conn.close()
        except Exception:
            box["ok"] = False

    t = threading.Thread(target=server)
    t.start()
    start = time.perf_counter()
    try:
        cli = socket.create_connection(srv.getsockname(), timeout=timeout)
        cli.settimeout(timeout)
        cli.sendall(struct.pack(">I", len(blob)) + blob)
        t.join(timeout)
        cli.close()
    except Exception:
        box["ok"] = False
    finally:
        srv.close()
    elapsed = (time.perf_counter() - start) * 1000.0
    return bool(box.get("ok")), elapsed


def measure(transport: str, attempts: int = 20, blob_size: int = None,
            timeout: float = None) -> dict:
    """Run `attempts` handshakes and report how many completed.

    Caller is responsible for applying network impairment (see eval.netem) and
    pinning the loopback MTU to 1500 around this call.
    """
    if blob_size is None:
        blob_size = pqc_cost.handshake_bytes("hybrid")
    attempt = {"naive": naive_attempt, "hybrid": hybrid_attempt}[transport]
    # On loopback a datagram that has not arrived within a few ms is gone; a
    # short timeout keeps a large sample affordable.
    kwargs = {} if timeout is None else {"timeout": timeout}

    successes, times = 0, []
    for _ in range(attempts):
        ok, ms = attempt(_blob(blob_size), **kwargs)
        if ok:
            successes += 1
            times.append(ms)
    times.sort()
    median_ms = times[len(times) // 2] if times else float("nan")
    return {
        "name": transport,
        "attempts": attempts,
        "successes": successes,
        "success_pct": 100.0 * successes / attempts,
        "median_ms": median_ms,
        "blob_bytes": blob_size,
        "fragments": expected_fragments(blob_size) if transport == "naive" else 0,
    }
