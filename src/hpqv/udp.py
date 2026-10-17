import errno
import socket

# Read whole datagrams, whatever their size: Windows raises WSAEMSGSIZE instead of
# truncating when a datagram is longer than the buffer, which would turn one forged
# packet into a socket error. 65535 holds any UDP payload; the keyring then rejects
# anything over packet.MAX_DATAGRAM as "oversize".
UDP_RECV_BUFFER = 65535


def is_oversize_error(e: OSError) -> bool:
    """WSAEMSGSIZE / EMSGSIZE: one datagram was dropped, the socket is fine."""
    return getattr(e, "winerror", None) == 10040 or e.errno in (10040, errno.EMSGSIZE)


def open_udp(bind_addr=("127.0.0.1", 0)) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(bind_addr)
    # Set Don't-Fragment so oversize packets error loudly instead of fragmenting.
    if hasattr(socket, "IP_MTU_DISCOVER"):
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MTU_DISCOVER, socket.IP_PMTUDISC_DO)
    # Windows turns a (spoofable) ICMP port-unreachable into WSAECONNRESET on the next
    # recvfrom; switch that off so stray ICMP cannot disturb the receive loop.
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    return s
