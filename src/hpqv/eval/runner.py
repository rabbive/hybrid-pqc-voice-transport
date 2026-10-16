import os
import socket
import struct
import subprocess
import tempfile
import threading
import time

from hpqv import packet, secure
from hpqv.audio import FRAME_BYTES, OpusCodec
from hpqv.keyring import Keyring
from hpqv.udp import open_udp
from hpqv.eval import baselines, stats
from hpqv.eval import mos as mos_mod
from hpqv.eval.capture import capture, count_ip_fragments
from hpqv.eval.netem import netem

SILENCE = b"\x00" * FRAME_BYTES
# The v1 HELLO was 6,446 B. The blob is random bytes of about that size: all this
# scenario needs is one UDP datagram oversized past the 1500B MTU.
NAIVE_HANDSHAKE_BLOB_LEN = 6445


def _keyrings():
    """(sender, receiver) v2 keyrings from a fresh TLS 1.3 handshake on loopback.

    v1 voice packets are gone from production, so the hybrid side seals v2
    datagrams. Both formats are <= 1200 B, so the fragment counts these
    scenarios report are unchanged.
    """
    client, server = secure.local_pair()
    try:
        return Keyring(client.media_keys()), Keyring(server.media_keys())
    finally:
        client.close()
        server.close()


def _tcp_pair():
    """A plain loopback TCP connection (the voice-over-TCP baseline's transport)."""
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        cli = socket.create_connection(srv.getsockname())
        conn, _ = srv.accept()
    return cli, conn


def _send_stream(udp_sock, dest, keyring, frames) -> int:
    codec = OpusCodec()
    for seq, pcm in enumerate(frames):
        udp_sock.sendto(keyring.seal(seq, codec.encode(pcm)), dest)
    return len(frames)


def _udp_recv_measure(sock, keyring, expected, timeout=5.0):
    # Datagrams that fail to open (e.g. the naive scenario's reassembled
    # handshake blob) are noise, not measurement points — skip and keep waiting.
    sock.settimeout(timeout)
    seqs, arrivals = [], []
    while len(seqs) < expected:
        try:
            dg, _ = sock.recvfrom(packet.MAX_DATAGRAM + 1)
        except OSError:
            break
        try:
            seq, _flags, _frame = keyring.open_(dg)
        except packet.Reject:
            continue
        seqs.append(seq)
        arrivals.append(time.monotonic())
    return seqs, arrivals


def _tcp_recv_measure(sock, keyring, expected, timeout=10.0):
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
        if ln > packet.MAX_DATAGRAM:
            break
        dg = recvn(ln)
        if dg is None:
            break
        seq, _flags, _frame = keyring.open_(dg)
        seqs.append(seq)
        arrivals.append(time.monotonic())
    return seqs, arrivals


def _run_hybrid(frames, loss_pct, delay_ms, jitter_ms, pcap):
    ring_a, ring_b = _keyrings()

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
                        _udp_recv_measure(rx, ring_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            _send_stream(tx, dest, ring_a, frames)
            t.join(timeout=10.0)
        time.sleep(0.3)

    tx.close()
    rx.close()
    return result.get("seqs", []), result.get("arrivals", []), connect_time


def _run_naive(frames, loss_pct, delay_ms, jitter_ms, pcap):
    ring_a, ring_b = _keyrings()

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
                        _udp_recv_measure(rx, ring_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            _send_stream(tx, dest, ring_a, frames)
            t.join(timeout=10.0)
        time.sleep(0.3)

    tx.close()
    rx.close()
    return result.get("seqs", []), result.get("arrivals", []), connect_time


def _run_tcp(frames, loss_pct, delay_ms, jitter_ms, pcap):
    ring_a, ring_b = _keyrings()
    cli, conn = _tcp_pair()

    result = {}
    with capture("lo", pcap):
        time.sleep(0.3)
        with netem(loss_pct, delay_ms, jitter_ms, dev="lo"):
            t = threading.Thread(
                target=lambda: result.update(
                    zip(("seqs", "arrivals"),
                        _tcp_recv_measure(conn, ring_b, len(frames)))))
            t.start()
            connect_time = time.monotonic()
            baselines.tcp_voice_send(cli, ring_a, frames)
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
