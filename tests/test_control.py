import socket, threading
from hpqv import handshake as h, control as c

def test_handshake_over_tcp_localhost():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    out = {}
    def responder():
        conn, _ = srv.accept()
        out["b"] = c.run_responder(conn, b_pub, b_sec, a_pub)
    t = threading.Thread(target=responder); t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    out["a"] = c.run_initiator(cli, a_pub, a_sec, b_pub)
    t.join()
    assert out["a"][0] == out["b"][0]
    assert out["a"][1] == out["b"][2] and out["a"][2] == out["b"][1]

def test_control_message_round_trip():
    s1, s2 = socket.socketpair()
    c.send_control(s1, c.MSG_KEY_ROTATE, b"gen1")
    t, payload = c.recv_control(s2)
    assert t == c.MSG_KEY_ROTATE and payload == b"gen1"

def test_control_message_empty_payload():
    s1, s2 = socket.socketpair()
    c.send_control(s1, c.MSG_CALL_START)
    t, payload = c.recv_control(s2)
    assert t == c.MSG_CALL_START and payload == b""
