import os
import shutil
import socket
import struct
import subprocess
import threading
import time

import pytest

from hpqv import packet, secure
from hpqv.eval import baselines
from hpqv.eval.capture import count_ip_fragments
from hpqv.keyring import Keyring

needs_linux_tools = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)


@needs_linux_tools
def test_naive_udp_handshake_fragments(tmp_path):
    # A realistic PQC handshake blob exceeds the 1500B MTU (~6.4KB): must fragment.
    # Docker's loopback defaults to a 65536 MTU (no fragmentation possible below
    # that), so pin it to the standard Ethernet 1500B for this test.
    subprocess.run(["ip", "link", "set", "lo", "mtu", "1500"], check=True)
    try:
        blob = os.urandom(6445)
        pcap = tmp_path / "naive.pcap"
        cap = subprocess.Popen(
            ["tshark", "-i", "lo", "-w", str(pcap), "-f", "udp port 5555"],
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 5555))
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        baselines.naive_send_handshake_udp(tx, ("127.0.0.1", 5555), blob)
        time.sleep(1.0)
        cap.terminate()
        cap.wait()
        assert count_ip_fragments(str(pcap)) > 0  # THE villain fragments
    finally:
        subprocess.run(["ip", "link", "set", "lo", "mtu", "65536"], check=True)


@pytest.fixture
def rings():
    client, server = secure.local_pair()
    try:
        yield Keyring(client.media_keys()), Keyring(server.media_keys())
    finally:
        client.close()
        server.close()


def test_tcp_voice_round_trip(rings):
    ring_a, ring_b = rings
    cli, conn = socket.socketpair()
    pcm = b"\x11\x22" * 960
    frames = [pcm] * 5
    out = {}
    rx = threading.Thread(target=lambda: out.__setitem__(
        "frames", baselines.tcp_voice_recv(conn, ring_b, expected=len(frames))))
    rx.start()
    sent = baselines.tcp_voice_send(cli, ring_a, frames)
    rx.join(10)
    cli.close()
    conn.close()

    assert sent == len(frames)
    assert len(out["frames"]) == len(frames)
    assert all(len(f) == len(pcm) for f in out["frames"])


def test_tcp_voice_recv_rejects_oversize_length_before_allocating(rings):
    _, ring_b = rings
    cli, conn = socket.socketpair()
    with cli, conn:
        cli.sendall(struct.pack(">I", 200 * 1024 * 1024))
        with pytest.raises(ValueError):
            baselines.tcp_voice_recv(conn, ring_b, expected=1)


def test_tcp_voice_recv_counts_a_tampered_datagram(rings):
    ring_a, ring_b = rings
    cli, conn = socket.socketpair()
    with cli, conn:
        dg = bytearray(ring_a.seal(0, b"\x01" * 40))
        dg[-1] ^= 1
        cli.sendall(struct.pack(">I", len(dg)) + bytes(dg))
        with pytest.raises(packet.Reject) as exc:
            baselines.tcp_voice_recv(conn, ring_b, expected=1)
    assert exc.value.reason == "auth"
    assert ring_b.rejects["auth"] == 1
