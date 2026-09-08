import socket

def open_udp(bind_addr=("127.0.0.1", 0)) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(bind_addr)
    # Set Don't-Fragment so oversize packets error loudly instead of fragmenting.
    if hasattr(socket, "IP_MTU_DISCOVER"):
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MTU_DISCOVER, socket.IP_PMTUDISC_DO)
    return s
