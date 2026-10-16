"""Live two-peer voice demo: v2 TLS 1.3 control channel + real-time UDP voice, mic/speaker or file I/O."""
import argparse
import logging
import socket
import struct
import sys
import threading
import time
import wave

from hpqv import control, identity, packet
from hpqv.audio import OpusCodec, FRAME_SAMPLES, FRAME_BYTES, FS, CHANNELS, _encoder_set
from hpqv.callctl import CallHandle, POLL
from hpqv.controls import CallControls
from hpqv.impair import DelayLine, LossModel
from hpqv.secure import HandshakeError, SecureChannel
from hpqv.udp import UDP_RECV_BUFFER, is_oversize_error

log = logging.getLogger(__name__)

STATUS_INTERVAL = 1.0
FRAME_MS = 1000 * FRAME_SAMPLES // FS     # 20
CALL_START_TIMEOUT = 10.0                 # the listener waits this long for CALL_START
CONNECT_TIMEOUT = 10.0


class CallSetupError(Exception):
    """The peer authenticated but did not start the call as the protocol requires."""


# Plain words for secure.HandshakeError.reason (the GUI and CLI show these).
FAILURE_TEXT = {
    "timeout": "the other side did not answer in time",
    "peer_closed": "the other side closed the connection during the handshake",
    "bad_peer_cert": "the other side's certificate is not the one you pinned",
    "wrong_group": "the other side would not use the required post-quantum key exchange",
    "alpn_mismatch": "the other side does not speak hpqv protocol v2",
    "protocol_v1_peer": "the other side runs the old v1 protocol; upgrade both peers to v2",
    "tls_error": "the secure connection could not be set up",
    "not_ready": "the other side refused the session (it does not trust your certificate)",
}


def handshake_failure_text(reason: str) -> str:
    return FAILURE_TEXT.get(reason, f"handshake failed ({reason})")


def create_identity(name, key_path, cert_path) -> str:
    """Write a new identity (key 0600, certificate) and return its fingerprint.

    Refuses to overwrite either file. Only the certificate is meant to be shared.
    """
    ident = identity.new(name)
    identity.save(ident, key_path, cert_path)
    return identity.fingerprint(ident.cert_pem)


def keygen(args):
    try:
        fp = create_identity(args.name, args.key_out, args.cert_out)
    except FileExistsError as e:
        sys.exit(f"refusing to overwrite existing file: {e}")
    print(f"wrote private key: {args.key_out}  (keep it secret; never share it)")
    print(f"wrote certificate: {args.cert_out}  (give this to the other peer)")
    print(f"fingerprint: {fp}")
    print("read the fingerprint to the other peer over a channel you trust and compare")


def _load_identity(key_path, cert_path, peer_cert_path):
    """(Identity, peer certificate PEM). Raises identity.LegacyIdentityError for a v1 file."""
    me, peer = identity.load(key_path, cert_path), identity.load_peer_cert(peer_cert_path)
    if identity.raw_public_key(peer) == identity.raw_public_key(me.cert_pem):
        raise ValueError(
            f"{peer_cert_path} is your own certificate. Each computer needs its own identity: "
            "run keygen on each one and exchange only the .crt files.")
    return me, peer


def _identity_or_exit(args):
    try:
        return _load_identity(args.key, args.cert, args.peer_cert)
    except (ValueError, OSError) as e:        # incl. LegacyIdentityError: its text is the advice
        sys.exit(str(e))


class FrameSource:
    """Yields 20ms PCM frames, either from a mic or a WAV file.

    File mode plays once, or restarts at EOF with `loop` (so a short WAV can
    carry a call that runs until hang-up).
    """

    def __init__(self, mode, wav_path=None, seconds=30, loop=False):
        self.mode = mode
        self.seconds = seconds
        self.loop = loop
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
                    if not self.loop:
                        return
                    self._wav.rewind()
                    pcm = self._wav.readframes(FRAME_SAMPLES)
                    if len(pcm) < FRAME_BYTES:      # under one frame: looping would spin
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

    With `controls`, speaker mode keeps at most jitter_depth + 2 frames queued
    for the device and skips (and counts in stats.skipped) the rest. Otherwise a
    PLC burst or a lowered delay leaves the call permanently that far behind,
    because the device only ever plays at real time.
    """

    def __init__(self, mode, wav_path=None, record_path=None,
                 controls=None, stats=None, clock=time.monotonic):
        self.mode = mode
        self._controls, self._stats, self._clock = controls, stats, clock
        self._queued = 0.0           # frames handed to the speaker, not yet played
        self._last_t = None
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
        if self.mode == "speaker" and self._controls is not None and self._over_backlog():
            if self._stats is not None:
                with self._stats.lock:
                    self._stats.skipped += 1
            return           # skipped before the tee: the recording is what was heard
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

    def _over_backlog(self):
        """Wall-clock model of the device queue, which drains 50 frames/s.

        ponytail: assumes the sound card clock matches time.monotonic; a ~50 ppm
        drift is about 1 frame per 1000 s. Upgrade to stream.write_available if
        a device ever drifts noticeably.
        """
        now = self._clock()
        if self._last_t is not None:
            drained = (now - self._last_t) * FS / FRAME_SAMPLES
            self._queued = max(0.0, self._queued - drained)
        self._last_t = now
        if self._queued + 1 > self._controls.get("jitter_depth") + 2 + 1e-6:
            return True
        self._queued += 1
        return False

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
        self.skipped = 0             # decoded frames not played to bound speaker latency
        # Receiver's view of the PEER's stream (RxMeter). The sender advances seq
        # on frames it drops, so the seq span is what the peer's encoder produced.
        self.rx_expected = 0
        self.rx_jitter_ms = 0.0      # RFC 3550 interarrival jitter estimate
        # Transit = arrival time - seq * 20 ms, relative to the first packet.
        # The clocks are not synced, so only differences between packets mean anything.
        self.rx_transit_sum_ms = 0.0
        self.rx_transit_n = 0
        self.rx_transit_min_ms = 0.0
        self.max_datagram = 0        # largest UDP payload sent, bytes (cap: 1200)
        self.key_gen = 0             # live key-rotation generation
        self.handshake_ms = 0.0
        self.handshake_bytes = 0
        # Negotiated session facts, set once by establish_timed. peer_fingerprint
        # and sas are for the screen only: telemetry never exports them.
        self.group = ""
        self.cipher = ""
        self.peer_fingerprint = ""
        self.sas = ""
        # Datagrams dropped before the AEAD accepted them, per reason; mirrored
        # from Keyring.rejects by the receiver loop. None of them count as received.
        self.rejects = dict.fromkeys(packet.REJECT_REASONS, 0)
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


class RxMeter:
    """Receiver-side measurement of the peer's stream, fed one authenticated
    datagram at a time, in arrival order."""

    def __init__(self, stats):
        self._stats = stats
        self._top = None             # highest seq seen
        self._low = None             # lowest seq seen: jitter can reorder the first ones
        self._t0 = None              # first packet's transit: the zero point
        self._last = None            # previous packet's relative transit

    def arrive(self, seq, now):
        transit = now * 1000.0 - seq * FRAME_MS
        if self._t0 is None:
            self._t0 = transit
        transit -= self._t0
        s = self._stats
        with s.lock:
            if self._top is None:
                s.rx_expected += 1
                self._top = self._low = seq
            elif seq > self._top:
                s.rx_expected += seq - self._top
                self._top = seq
            elif seq < self._low:
                s.rx_expected += self._low - seq
                self._low = seq
            if self._last is not None:
                s.rx_jitter_ms += (abs(transit - self._last) - s.rx_jitter_ms) / 16
            self._last = transit
            s.rx_transit_sum_ms += transit
            s.rx_transit_n += 1
            s.rx_transit_min_ms = min(s.rx_transit_min_ms, transit)


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


def _sender_loop(udp_sock, dest, ring, source, controls, stats, stop_event, seed=None):
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
            if seq > packet.MAX_SEQ:         # 2**48 frames: end the call, never wrap
                log.warning("sequence space exhausted, ending call")
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
            dg = ring.seal(seq, opus, extra_flags=packet.FLAG_FEC if fec else 0)
            delay_line.send(dg, controls.get("delay_ms"), controls.get("jitter_ms"))
            seq += 1
    finally:
        try:
            delay_line.close(drain=True)
        finally:
            stop_event.set()


def _receiver_loop(udp_sock, ring, sink, controls, stats, stop_event, seed=None):
    from hpqv.jitter import JitterBuffer
    from hpqv.playout import MAX_GAP
    from hpqv.playout import Playout
    codec = OpusCodec()
    jb = JitterBuffer(depth=controls.get("jitter_depth"))
    playout = Playout(codec)
    loss = LossModel(seed)
    meter = RxMeter(stats)
    udp_sock.settimeout(0.5)

    def play(seq, item):
        opus, fec_hint = item
        with stats.lock:
            if stats.first_played_seq is None:
                stats.first_played_seq = seq
            gap = 0 if playout.next_seq is None else seq - playout.next_seq
            if 0 < gap <= MAX_GAP:
                stats.repaired_sequences.extend(range(playout.next_seq, seq))
        r0, c0 = playout.recovered, playout.concealed
        frames = playout.frames(seq, opus, fec_hint)
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
            dg, _addr = udp_sock.recvfrom(UDP_RECV_BUFFER)
        except socket.timeout:
            if stop_event.is_set():
                break
            continue
        except ConnectionResetError:
            # Windows WSAECONNRESET comes from an ICMP port-unreachable, which anyone can
            # spoof. It proves nothing about the peer; a dead peer ends the call through
            # the authenticated TCP channel instead.
            continue
        except OSError as e:
            if is_oversize_error(e):
                continue             # one oversize datagram dropped by the OS; socket is fine
            stop_event.set()         # the socket itself is gone (closed, EBADF): end the call
            break
        try:
            seq, flags, opus = ring.open_(dg)
        except packet.Reject:
            # Counted by the keyring; it must not touch received, the meter or the buffer.
            with stats.lock:
                stats.rejects = ring.rejects
            continue
        with stats.lock:
            stats.received += 1
        # Before the simulated drop: those datagrams did cross the network, and
        # counting them keeps 100 % incoming loss visible as 100 %, not 0/0.
        meter.arrive(seq, time.monotonic())
        if loss.drop(controls.get("incoming_loss_pct")):
            with stats.lock:
                stats.incoming_dropped += 1
            continue
        jb.depth = controls.get("jitter_depth")
        if playout.next_seq is not None and seq < playout.next_seq:
            with stats.lock:
                stats.late += 1
            continue
        jb.push(seq, (opus, bool(flags & packet.FLAG_FEC)))
        # Pop until back under depth: after the depth is lowered mid-call a single
        # pop per push would leave the old backlog (and its latency) in place.
        while (popped := jb.pop()) is not None:
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


def _run_call(channel, udp_sock, dest, args,
              stats=None, controls=None, stop_event=None, call_handle=None,
              rng_seed=None, quiet=False):
    """Run a call. The GUI passes in its own stats/controls/stop_event so it can
    watch the counters and turn the knobs while the call is live, and a
    call_handle to rotate keys or hang up. Either side ending ends both."""
    source = FrameSource(args.input, wav_path=args.wav, seconds=args.seconds,
                         loop=args.loop)
    stats = stats if stats is not None else Stats()
    controls = controls if controls is not None else CallControls(
        loss_pct=args.drop_pct, jitter_depth=args.jitter_depth)
    sink = FrameSink(args.output, wav_path=args.out_wav, record_path=args.record_wav,
                     controls=controls, stats=stats)
    stop_event = stop_event if stop_event is not None else threading.Event()
    call_handle = call_handle if call_handle is not None else CallHandle()
    ring = call_handle.start(channel, stats, stop_event)

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


def establish_timed(role, sock, ident, peer_cert_pem, stats) -> SecureChannel:
    """Run the TLS handshake and record its cost and negotiated facts in stats
    (shared by CLI and GUI). `role` is "client" (caller) or "server" (listener).
    Raises secure.HandshakeError."""
    channel = SecureChannel.establish(sock, role, ident, peer_cert_pem)
    with stats.lock:
        stats.handshake_ms = channel.handshake_ms
        stats.handshake_bytes = channel.handshake_bytes
        stats.group, stats.cipher = channel.group, channel.cipher
        stats.peer_fingerprint, stats.sas = channel.peer_fingerprint, channel.sas()
    return channel


def handshake_info() -> dict:
    """Static facts about the handshake for the GUI's handshake panel."""
    return {"protocol": "TLSv1.3", "group": "X25519MLKEM768", "sig": "ML-DSA-65",
            "transport": "TCP",   # too big for one UDP datagram, so it never touches UDP
            "udp_fragments": 0, "datagram_cap": packet.MAX_DATAGRAM}


def wait_call_start(channel, peer_ip, timeout=CALL_START_TIMEOUT, stop_event=None):
    """Listener side: the first control message after READY must be CALL_START.

    Returns the UDP destination (the TCP peer's IP and the port it announced).
    Anything else, or silence for `timeout` seconds, raises CallSetupError;
    a closed connection raises ConnectionError. Polls in short slices so
    `stop_event` (Hang up) ends the wait.
    """
    deadline = time.monotonic() + timeout
    while True:
        if stop_event is not None and stop_event.is_set():
            raise CallSetupError("hung up before the call started")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CallSetupError("the caller did not start the call in time")
        try:
            msg = channel.recv(timeout=min(POLL, remaining))
        except ValueError as e:
            raise CallSetupError("malformed control message before the call started") from e
        if msg is None:
            continue
        msg_type, payload = msg
        if msg_type != control.MSG_CALL_START or len(payload) != 2:
            raise CallSetupError(f"first control message was not a valid CALL_START "
                                 f"(type {msg_type}, {len(payload)} B)")
        (port,) = struct.unpack(">H", payload)
        if port == 0:
            raise CallSetupError("CALL_START announced UDP port 0")
        return peer_ip, port


def _fail(what, exc):
    sys.exit(f"{what}: {exc}")


def listen(args):
    ident, peer_cert = _identity_or_exit(args)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(1)
    print(f"listening on port {args.port}", flush=True)
    conn, addr = srv.accept()
    srv.close()
    print(f"connection from {addr}")

    stats = Stats()
    channel = None
    udp_sock = None
    try:
        try:
            channel = establish_timed("server", conn, ident, peer_cert, stats)
        except HandshakeError as e:
            _fail("handshake failed", handshake_failure_text(e.reason))
        print(f"handshake complete ({stats.handshake_ms:.1f} ms, {stats.handshake_bytes} B, "
              f"{stats.group}); peer fingerprint {stats.peer_fingerprint}; code {stats.sas}")
        # Bound before CALL_START so the caller's first datagrams find a port.
        udp_sock = _open_bound_udp(args.port)
        try:
            peer_udp = wait_call_start(channel, addr[0])
        except (CallSetupError, ConnectionError) as e:
            _fail("call not started", e)
        _run_call(channel, udp_sock, peer_udp, args, stats=stats)
    finally:
        for closer in (channel, udp_sock, conn):
            if closer is not None:
                closer.close()


def call(args):
    host, port_s = args.target.rsplit(":", 1)
    port = int(port_s)
    ident, peer_cert = _identity_or_exit(args)

    sock_tcp = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    print(f"connected to {host}:{port}")

    stats = Stats()
    channel = None
    udp_sock = None
    try:
        try:
            channel = establish_timed("client", sock_tcp, ident, peer_cert, stats)
        except HandshakeError as e:
            _fail("handshake failed", handshake_failure_text(e.reason))
        print(f"handshake complete ({stats.handshake_ms:.1f} ms, {stats.handshake_bytes} B, "
              f"{stats.group}); peer fingerprint {stats.peer_fingerprint}; code {stats.sas}")
        udp_sock = _open_bound_udp(0)
        channel.send(control.MSG_CALL_START, struct.pack(">H", udp_sock.getsockname()[1]))
        _run_call(channel, udp_sock, (host, port), args, stats=stats)
    finally:
        for closer in (channel, udp_sock, sock_tcp):
            if closer is not None:
                closer.close()


def _open_bound_udp(port):
    from hpqv.udp import open_udp
    return open_udp(("0.0.0.0", port))


def _add_common_call_args(p, identity_required=False):
    p.add_argument("--key", required=identity_required, help="your private key from `keygen`")
    p.add_argument("--cert", required=identity_required, help="your certificate from `keygen`")
    p.add_argument("--peer-cert", required=identity_required,
                   help="the OTHER peer's certificate file (never a private key)")
    p.add_argument("--input", choices=["mic", "file"], default="mic")
    p.add_argument("--wav", help="input WAV path, required when --input file")
    p.add_argument("--loop", action="store_true",
                   help="restart the input WAV at its end, so the call runs until hang-up")
    p.add_argument("--output", choices=["speaker", "file"], default="speaker")
    p.add_argument("--out-wav", help="output WAV path, required when --output file")
    p.add_argument("--drop-pct", type=float, default=0, help="%% of outgoing packets to randomly drop")
    p.add_argument("--seconds", type=int, default=30, help="call duration for mic input")
    p.add_argument("--jitter-depth", type=int, default=3)
    p.add_argument("--record-wav", help="also record received audio to this WAV")


def call_args(**overrides):
    """Call args for code that starts a call without the CLI (GUI, frozen self-check, tests).

    Built from the CLI parser's own defaults, so a new flag reaches every caller.
    """
    p = argparse.ArgumentParser()
    _add_common_call_args(p)
    args = p.parse_args([])
    unknown = set(overrides) - set(vars(args))
    if unknown:
        raise TypeError(f"unknown call args: {sorted(unknown)}")
    vars(args).update(overrides)
    return args


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m hpqv.demo")
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="generate this peer's own key and certificate")
    p_keygen.add_argument("--name", required=True, help="name written into the certificate")
    p_keygen.add_argument("--key-out", required=True, help="private key file (created 0600)")
    p_keygen.add_argument("--cert-out", required=True, help="certificate file to give the peer")
    p_keygen.set_defaults(func=keygen)

    p_listen = sub.add_parser("listen", help="wait for an incoming call")
    p_listen.add_argument("--port", type=int, required=True)
    _add_common_call_args(p_listen, identity_required=True)
    p_listen.set_defaults(func=listen)

    p_call = sub.add_parser("call", help="place a call to HOST:PORT")
    p_call.add_argument("target", metavar="HOST:PORT")
    _add_common_call_args(p_call, identity_required=True)
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
