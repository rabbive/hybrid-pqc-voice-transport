"""Live two-peer voice demo: real PQC handshake + real-time UDP voice, mic/speaker or file I/O."""
import argparse
import json
import socket
import struct
import sys
import threading
import time
import wave

from hpqv import control, handshake as h, packet
from hpqv.audio import OpusCodec, FRAME_SAMPLES, FRAME_BYTES, FS, CHANNELS, _encoder_set
from hpqv.callctl import CallHandle
from hpqv.controls import CallControls
from hpqv.impair import DelayLine, LossModel
from hpqv.keyring import Keyring

STATUS_INTERVAL = 1.0


def create_identity(path):
    """Save fresh demo keys, refusing to overwrite an existing identity."""
    a_pub, a_secret = h.make_identity()
    b_pub, b_secret = h.make_identity()
    identity = {
        "listener": {"sig_pub": a_pub.hex(), "sig_secret": a_secret.hex()},
        "caller": {"sig_pub": b_pub.hex(), "sig_secret": b_secret.hex()},
    }
    with open(path, "x", encoding="utf-8") as f:
        json.dump(identity, f)


def keygen(args):
    create_identity(args.out)
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
            # Pace the file at 20 ms/frame so a rehearsal behaves like a real
            # call: packets spread over the wire instead of arriving as one
            # burst, and the live status counter actually ticks. Without this a
            # 3 s WAV finishes in well under a second.
            next_frame = time.monotonic()
            while True:
                pcm = self._wav.readframes(FRAME_SAMPLES)
                if len(pcm) < FRAME_BYTES:
                    return
                next_frame += FRAME_SAMPLES / FS
                delay = next_frame - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
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
    """Consumes decoded PCM frames, either to a speaker or a WAV file.

    `record_path` additionally tees every frame to a WAV, streamed to disk so a
    long call does not sit in memory.
    """

    def __init__(self, mode, wav_path=None, record_path=None):
        self.mode = mode
        self._frames = []
        self._stream = None
        self._closed = False
        self._rec = None
        self._rec_lock = threading.Lock()    # write vs close race on hang-up
        if record_path:
            self._rec = wave.open(record_path, "wb")
            self._rec.setnchannels(CHANNELS)
            self._rec.setsampwidth(2)
            self._rec.setframerate(FS)
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
        if self._closed:
            # Hanging up closes the sink while the receiver thread may still be
            # draining; writing to a torn-down PortAudio stream throws and dumps
            # a traceback over the demo. Dropping late frames is the right call.
            return
        if self._rec is not None:
            with self._rec_lock:
                if self._rec is not None:
                    self._rec.writeframes(pcm)
        if self.mode == "speaker":
            # sounddevice needs a shaped int16 array, not raw bytes: handing it
            # bytes raises "dtype mismatch: 'bytesN' vs 'int16'" and kills
            # playback on the first frame.
            import numpy as np
            try:
                self._stream.write(
                    np.frombuffer(pcm, dtype="<i2").reshape(-1, CHANNELS))
            except Exception:
                pass          # device hiccup or shutdown: never kill the call
        else:
            self._frames.append(pcm)

    def close(self):
        self._closed = True          # stop accepting writes before tearing down
        with self._rec_lock:
            if self._rec is not None:
                self._rec.close()
                self._rec = None
        if self.mode == "speaker":
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
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
        # Frames missing at playout time (lost or arrived too late), rebuilt from
        # FEC / concealed by PLC in the receiver. Not the same as `dropped`.
        self.recovered = 0
        self.concealed = 0
        self.late = 0                # arrived after their slot was played/repaired
        self.incoming_dropped = 0    # dropped on purpose by receiver-side loss
        self.max_datagram = 0        # largest UDP payload sent, bytes (cap: 1200)
        self.key_gen = 0             # live key-rotation generation
        self.handshake_ms = 0.0
        self.handshake_bytes = 0
        # Most recent frame energy each way, for the GUI level meters.
        self.tx_level = 0.0
        self.rx_level = 0.0
        self.first_played_seq = None
        self.repaired_sequences = []

    def snapshot(self):
        with self.lock:
            return self.sent, self.received, self.dropped, self.recovered

    def as_dict(self) -> dict:
        with self.lock:
            return {k: v for k, v in vars(self).items() if k != "lock"}

    def levels(self):
        with self.lock:
            return self.tx_level, self.rx_level


def _rms(pcm: bytes) -> float:
    """Frame energy, 0.0-1.0, cheap enough to run per frame."""
    if not pcm:
        return 0.0
    import array
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    total = sum(float(x) * x for x in samples)
    return min(1.0, (total / len(samples)) ** 0.5 / 32768.0)


def _sender_loop(udp_sock, dest, session, source, controls, stats, stop_event, seed=None):
    ring = Keyring.of(session)
    codec = OpusCodec()
    seq = 0
    loss = LossModel(seed)

    def deliver(dg):
        udp_sock.sendto(dg, dest)
        with stats.lock:
            stats.sent += 1
            stats.max_datagram = max(stats.max_datagram, len(dg))

    delay_line = DelayLine(deliver, seed=None if seed is None else seed + 1)
    last_fec, last_bitrate = True, 0
    try:
        for pcm in source.frames():
            if stop_event.is_set():
                break
            level = _rms(pcm)
            with stats.lock:
                stats.tx_level = level
            fec = controls.get("fec")
            if fec != last_fec:
                _encoder_set(codec.enc, 4012, int(fec))
                _encoder_set(codec.enc, 4014, 10 if fec else 0)
                last_fec = fec
            bitrate = controls.get("bitrate")
            if bitrate != last_bitrate:
                _encoder_set(codec.enc, 4002, bitrate or -1000)
                last_bitrate = bitrate
            opus = codec.encode(pcm)     # encode first: a real loss happens after the encoder
            drop_pct = controls.get("loss_pct")   # re-read every frame: changeable mid-call
            if loss.drop(drop_pct, controls.get("loss_model"), controls.get("burst_len")):
                with stats.lock:
                    stats.dropped += 1
                seq += 1
                continue
            dg = ring.seal(seq, opus)
            delay_ms, jitter_ms = controls.get("delay_ms"), controls.get("jitter_ms")
            delay_line.send(dg, delay_ms, jitter_ms)
            seq += 1
    finally:
        try:
            delay_line.close(drain=True)
        finally:
            stop_event.set()


def _receiver_loop(udp_sock, session, sink, controls, stats, stop_event, seed=None):
    from hpqv.jitter import JitterBuffer
    from hpqv.playout import MAX_GAP
    from hpqv.playout import Playout
    ring = Keyring.of(session)
    codec = OpusCodec()
    jb = JitterBuffer(depth=controls.get("jitter_depth"))
    playout = Playout(codec)
    loss = LossModel(seed)
    udp_sock.settimeout(0.5)

    def play(seq, opus):
        with stats.lock:
            if stats.first_played_seq is None:
                stats.first_played_seq = seq
            gap = 0 if playout.next_seq is None else seq - playout.next_seq
            if 0 < gap <= MAX_GAP:
                stats.repaired_sequences.extend(range(playout.next_seq, seq))
        r0, c0 = playout.recovered, playout.concealed
        frames = playout.frames(seq, opus)
        for pcm in frames:
            sink.write(pcm)
        with stats.lock:
            stats.recovered += playout.recovered - r0
            stats.concealed += playout.concealed - c0
            if frames:               # Playout drops late/duplicate packets
                stats.rx_level = _rms(frames[-1])

    drain_deadline = None
    while True:
        if stop_event.is_set():
            drain_deadline = drain_deadline or time.monotonic() + 0.5
            if time.monotonic() >= drain_deadline:
                break
        try:
            dg, _addr = udp_sock.recvfrom(2048)
        except socket.timeout:
            if stop_event.is_set():
                break
            continue
        except OSError:
            break
        try:
            seq, _flags, opus = ring.open_(dg)
        except Exception:
            continue
        with stats.lock:
            stats.received += 1
        if loss.drop(controls.get("incoming_loss_pct")):
            with stats.lock:
                stats.incoming_dropped += 1
            continue
        jb.depth = controls.get("jitter_depth")
        if playout.next_seq is not None and seq < playout.next_seq:
            with stats.lock:
                stats.late += 1
            continue
        jb.push(seq, opus)
        popped = jb.pop()
        if popped is not None:
            playout.repair = controls.get("repair")
            play(*popped)
    for popped in jb.flush():
        playout.repair = controls.get("repair")
        play(*popped)


def _status_loop(stats, controls, stop_event):
    while not stop_event.is_set():
        sent, received, dropped, recovered = stats.snapshot()
        print(f"\r[status] sent={sent} received={received} dropped={dropped} recovered={recovered} drop_pct={controls.get('loss_pct')}%   ",
              end="", flush=True)
        stop_event.wait(STATUS_INTERVAL)
    print()


def _run_call(sock_tcp, udp_sock, dest, session, args,
              stats=None, controls=None, stop_event=None, call_handle=None,
              rng_seed=None, quiet=False):
    """Run a call. The GUI passes in its own stats/controls/stop_event so it can
    watch the counters and turn the knobs while the call is live, and a
    call_handle to rotate keys or hang up. Either side ending ends both."""
    source = FrameSource(args.input, wav_path=args.wav, seconds=args.seconds)
    sink = FrameSink(args.output, wav_path=args.out_wav, record_path=args.record_wav)
    stats = stats if stats is not None else Stats()
    controls = controls if controls is not None else CallControls(
        loss_pct=args.drop_pct, jitter_depth=args.jitter_depth)
    stop_event = stop_event if stop_event is not None else threading.Event()
    call_handle = call_handle if call_handle is not None else CallHandle()
    ring = call_handle.start(sock_tcp, session, stats, stop_event)

    if controls.get("loss_pct") > 0 and not quiet:
        print(f"loss injection active: dropping {controls.get('loss_pct')}% of outgoing packets")

    status_thread = None if quiet else threading.Thread(
        target=_status_loop, args=(stats, controls, stop_event))
    recv_thread = threading.Thread(
        target=_receiver_loop,
        args=(udp_sock, ring, sink, controls, stats, stop_event, rng_seed))
    if status_thread is not None:
        status_thread.start()
    recv_thread.start()

    try:
        _sender_loop(udp_sock, dest, ring, source, controls, stats, stop_event, rng_seed)
        if args.input == "file":
            # let receiver drain remaining in-flight datagrams
            time.sleep(0.5)
    finally:
        stop_event.set()
        call_handle.finish()
        recv_thread.join(timeout=2)
        if status_thread is not None:
            status_thread.join(timeout=2)
        source.close()
        sink.close()

    sent, received, dropped, recovered = stats.snapshot()
    if not quiet:
        print(f"call ended: sent={sent} received={received} dropped={dropped} recovered={recovered}")


class _CountingSocket:
    """Counts payload bytes through control.send/recv_record and when data first arrived."""

    def __init__(self, sock):
        self._sock = sock
        self.nbytes = 0
        self.first_rx = None

    def sendall(self, data):
        self.nbytes += len(data)
        self._sock.sendall(data)

    def recv(self, n):
        data = self._sock.recv(n)
        if data and self.first_rx is None:
            self.first_rx = time.perf_counter()
        self.nbytes += len(data)
        return data


_RECORD_HEADER = 4       # control.send_record's length prefix; not handshake payload


def timed_handshake(role, sock, sig_pub, sig_secret, peer_sig_pub, stats):
    """Run the handshake and record its cost in stats (shared by CLI and GUI).

    The initiator's clock starts at the call, so HELLO key generation and signing
    count. The responder idles in accept/recv until the caller speaks, so its
    clock starts when the first HELLO byte arrives rather than at connect time.
    """
    run = {"initiator": control.run_initiator, "responder": control.run_responder}[role]
    counted = _CountingSocket(sock)
    t0 = time.perf_counter()
    session = run(counted, sig_pub, sig_secret, peer_sig_pub)
    t1 = time.perf_counter()
    if role == "responder" and counted.first_rx is not None:
        t0 = counted.first_rx
    with stats.lock:
        stats.handshake_ms = (t1 - t0) * 1000.0
        stats.handshake_bytes = counted.nbytes - 2 * _RECORD_HEADER   # HELLO + ACCEPT
    return session


def handshake_info() -> dict:
    """Static facts about the handshake for the GUI's handshake panel."""
    import oqs
    sig_len = oqs.Signature(h.SIG_ALG).details["length_signature"]
    ct_len = oqs.KeyEncapsulation(h.KEM_ALG).details["length_ciphertext"]
    hello = 1 + h.KEM_PUB_LEN + h.SIG_PUB_LEN + sig_len
    accept = 1 + ct_len + sig_len
    return {"kem": h.KEM_ALG, "sig": h.SIG_ALG, "hello_bytes": hello,
            "accept_bytes": accept, "total_bytes": hello + accept,
            "transport": "TCP",   # too big for one UDP datagram, so it never touches UDP
            "udp_fragments": 0, "datagram_cap": packet.MAX_DATAGRAM}


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
    print(f"listening on port {args.port}, fingerprint {sig_pub[:8].hex()}...", flush=True)
    conn, addr = srv.accept()
    print(f"connection from {addr}")

    stats = Stats()
    session = timed_handshake("responder", conn, sig_pub, sig_secret, peer_sig_pub, stats)
    print(f"handshake complete ({stats.handshake_ms:.1f} ms, {stats.handshake_bytes} B)")

    udp_sock = _open_bound_udp(args.port)
    _msg_type, payload = control.recv_control(conn)
    (caller_udp_port,) = struct.unpack(">H", payload)
    peer_udp = (addr[0], caller_udp_port)
    _run_call(conn, udp_sock, peer_udp, session, args, stats=stats)
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

    stats = Stats()
    session = timed_handshake("initiator", sock_tcp, sig_pub, sig_secret, peer_sig_pub, stats)
    print(f"handshake complete ({stats.handshake_ms:.1f} ms, {stats.handshake_bytes} B)")

    udp_sock = _open_bound_udp(0)
    udp_port = udp_sock.getsockname()[1]
    control.send_control(sock_tcp, control.MSG_CALL_START, struct.pack(">H", udp_port))
    peer_udp = (host, port)
    _run_call(sock_tcp, udp_sock, peer_udp, session, args, stats=stats)
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
    p.add_argument("--record-wav", help="also record received audio to this WAV")


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
