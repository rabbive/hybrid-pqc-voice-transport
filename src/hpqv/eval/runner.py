import os
import socket
import struct
import subprocess
import tempfile
import threading
import time

from hpqv import call, control, packet
from hpqv import handshake as h
from hpqv.audio import FRAME_BYTES
from hpqv.udp import open_udp
from hpqv.eval import baselines, stats
from hpqv.eval import mos as mos_mod
from hpqv.eval.capture import capture, count_ip_fragments
from hpqv.eval.netem import netem

SILENCE = b"\x00" * FRAME_BYTES
NAIVE_HANDSHAKE_BLOB_LEN = 6445  # a realistic PQC handshake, oversized past the 1500B MTU


def _handshake_pair():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    box = {}

    def responder():
        conn, _ = srv.accept()
        box["conn"] = conn
        box["session"] = control.run_responder(conn, b_pub, b_sec, a_pub)

    t = threading.Thread(target=responder)
    t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    sess_a = control.run_initiator(cli, a_pub, a_sec, b_pub)
    t.join()
    srv.close()
    return sess_a, box["session"], cli, box["conn"]


def _udp_recv_measure(sock, key, recv_prefix, expected, timeout=5.0):
    # Datagrams that fail to open (e.g. the naive scenario's reassembled
    # handshake blob) are noise, not measurement points — skip and keep waiting.
    sock.settimeout(timeout)
    seqs, arrivals = [], []
    while len(seqs) < expected:
        try:
            dg, _ = sock.recvfrom(65535)
        except OSError:
            break
        try:
            seq, _flags, _frame = packet.open_(key, recv_prefix, dg)
        except Exception:
            continue
        seqs.append(seq)
        arrivals.append(time.monotonic())
    return seqs, arrivals


def _tcp_recv_measure(sock, key, recv_prefix, expected, timeout=10.0):
    sock.settimeout(timeout)
    seqs, arrivals = [], []
    buf = b""

    def recvn(n):
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
        r, buf = buf[:n], buf[n:]
        return r

    for _ in range(expected):
        hdr = recvn(4)
        if hdr is None:
            break
        (ln,) = struct.unpack(">I", hdr)
        dg = recvn(ln)
        if dg is None:
            break
        seq, _flags, _frame = packet.open_(key, recv_prefix, dg)
        seqs.append(seq)
        arrivals.append(time.monotonic())
    return seqs, arrivals


def _run_hybrid(frames, loss_pct, delay_ms, jitter_ms, pcap):
    sess_a, sess_b, cli, conn = _handshake_pair()
    cli.close()
    conn.close()
    key_b, _send_prefix_b, recv_prefix_b = sess_b

    tx = open_udp()
    rx = open_udp()
    dest = ("127.0.0.1", rx.getsockname()[1])

    result = {}
    with capture("lo", pcap):
        time.sleep(0.3)
        with netem(loss_pct, delay_ms, jitter_ms, dev="lo"):
            t = threading.Thread(
                target=lambda: result.update(
                    zip(("seqs", "arrivals"),
                        _udp_recv_measure(rx, key_b, recv_prefix_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            call.send_stream(tx, dest, sess_a, frames)
            t.join(timeout=10.0)
        time.sleep(0.3)

    tx.close()
    rx.close()
    return result.get("seqs", []), result.get("arrivals", []), connect_time


def _run_naive(frames, loss_pct, delay_ms, jitter_ms, pcap):
    sess_a, sess_b, cli, conn = _handshake_pair()
    cli.close()
    conn.close()
    key_b, _send_prefix_b, recv_prefix_b = sess_b

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    dest = ("127.0.0.1", rx.getsockname()[1])

    result = {}
    with capture("lo", pcap):
        time.sleep(0.3)
        with netem(loss_pct, delay_ms, jitter_ms, dev="lo"):
            # The oversized handshake blob is the villain: it fragments at the IP
            # layer. Send it first so the capture window includes the fragments.
            blob = os.urandom(NAIVE_HANDSHAKE_BLOB_LEN)
            baselines.naive_send_handshake_udp(tx, dest, blob)
            time.sleep(0.2)

            t = threading.Thread(
                target=lambda: result.update(
                    zip(("seqs", "arrivals"),
                        _udp_recv_measure(rx, key_b, recv_prefix_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            call.send_stream(tx, dest, sess_a, frames)
            t.join(timeout=10.0)
        time.sleep(0.3)

    tx.close()
    rx.close()
    return result.get("seqs", []), result.get("arrivals", []), connect_time


def _run_tcp(frames, loss_pct, delay_ms, jitter_ms, pcap):
    sess_a, sess_b, cli, conn = _handshake_pair()
    key_b, _send_prefix_b, recv_prefix_b = sess_b

    result = {}
    with capture("lo", pcap):
        time.sleep(0.3)
        with netem(loss_pct, delay_ms, jitter_ms, dev="lo"):
            t = threading.Thread(
                target=lambda: result.update(
                    zip(("seqs", "arrivals"),
                        _tcp_recv_measure(conn, key_b, recv_prefix_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            baselines.tcp_voice_send(cli, sess_a, frames)
            t.join(timeout=30.0)
        time.sleep(0.3)

    cli.close()
    conn.close()
    return result.get("seqs", []), result.get("arrivals", []), connect_time


_SCENARIOS = {"hybrid": _run_hybrid, "naive": _run_naive, "tcp": _run_tcp}


def run_scenario(name, loss_pct, delay_ms, jitter_ms, n_frames=250) -> dict:
    if name not in _SCENARIOS:
        raise ValueError(f"unknown scenario: {name}")
    frames = [SILENCE] * n_frames

    fd, pcap = tempfile.mkstemp(suffix=".pcap")
    os.close(fd)
    # Docker's loopback defaults to a 65536 MTU, under which nothing ever
    # fragments — pin it to the standard Ethernet 1500B for the run.
    subprocess.run(["ip", "link", "set", "lo", "mtu", "1500"], check=True)
    try:
        seqs, arrivals, connect_time = _SCENARIOS[name](
            frames, loss_pct, delay_ms, jitter_ms, pcap)
        frag_count = count_ip_fragments(pcap)
    finally:
        subprocess.run(["ip", "link", "set", "lo", "mtu", "65536"], check=True)
        os.unlink(pcap)

    drop = stats.drop_rate(n_frames, seqs)
    jitter_measured = stats.mean_jitter_ms(arrivals) if arrivals else 0.0
    ttfb = stats.ttfb_ms(connect_time, arrivals[0]) if arrivals else 0.0
    m = mos_mod.mos_from_network(delay_ms, jitter_ms, loss_pct=drop * 100)

    return {
        "name": name,
        "loss_pct": loss_pct,
        "delay_ms": delay_ms,
        "frag_count": frag_count,
        "drop_rate": drop,
        "jitter_ms": jitter_measured,
        "ttfb_ms": ttfb,
        "mos": m,
    }
