from hpqv.udp import open_udp

def test_loopback_send_recv():
    a = open_udp(); b = open_udp()
    a.sendto(b"hello", b.getsockname())
    data, _ = b.recvfrom(2048)
    assert data == b"hello"
