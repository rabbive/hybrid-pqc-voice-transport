"""Write a sample pcap of one hpqv v2 call, for rehearsing the Wireshark demo
without root (live capture on macOS needs /dev/bpf access).

Everything in it is real protocol output: a full TLS 1.3 handshake between two
fresh identities (run in memory by OpenSSL, so X25519MLKEM768 and ML-DSA-65 are
what OpenSSL actually negotiated), READY and CALL_START, then voice datagrams
sealed by the real Keyring with the exporter keys, with one key rotation.
Only the IP/TCP/UDP framing is synthesized.

    uv run python tools/wireshark/make_sample_pcap.py OUT.pcap
"""
import struct
import sys

from OpenSSL import SSL
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.utils import wrpcap

from hpqv import control, identity, secure
from hpqv.keyring import Keyring
from hpqv.packet import FLAG_FEC

CALLER, LISTENER = ("10.0.0.2", 50123), ("10.0.0.1", 9000)   # 9000: GUI default port
UDP_CALLER = 50124
MSS = 1448
MACS = {True: ("02:00:00:00:00:02", "02:00:00:00:00:01"),
        False: ("02:00:00:00:00:01", "02:00:00:00:00:02")}
FRAMES = 100             # 2 s of 20 ms voice


def _pump(src, dst):
    out = b""
    while True:
        try:
            out += src.bio_read(65536)
        except SSL.WantReadError:
            break
    if out:
        dst.bio_write(out)
    return out


def tls_session():
    """(records as [(from_caller, bytes)], client conn, server conn)."""
    caller, listener = identity.new("caller"), identity.new("listener")
    c = SSL.Connection(secure._context(caller, listener.cert_pem, "client", []), None)
    s = SSL.Connection(secure._context(listener, caller.cert_pem, "server", []), None)
    c.set_connect_state()
    s.set_accept_state()
    flights = []
    for _ in range(10):
        for conn, peer, from_caller in ((c, s, True), (s, c, False)):
            try:
                conn.do_handshake()
            except SSL.WantReadError:
                pass
            data = _pump(conn, peer)
            if data:
                flights.append((from_caller, data))
    assert c.get_group_name() == secure.GROUP, c.get_group_name()
    s.sendall(bytes([control.MSG_READY, 0, 0]))
    flights.append((False, _pump(s, c)))
    c.sendall(bytes([control.MSG_CALL_START, 0, 2]) + struct.pack(">H", UDP_CALLER))
    flights.append((True, _pump(c, s)))
    return flights, c


def media_keys(conn):
    ex = conn.export_keying_material
    return secure.MediaKeys(send_secret=ex(secure._LABEL_C2S, 32, b""),
                            recv_secret=ex(secure._LABEL_S2C, 32, b""),
                            session_id=ex(secure._LABEL_SESSION_ID, 4, b""))


def packets(flights, ring):
    t, pkts = 0.0, []
    seq = {True: 1000, False: 5000}

    def tcp(from_caller, flags, payload=b""):
        (sh, sp), (dh, dp) = (CALLER, LISTENER) if from_caller else (LISTENER, CALLER)
        src, dst = MACS[from_caller]
        p = (Ether(src=src, dst=dst) / IP(src=sh, dst=dh) /
             TCP(sport=sp, dport=dp, flags=flags, seq=seq[from_caller],
                 ack=seq[not from_caller]) / payload)
        seq[from_caller] += len(payload) + (1 if "S" in flags else 0)
        return p

    for from_caller, flags in ((True, "S"), (False, "SA"), (True, "A")):
        pkts.append(tcp(from_caller, flags))
    for from_caller, data in flights:
        for i in range(0, len(data), MSS):
            pkts.append(tcp(from_caller, "PA", data[i:i + MSS]))
    for n in range(FRAMES):
        if n == FRAMES // 2:
            ring.rotate_next()
        dg = ring.seal(n, bytes(60 + n % 40), extra_flags=FLAG_FEC)  # Opus-sized frames
        pkts.append(Ether(src=MACS[True][0], dst=MACS[True][1]) / IP(src=CALLER[0], dst=LISTENER[0]) /
                    UDP(sport=UDP_CALLER, dport=LISTENER[1]) / dg)
    for i, p in enumerate(pkts):
        p.time = 1_700_000_000 + i * 0.02
    return pkts


def main(out):
    flights, client = tls_session()
    wrpcap(out, packets(flights, Keyring(media_keys(client))))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1])
