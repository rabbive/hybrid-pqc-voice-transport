"""Minimal one-way voice helpers over a Keyring (no live controls, no GUI)."""
import time

from hpqv import packet
from hpqv.audio import OpusCodec
from hpqv.jitter import JitterBuffer

IDLE_TIMEOUT = 2.0      # stop when nothing authentic arrives for this long
MAX_SECONDS = 60.0      # hard bound, however many forged datagrams keep arriving


def send_stream(udp_sock, dest, ring, frames) -> int:
    codec = OpusCodec()
    n = 0
    for seq, pcm in enumerate(frames):
        udp_sock.sendto(ring.seal(seq, codec.encode(pcm)), dest)
        n += 1
    return n


def recv_stream(udp_sock, ring, expected: int) -> list[bytes]:
    """Decode up to `expected` authentic datagrams. Rejected datagrams are counted by
    the ring and never count toward `expected`."""
    codec = OpusCodec()
    jb = JitterBuffer(depth=3)
    out, got = [], 0
    stop_at = time.monotonic() + MAX_SECONDS
    udp_sock.settimeout(IDLE_TIMEOUT)
    while got < expected and time.monotonic() < stop_at:
        try:
            dg, _ = udp_sock.recvfrom(packet.MAX_DATAGRAM + 1)
        except OSError:
            break
        try:
            seq, _flags, opus = ring.open_(dg)
        except packet.Reject:
            continue
        jb.push(seq, opus)
        got += 1
        popped = jb.pop()
        if popped is not None:
            out.append(codec.decode(popped[1]))
    for _seq, frame in jb.flush():
        out.append(codec.decode(frame))
    return out
