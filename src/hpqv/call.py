from hpqv import packet
from hpqv.audio import OpusCodec
from hpqv.jitter import JitterBuffer

def send_stream(udp_sock, dest, session, frames) -> int:
    key, prefix = session
    codec = OpusCodec()
    n = 0
    for seq, pcm in enumerate(frames):
        opus = codec.encode(pcm)
        dg = packet.seal(key, prefix, seq, opus)
        udp_sock.sendto(dg, dest)
        n += 1
    return n

def recv_stream(udp_sock, session, expected: int) -> list[bytes]:
    key, prefix = session
    codec = OpusCodec()
    jb = JitterBuffer(depth=3)
    out, got = [], 0
    udp_sock.settimeout(2.0)
    while got < expected:
        try:
            dg, _ = udp_sock.recvfrom(2048)
        except OSError:
            break
        seq, _flags, opus = packet.open_(key, prefix, dg)
        jb.push(seq, opus)
        got += 1
        frame = jb.pop()
        if frame is not None:
            out.append(codec.decode(frame))
    for frame in jb.flush():
        out.append(codec.decode(frame))
    return out
