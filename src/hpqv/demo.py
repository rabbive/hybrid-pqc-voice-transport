"""Live two-peer voice demo: real PQC handshake + real-time UDP voice, mic/speaker or file I/O."""
import argparse
import json
import random
import socket
import struct
import sys
import threading
import time
import wave

from hpqv import control, handshake as h, packet
from hpqv.audio import OpusCodec, FRAME_SAMPLES, FRAME_BYTES, FS, CHANNELS

STATUS_INTERVAL = 1.0


def keygen(args):
    a_pub, a_secret = h.make_identity()
    b_pub, b_secret = h.make_identity()
    identity = {
        "listener": {"sig_pub": a_pub.hex(), "sig_secret": a_secret.hex()},
        "caller": {"sig_pub": b_pub.hex(), "sig_secret": b_secret.hex()},
    }
    with open(args.out, "w") as f:
        json.dump(identity, f)
    print(f"wrote identity file: {args.out}")
    print("both listener and caller must use the same --identity-file")


def _load_identity(path, role):
    with open(path) as f:
        identity = json.load(f)
    mine = identity[role]
    other = identity["caller" if role == "listener" else "listener"]
    sig_pub = bytes.fromhex(mine["sig_pub"])
    sig_secret = bytes.fromhex(mine["sig_secret"])
    peer_sig_pub = bytes.fromhex(other["sig_pub"])
    return sig_pub, sig_secret, peer_sig_pub


class FrameSource:
    """Yields 20ms PCM frames, either from a mic or a WAV file."""

    def __init__(self, mode, wav_path=None, seconds=30):
        self.mode = mode
        self.seconds = seconds
        self._stream = None
        if mode == "file":
            self._wav = wave.open(wav_path, "rb")
            if self._wav.getframerate() != FS or self._wav.getnchannels() != CHANNELS:
                raise ValueError(f"--wav must be {FS}Hz mono 16-bit PCM")
        elif mode == "mic":
            try:
                import sounddevice as sd
            except ImportError as e:
                raise RuntimeError(
                    "sounddevice unavailable (no audio device / headless environment). "
                    "Use --input file --output file instead."
                ) from e
            self._sd = sd
            try:
                self._stream = sd.InputStream(
                    samplerate=FS, channels=CHANNELS, dtype="int16",
                    blocksize=FRAME_SAMPLES)
                self._stream.start()
            except Exception as e:
                raise RuntimeError(
                    "no microphone available. Use --input file --output file instead."
                ) from e
        else:
            raise ValueError(mode)

    def frames(self):
        if self.mode == "file":
            while True:
                pcm = self._wav.readframes(FRAME_SAMPLES)
                if len(pcm) < FRAME_BYTES:
                    return
                yield pcm
        else:
            deadline = time.monotonic() + self.seconds
            while time.monotonic() < deadline:
                data, _overflow = self._stream.read(FRAME_SAMPLES)
                yield bytes(data)

    def close(self):
        if self.mode == "file":
            self._wav.close()
        elif self._stream is not None:
            self._stream.stop()
            self._stream.close()


class FrameSink:
    """Consumes decoded PCM frames, either to a speaker or a WAV file."""

    def __init__(self, mode, wav_path=None):
        self.mode = mode
        self._frames = []
        self._stream = None
        if mode == "speaker":
            try:
                import sounddevice as sd
            except ImportError as e:
                raise RuntimeError(
                    "sounddevice unavailable (no audio device / headless environment). "
                    "Use --input file --output file instead."
                ) from e
            try:
                self._stream = sd.OutputStream(
                    samplerate=FS, channels=CHANNELS, dtype="int16",
                    blocksize=FRAME_SAMPLES)
                self._stream.start()
            except Exception as e:
                raise RuntimeError(
                    "no speaker available. Use --input file --output file instead."
                ) from e
        elif mode == "file":
            self._wav_path = wav_path
        else:
            raise ValueError(mode)

    def write(self, pcm: bytes):
        if self.mode == "speaker":
            self._stream.write(pcm)
        else:
            self._frames.append(pcm)

    def close(self):
        if self.mode == "speaker":
            self._stream.stop()
            self._stream.close()
        else:
            with wave.open(self._wav_path, "wb") as w:
                w.setnchannels(CHANNELS)
                w.setsampwidth(2)
                w.setframerate(FS)
                for pcm in self._frames:
                    w.writeframes(pcm)


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.sent = 0
        self.received = 0
        self.dropped = 0

    def snapshot(self):
        with self.lock:
            return self.sent, self.received, self.dropped


def _sender_loop(udp_sock, dest, session, source, drop_pct, stats, stop_event):
    key, send_prefix, _recv_prefix = session
    codec = OpusCodec()
    seq = 0
    try:
        for pcm in source.frames():
            if stop_event.is_set():
                break
            if drop_pct > 0 and random.random() * 100 < drop_pct:
                with stats.lock:
                    stats.dropped += 1
                seq += 1
                continue
            opus = codec.encode(pcm)
            dg = packet.seal(key, send_prefix, seq, opus)
            udp_sock.sendto(dg, dest)
            with stats.lock:
                stats.sent += 1
            seq += 1
    finally:
        stop_event.set()


def _receiver_loop(udp_sock, session, sink, jitter_depth, stats, stop_event):
    from hpqv.jitter import JitterBuffer
    key, _send_prefix, recv_prefix = session
    codec = OpusCodec()
    jb = JitterBuffer(depth=jitter_depth)
    udp_sock.settimeout(0.5)
    while not stop_event.is_set():
        try:
            dg, _addr = udp_sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            seq, _flags, opus = packet.open_(key, recv_prefix, dg)
        except Exception:
            continue
        jb.push(seq, opus)
        with stats.lock:
            stats.received += 1
        frame = jb.pop()
        if frame is not None:
            sink.write(codec.decode(frame))
    for frame in jb.flush():
        sink.write(codec.decode(frame))


def _status_loop(stats, drop_pct, stop_event):
    while not stop_event.is_set():
        sent, received, dropped = stats.snapshot()
        print(f"\r[status] sent={sent} received={received} dropped={dropped} drop_pct={drop_pct}%   ",
              end="", flush=True)
        stop_event.wait(STATUS_INTERVAL)
    print()


def _run_call(sock_tcp, udp_sock, dest, session, args):
    source = FrameSource(args.input, wav_path=args.wav, seconds=args.seconds)
    sink = FrameSink(args.output, wav_path=args.out_wav)
    stats = Stats()
    stop_event = threading.Event()

    if args.drop_pct > 0:
        print(f"loss injection active: dropping {args.drop_pct}% of outgoing packets")

    status_thread = threading.Thread(target=_status_loop, args=(stats, args.drop_pct, stop_event))
    recv_thread = threading.Thread(
        target=_receiver_loop,
        args=(udp_sock, session, sink, args.jitter_depth, stats, stop_event))
    status_thread.start()
    recv_thread.start()

    try:
        _sender_loop(udp_sock, dest, session, source, args.drop_pct, stats, stop_event)
        if args.input == "file":
            # let receiver drain remaining in-flight datagrams
            time.sleep(0.5)
    finally:
        stop_event.set()
        recv_thread.join(timeout=2)
        status_thread.join(timeout=2)
        source.close()
        sink.close()

    sent, received, dropped = stats.snapshot()
    print(f"call ended: sent={sent} received={received} dropped={dropped}")


def listen(args):
    if not args.identity_file:
        raise RuntimeError(
            "listener requires --identity-file to pin the caller's identity. "
            "Run `python -m hpqv.demo keygen --out identity.json` and share it with the caller.")
    sig_pub, sig_secret, peer_sig_pub = _load_identity(args.identity_file, "listener")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(1)
    print(f"listening on port {args.port}, fingerprint {sig_pub[:8].hex()}...")
    conn, addr = srv.accept()
    print(f"connection from {addr}")

    session = control.run_responder(conn, sig_pub, sig_secret, peer_sig_pub)
    print("handshake complete")

    udp_sock = _open_bound_udp(args.port)
    _msg_type, payload = control.recv_control(conn)
    (caller_udp_port,) = struct.unpack(">H", payload)
    peer_udp = (addr[0], caller_udp_port)
    _run_call(conn, udp_sock, peer_udp, session, args)
    conn.close()


def call(args):
    host, port_s = args.target.rsplit(":", 1)
    port = int(port_s)

    if not args.identity_file:
        raise RuntimeError(
            "caller requires --identity-file to pin the listener's identity. "
            "Run `python -m hpqv.demo keygen --out identity.json` and share it with the listener.")
    sig_pub, sig_secret, peer_sig_pub = _load_identity(args.identity_file, "caller")

    sock_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock_tcp.connect((host, port))
    print(f"connected to {host}:{port}")

    session = control.run_initiator(sock_tcp, sig_pub, sig_secret, peer_sig_pub)
    print("handshake complete")

    udp_sock = _open_bound_udp(0)
    udp_port = udp_sock.getsockname()[1]
    control.send_control(sock_tcp, control.MSG_CALL_START, struct.pack(">H", udp_port))
    peer_udp = (host, port)
    _run_call(sock_tcp, udp_sock, peer_udp, session, args)
    sock_tcp.close()


def _open_bound_udp(port):
    from hpqv.udp import open_udp
    return open_udp(("0.0.0.0", port))


def _add_common_call_args(p):
    p.add_argument("--identity-file", help="shared identity file from `demo.py keygen`")
    p.add_argument("--input", choices=["mic", "file"], default="mic")
    p.add_argument("--wav", help="input WAV path, required when --input file")
    p.add_argument("--output", choices=["speaker", "file"], default="speaker")
    p.add_argument("--out-wav", help="output WAV path, required when --output file")
    p.add_argument("--drop-pct", type=float, default=0, help="%% of outgoing packets to randomly drop")
    p.add_argument("--seconds", type=int, default=30, help="call duration for mic input")
    p.add_argument("--jitter-depth", type=int, default=3)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m hpqv.demo")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="generate a shared identity file for both peers")
    p_keygen.add_argument("--out", required=True)
    p_keygen.set_defaults(func=keygen)

    p_listen = sub.add_parser("listen", help="wait for an incoming call")
    p_listen.add_argument("--port", type=int, required=True)
    _add_common_call_args(p_listen)
    p_listen.set_defaults(func=listen)

    p_call = sub.add_parser("call", help="place a call to HOST:PORT")
    p_call.add_argument("target", metavar="HOST:PORT")
    _add_common_call_args(p_call)
    p_call.set_defaults(func=call)

    args = parser.parse_args(argv)

    if getattr(args, "command", None) in ("listen", "call"):
        if args.input == "file" and not args.wav:
            parser.error("--wav is required when --input file")
        if args.output == "file" and not args.out_wav:
            parser.error("--out-wav is required when --output file")

    args.func(args)


if __name__ == "__main__":
    main()
