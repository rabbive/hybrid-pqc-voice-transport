"""Frozen-side checks, run as `hpqv-cli selfcheck`: what the unit tests cover, inside the bundle."""
import math
import socket
import struct
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path


def _wait(cond, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def opus_fec_and_version():
    import ctypes
    import opuslib.api
    from hpqv import audio, playout
    lib = opuslib.api.libopus
    bundle = str(Path(sys._MEIPASS).resolve())
    if not str(Path(lib._name).resolve()).startswith(bundle):
        raise RuntimeError(f"libopus not from the bundle: {lib._name}")
    lib.opus_get_version_string.restype = ctypes.c_char_p
    version = lib.opus_get_version_string().decode()
    codec = audio.OpusCodec(fec=True)
    if audio._encoder_get(codec.enc, 4013) != 1:      # OPUS_GET_INBAND_FEC_REQUEST
        raise RuntimeError("INBAND_FEC did not read back as 1")
    # Pinned Opus 1.5.2 must export it; playout silently falls back otherwise.
    if not hasattr(lib, "opus_packet_has_lbrr") or playout._HAS_LBRR is None:
        raise RuntimeError(f"opus_packet_has_lbrr missing from bundled {version}")
    return f"{version}; INBAND_FEC=1; opus_packet_has_lbrr present"


def telemetry_export():
    from hpqv.controls import CallControls
    from hpqv.demo import Stats
    from hpqv.telemetry import Telemetry
    stats = Stats()
    tel = Telemetry(stats, CallControls())
    for t in (1.0, 2.0, 3.0):
        stats.rx_expected += 50
        stats.received += 45
        stats.recovered += 3
        stats.rx_transit_n += 45
        stats.rx_transit_sum_ms += 45 * 20.0
        tel.sample(t)
    with tempfile.TemporaryDirectory(prefix="hpqv telemetry ") as out:
        paths = tel.export(out)
        pngs = [p for p in paths if p.endswith(".png")]
        for p in pngs:
            if Path(p).read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError(f"not a PNG: {p}")
        for p in paths:
            if Path(p).stat().st_size == 0:
                raise RuntimeError(f"empty export: {p}")
    return f"{len(pngs)} PNGs + csv + summary written"


def rotate_call():
    from hpqv import control, demo, handshake as h
    from hpqv.audio import FS
    from hpqv.callctl import CallHandle
    with tempfile.TemporaryDirectory(prefix="hpqv rotate ") as tmp:
        tmp = Path(tmp)
        pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / FS)))
                       for i in range(FS * 5))
        with wave.open(str(tmp / "tone.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FS)
            w.writeframes(pcm)
        srv = socket.create_server(("127.0.0.1", 0))
        caller_tcp = socket.create_connection(srv.getsockname())
        listener_tcp, _ = srv.accept()
        srv.close()
        a_pub, a_sec = h.make_identity()
        b_pub, b_sec = h.make_identity()
        sessions = {}
        t = threading.Thread(target=lambda: sessions.__setitem__(
            "listener", control.run_responder(listener_tcp, b_pub, b_sec, a_pub)))
        t.start()
        sessions["caller"] = control.run_initiator(caller_tcp, a_pub, a_sec, b_pub)
        t.join()
        udp = {}
        for role in ("caller", "listener"):
            udp[role] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp[role].bind(("127.0.0.1", 0))
        peers = {}
        for role, tcp, other in (("caller", caller_tcp, "listener"),
                                 ("listener", listener_tcp, "caller")):
            args = demo.call_args(input="file", wav=str(tmp / "tone.wav"), seconds=5,
                                  output="file", out_wav=str(tmp / f"{role}.wav"))
            handle, stats, stop = CallHandle(), demo.Stats(), threading.Event()
            thread = threading.Thread(target=demo._run_call, args=(
                tcp, udp[role], udp[other].getsockname(), sessions[role], args),
                kwargs=dict(stats=stats, stop_event=stop, call_handle=handle))
            peers[role] = (handle, stats, stop, thread)
        try:
            for *_, thread in peers.values():
                thread.start()
            (hc, sc, _, tc), (hl, sl, _, tl) = peers["caller"], peers["listener"]
            if not _wait(lambda: sc.received > 20 and sl.received > 20, 10):
                raise RuntimeError("no audio before rotation")
            before = hc.key_fingerprint()
            hc.rotate_keys()
            if not _wait(lambda: sc.key_gen == 1 and sl.key_gen == 1, 5):
                raise RuntimeError("rotation did not reach both peers")
            if not hc.key_fingerprint() == hl.key_fingerprint() != before:
                raise RuntimeError("key fingerprints differ after rotation")
            rc, rl = sc.received, sl.received
            time.sleep(0.6)
            if sc.received - rc < 15 or sl.received - rl < 15:
                raise RuntimeError("audio stopped after rotation")
            tc.join(timeout=15)
            tl.join(timeout=15)
            if tc.is_alive() or tl.is_alive():
                raise RuntimeError("call did not finish")
        finally:
            for *_, stop, thread in peers.values():
                stop.set()
                thread.join(timeout=3)
            for s in (caller_tcp, listener_tcp, *udp.values()):
                s.close()
    return "file-to-file call rotated keys mid-call and completed"


CHECKS = {"opus": opus_fec_and_version, "telemetry": telemetry_export, "rotate": rotate_call}


def main():
    failed = False
    for name, check in CHECKS.items():
        try:
            print(f"PASS {name}: {check()}", flush=True)
        except Exception as exc:
            failed = True
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
    return 1 if failed else 0
