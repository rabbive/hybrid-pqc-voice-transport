import threading, socket
from hpqv import handshake as h, control as c, call
from hpqv.udp import open_udp

def test_end_to_end_voice_over_localhost():
    # handshake over TCP
    a_pub, a_sec = h.make_identity(); b_pub, b_sec = h.make_identity()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    box = {}
    def responder():
        conn, _ = srv.accept(); box["b"] = c.run_responder(conn, b_pub, b_sec, a_pub)
    t = threading.Thread(target=responder); t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    sess_a = c.run_initiator(cli, a_pub, a_sec, b_pub); t.join(); sess_b = box["b"]
    assert sess_a == sess_b

    # voice over UDP
    ua, ub = open_udp(), open_udp()
    pcm = b"\x11\x22" * 960
    frames = [pcm, pcm, pcm, pcm, pcm]
    out = {}
    rx = threading.Thread(target=lambda: out.__setitem__("frames",
        call.recv_stream(ub, sess_b, expected=len(frames))))
    rx.start()
    sent = call.send_stream(ua, ub.getsockname(), sess_a, frames)
    rx.join()
    assert sent == len(frames)
    assert len(out["frames"]) == len(frames)
    assert all(len(f) == len(pcm) for f in out["frames"])
