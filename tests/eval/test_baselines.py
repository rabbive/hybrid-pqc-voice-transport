import os
import socket
import subprocess
import threading
import time

import shutil, pytest
pytestmark = pytest.mark.skipif(
    shutil.which("tc") is None or shutil.which("tshark") is None,
    reason="needs Linux tc/tshark — run in the hpqv-eval container",
)

from hpqv import control as c
from hpqv import handshake as h
from hpqv.eval import baselines
from hpqv.eval.capture import count_ip_fragments


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


def test_tcp_voice_round_trip():
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
        box["b"] = c.run_responder(conn, b_pub, b_sec, a_pub)
        box["conn"] = conn

    t = threading.Thread(target=responder)
    t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    sess_a = c.run_initiator(cli, a_pub, a_sec, b_pub)
    t.join()
    sess_b = box["b"]

    pcm = b"\x11\x22" * 960
    frames = [pcm, pcm, pcm, pcm, pcm]
    out = {}
    rx = threading.Thread(
        target=lambda: out.__setitem__(
            "frames", baselines.tcp_voice_recv(box["conn"], sess_b, expected=len(frames))
        )
    )
    rx.start()
    sent = baselines.tcp_voice_send(cli, sess_a, frames)
    rx.join()

    assert sent == len(frames)
    assert len(out["frames"]) == len(frames)
    assert all(len(f) == len(pcm) for f in out["frames"])
