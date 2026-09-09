import struct
from hpqv import handshake as h

def send_record(sock, payload: bytes) -> None:
    sock.sendall(struct.pack(">I", len(payload)) + payload)

def _recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf

def recv_record(sock) -> bytes:
    (length,) = struct.unpack(">I", _recvn(sock, 4))
    return _recvn(sock, length)

# Control-plane message types (sent post-handshake over the TCP channel).
MSG_CALL_START, MSG_CALL_END, MSG_KEY_ROTATE = 1, 2, 3

def send_control(sock, msg_type: int, payload: bytes = b"") -> None:
    send_record(sock, bytes([msg_type]) + payload)

def recv_control(sock) -> tuple:
    rec = recv_record(sock)
    return rec[0], rec[1:]

def run_initiator(sock, sig_pub, sig_secret, peer_sig_pub):
    hello, kem, kem_pub = h.build_hello(sig_pub, sig_secret)
    send_record(sock, hello)
    accept = recv_record(sock)
    return h.finish(accept, kem, kem_pub, peer_sig_pub)

def run_responder(sock, sig_pub, sig_secret, peer_sig_pub):
    hello = recv_record(sock)
    accept, session = h.accept_hello(hello, peer_sig_pub, sig_secret)
    send_record(sock, accept)
    return session
