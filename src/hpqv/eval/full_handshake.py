"""Measure the authenticated v1 PQC handshake over TCP and UDP.

HISTORICAL: this measures the v1 protocol (hpqv.eval.v1_handshake), not v2.
"""
import math
import select
import socket
import threading
import time

from hpqv.eval import v1_handshake as v1
from hpqv.eval.handshake_survival import expected_fragments


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("handshake deadline expired")
    return left


class _DeadlineSocket:
    """Refresh the timeout before each control-channel socket operation."""

    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        return self.sock.sendall(data)

    def recv(self, size):
        self.sock.settimeout(_remaining(self.deadline))
        return self.sock.recv(size)


def _matches(initiator, responder):
    return initiator[0] == responder[0] and initiator[1] == responder[2]


def run_tcp(ids, deadline_s) -> tuple[bool, float]:
    start = time.monotonic()
    deadline = start + deadline_s
    listener = socket.socket()
    accepted = []
    server = {}
    thread = None
    initiator = None

    def respond():
        try:
            listener.settimeout(_remaining(deadline))
            conn, _ = listener.accept()
            accepted.append(conn)
            server["session"] = v1.run_responder(
                _DeadlineSocket(conn, deadline), *ids[1], ids[0][0])
        except Exception:
            pass

    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        thread = threading.Thread(target=respond)
        thread.start()
        remaining = _remaining(deadline)
        initiator = socket.create_connection(listener.getsockname(), timeout=remaining)
        result = v1.run_initiator(
            _DeadlineSocket(initiator, deadline), *ids[0], ids[1][0])
        finished = time.monotonic()
        thread.join(0.1)
        ok = (finished <= deadline and
              _matches(result, server.get("session", (None, None, None))))
        return ok, (finished - start) * 1000
    except (OSError, TimeoutError, ValueError, ConnectionError):
        return False, (time.monotonic() - start) * 1000
    finally:
        listener.close()
        if initiator is not None:
            initiator.close()
        for conn in accepted:
            conn.close()
        if thread is not None:
            thread.join(0.1)


def _accept_for_hello(hello, peer_sig_pub, sig_secret, cache):
    if hello not in cache:
        cache[hello] = v1.accept_hello(hello, peer_sig_pub, sig_secret)
    return cache[hello]


def _wait(sock, deadline, writable=False):
    timeout = _remaining(deadline)
    ready = select.select([] if writable else [sock], [sock] if writable else [], [], timeout)
    if not (ready[1] if writable else ready[0]):
        raise TimeoutError("handshake deadline expired")


def _send_udp(sock, payload, addr, deadline):
    while True:
        _wait(sock, deadline, writable=True)
        try:
            sock.sendto(payload, addr)
            return
        except BlockingIOError:
            continue


def _recv_udp(sock, deadline, max_bytes):
    _wait(sock, deadline)
    return sock.recvfrom(max_bytes)


def run_udp(ids, deadline_s, retry: bool, rto_s=0.25) -> tuple[bool, float]:
    start = time.monotonic()
    deadline = start + deadline_s
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    done = threading.Event()
    result = {}
    cache = {}
    thread = None

    def respond():
        try:
            while not done.is_set():
                try:
                    hello, addr = _recv_udp(
                        server, min(deadline, time.monotonic() + 0.05), 65535)
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        return
                    continue
                accept, session = _accept_for_hello(hello, ids[0][0], ids[1][1], cache)
                result["session"] = session
                _send_udp(server, accept, addr, deadline)
        except Exception:
            pass

    ok = False
    try:
        server.bind(("127.0.0.1", 0))
        server.setblocking(False)
        client.setblocking(False)
        thread = threading.Thread(target=respond)
        thread.start()
        hello, kem, kem_pub = v1.build_hello(*ids[0])
        addr = server.getsockname()
        _send_udp(client, hello, addr, deadline)
        next_send = time.monotonic() + rto_s
        while True:
            wait_until = min(deadline, next_send) if retry else deadline
            try:
                reply, peer = _recv_udp(client, wait_until, 65535)
            except TimeoutError:
                if not retry or time.monotonic() >= deadline:
                    return False, (time.monotonic() - start) * 1000
                _send_udp(client, hello, addr, deadline)
                next_send = time.monotonic() + rto_s
                continue
            if peer != addr:
                continue
            initiated = v1.finish(reply, kem, kem_pub, ids[1][0])
            finished = time.monotonic()
            done.set()
            thread.join(0.1)
            ok = (finished <= deadline and
                  _matches(initiated, result.get("session", (None, None, None))))
            return ok, (finished - start) * 1000
    except (OSError, TimeoutError, ValueError):
        return False, (time.monotonic() - start) * 1000
    finally:
        done.set()
        client.close()
        server.close()
        if thread is not None:
            thread.join(0.1)


def measure(transport, attempts, deadline_s) -> dict:
    runs = {"tcp": lambda ids: run_tcp(ids, deadline_s),
            "udp-naive": lambda ids: run_udp(ids, deadline_s, retry=False),
            "udp-retry": lambda ids: run_udp(ids, deadline_s, retry=True)}
    if transport not in runs:
        raise ValueError(f"unknown transport: {transport}")
    if attempts <= 0 or deadline_s <= 0:
        raise ValueError("attempts and deadline_s must be positive")

    ids = v1.make_identity(), v1.make_identity()
    hello, _, _ = v1.build_hello(*ids[0])
    accept, _ = v1.accept_hello(hello, ids[0][0], ids[1][1])
    hello_bytes, accept_bytes = len(hello), len(accept)
    successes, elapsed = 0, []
    for _ in range(attempts):
        ok, ms = runs[transport](ids)
        if ok:
            successes += 1
            elapsed.append(ms)
    median_ms = sorted(elapsed)[len(elapsed) // 2] if elapsed else math.nan
    fragments = (expected_fragments(hello_bytes) + expected_fragments(accept_bytes)
                 if transport.startswith("udp") else 0)
    return {"name": transport, "attempts": attempts, "successes": successes,
            "success_pct": 100 * successes / attempts, "median_ms": median_ms,
            "deadline_s": deadline_s, "hello_bytes": hello_bytes,
            "accept_bytes": accept_bytes, "fragments": fragments}
