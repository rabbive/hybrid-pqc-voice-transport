import struct
from hpqv import packet


def naive_send_handshake_udp(udp_sock, dest, blob: bytes) -> int:
    # Deliberately naive: no framing, no DF — one oversized datagram the kernel
    # must fragment at the IP layer. This is the baseline our hybrid beats.
    return udp_sock.sendto(blob, dest)


def tcp_voice_send(sock, keyring, frames) -> int:
    """Voice over one TCP stream, each v2 datagram length-prefixed. `keyring` is a keyring.Keyring."""
    from hpqv.audio import OpusCodec
    codec = OpusCodec()
    n = 0
    for seq, pcm in enumerate(frames):
        dg = keyring.seal(seq, codec.encode(pcm))
        sock.sendall(struct.pack(">I", len(dg)) + dg)
        n += 1
    return n


def tcp_voice_recv(sock, keyring, expected) -> list:
    from hpqv.audio import OpusCodec
    codec = OpusCodec()
    out = []
    buf = b""

    def recvn(n):
        nonlocal buf
        while len(buf) < n:
            c = sock.recv(4096)
            if not c:
                return None
            buf += c
        r, buf = buf[:n], buf[n:]
        return r

    for _ in range(expected):
        hdr = recvn(4)
        if hdr is None:
            break
        (ln,) = struct.unpack(">I", hdr)
        if ln > packet.MAX_DATAGRAM:     # bounded: never allocate what the peer names
            raise ValueError("voice record too large")
        dg = recvn(ln)
        if dg is None:
            break
        _seq, _flags, opus = keyring.open_(dg)
        out.append(codec.decode(opus))
    return out
