"""Citable, seeded loopback measurements through the live call path."""
import argparse
import hashlib
import shutil
import socket
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from hpqv import control, demo, handshake
from hpqv.audio import CHANNELS, FRAME_SAMPLES, FS
from hpqv.callctl import CallHandle
from hpqv.controls import CallControls
from hpqv.demo import Stats
from hpqv.telemetry import Telemetry

RESULTS_DIR = Path("results/live_scenarios")


class _DirectionalStats:
    """Pair one impaired sender with its corresponding receiver counters."""

    def __init__(self, sent, received):
        self.sent, self.received = sent, received

    def as_dict(self):
        tx, rx = self.sent.as_dict(), self.received.as_dict()
        return {**rx, "sent": tx["sent"], "dropped": tx["dropped"]}


def make_speech_wav(path, seconds=10.0):
    """Create deterministic harmonic speech-like PCM for repeatable headless runs."""
    n = int(FS * seconds)
    t = np.arange(n, dtype=np.float64) / FS
    syllable = 0.18 + 0.82 * np.maximum(0, np.sin(2 * np.pi * 4.3 * t)) ** 2
    signal = np.zeros(n, dtype=np.float64)
    for harmonic in range(1, 10):
        signal += np.sin(2 * np.pi * (115 * harmonic * t + 1.7 * np.sin(2 * np.pi * 3.1 * t))) / harmonic
    pcm = np.clip(signal * syllable * 4300, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(FS)
        out.writeframes(pcm.tobytes())
    return Path(path)


def _duration_wav(source, destination, seconds):
    limit = max(1, int(seconds * FS))
    with wave.open(str(source), "rb") as inp:
        if inp.getframerate() != FS or inp.getnchannels() != CHANNELS or inp.getsampwidth() != 2:
            raise ValueError(f"WAV must be {FS}Hz mono 16-bit PCM")
        frames = inp.readframes(limit)
    with wave.open(str(destination), "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(FS)
        out.writeframes(frames)


def _session_pair():
    listener_pub, listener_secret = handshake.make_identity()
    caller_pub, caller_secret = handshake.make_identity()
    accepted = {}
    server = socket.create_server(("127.0.0.1", 0))
    caller_tcp = socket.create_connection(server.getsockname())
    listener_tcp, _ = server.accept()
    server.close()
    thread = threading.Thread(target=lambda: accepted.setdefault(
        "listener", control.run_responder(listener_tcp, listener_pub, listener_secret, caller_pub)))
    thread.start()
    caller_session = control.run_initiator(caller_tcp, caller_pub, caller_secret, listener_pub)
    thread.join()
    return caller_tcp, listener_tcp, caller_session, accepted["listener"]


def _run_loopback(source, received_path, knobs, seed):
    caller_tcp, listener_tcp, caller_session, listener_session = _session_pair()
    udp = {}
    peers = {}
    stop_events = {"caller": threading.Event(), "listener": threading.Event()}
    caller_stats, listener_stats = Stats(), Stats()
    repair = knobs.get("repair", "silence")
    fec = knobs.get("fec", repair == "fec")
    outbound = {
        k: knobs[k] for k in ("loss_pct", "loss_model", "burst_len", "delay_ms",
                               "jitter_ms", "bitrate")
        if k in knobs
    }
    caller_controls = CallControls(**outbound, fec=fec)
    listener_controls = CallControls(
        incoming_loss_pct=knobs.get("incoming_loss_pct", 0),
        repair=repair, jitter_depth=knobs.get("jitter_depth", 3), fec=fec)
    for role in ("caller", "listener"):
        udp[role] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp[role].bind(("127.0.0.1", 0))
    return_path = received_path.with_name(f".{received_path.stem}-return.wav")
    for role, tcp, other, session, stats, controls, seed_offset in (
        ("caller", caller_tcp, "listener", caller_session, caller_stats, caller_controls,
         0),
        ("listener", listener_tcp, "caller", listener_session, listener_stats,
         listener_controls, 10),
    ):
        args = argparse.Namespace(input="file", wav=str(source), seconds=0, output="file",
                                  out_wav=str(received_path if role == "listener" else return_path),
                                  record_wav=None)
        handle = CallHandle()
        thread = threading.Thread(target=demo._run_call, args=(
            tcp, udp[role], udp[other].getsockname(), session, args),
            kwargs={"stats": stats, "controls": controls, "stop_event": stop_events[role],
                    "call_handle": handle, "rng_seed": seed + seed_offset, "quiet": True})
        peers[role] = (handle, thread)
    try:
        for _handle, thread in peers.values():
            thread.start()
        combined = _DirectionalStats(caller_stats, listener_stats)
        telemetry_controls = CallControls(
            delay_ms=knobs.get("delay_ms", 0), jitter_ms=knobs.get("jitter_ms", 0),
            loss_pct=knobs.get("loss_pct", 0), loss_model=knobs.get("loss_model", "random"),
            burst_len=knobs.get("burst_len", 3),
            incoming_loss_pct=knobs.get("incoming_loss_pct", 0),
            repair=repair, fec=fec, jitter_depth=knobs.get("jitter_depth", 3))
        telemetry = Telemetry(combined, telemetry_controls)
        start = time.monotonic()
        next_sample = start + 1
        while any(thread.is_alive() for _handle, thread in peers.values()):
            now = time.monotonic()
            if now >= next_sample:
                telemetry.sample(now - start)
                next_sample += 1
            time.sleep(min(0.02, max(0, next_sample - time.monotonic())))
        elapsed = time.monotonic() - start
        if not telemetry.rows:
            telemetry.sample(elapsed)
        return caller_stats, listener_stats, telemetry
    finally:
        for stop in stop_events.values():
            stop.set()
        for handle, thread in peers.values():
            if thread.ident is not None:
                thread.join(timeout=4)
            handle.finish()
        for sock in (caller_tcp, listener_tcp, *udp.values()):
            sock.close()
        return_path.unlink(missing_ok=True)


def _read_pcm(path):
    with wave.open(str(path), "rb") as f:
        return np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")


def _whole_signal_snr_db(reference, reconstructed):
    ref = reference.astype(np.float64)
    noise = ref - reconstructed.astype(np.float64)
    noise_power = np.sum(noise ** 2)
    if noise_power == 0:
        return float("inf")
    signal_power = np.sum(ref ** 2)
    if signal_power == 0:
        return float("-inf")
    return float(10 * np.log10(signal_power / noise_power))


def _align_frames(reference, reconstructed):
    best = None
    for offset in range(-25, 26):
        ref_start, out_start = max(offset, 0), max(-offset, 0)
        frames = min(len(reference) // FRAME_SAMPLES - ref_start,
                     len(reconstructed) // FRAME_SAMPLES - out_start)
        if frames <= 0:
            continue
        ref = reference[ref_start * FRAME_SAMPLES:(ref_start + frames) * FRAME_SAMPLES]
        out = reconstructed[out_start * FRAME_SAMPLES:(out_start + frames) * FRAME_SAMPLES]
        score = _whole_signal_snr_db(ref, out)
        if best is None or score > best[0]:
            best = score, ref, out, offset, ref_start, out_start
    if best is None:
        empty = reference[:0]
        return empty, reconstructed[:0], 0, 0, 0
    _, ref, out, offset, ref_start, out_start = best
    return ref, out, offset, ref_start, out_start


def _repaired_frame_snr_db(reference, reconstructed, sequences, first_seq, out_start):
    if first_seq is None:
        return None
    repaired = [seq - first_seq - out_start for seq in sequences]
    repaired = [i for i in repaired if 0 <= i < len(reconstructed) // FRAME_SAMPLES]
    if not repaired:
        return None
    indexes = np.concatenate([
        np.arange(i * FRAME_SAMPLES, (i + 1) * FRAME_SAMPLES) for i in repaired
    ])
    return _whole_signal_snr_db(reference[indexes], reconstructed[indexes])


def run_scenario(name: str, knobs: dict, seconds: float, wav_path=None, seed: int = 0) -> dict:
    """Run a real TCP/UDP call and return its measured impairment and quality row."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if wav_path is None:
        wav_path = make_speech_wav(RESULTS_DIR / "speech_like.wav")
    received_path = RESULTS_DIR / f"{name}.wav"
    codec = {"fec": knobs.get("fec", knobs.get("repair") == "fec"),
             "bitrate": knobs.get("bitrate", 0)}
    key = hashlib.sha256(
        Path(wav_path).read_bytes() + f"{seconds}:{codec}".encode()).hexdigest()[:16]
    reference_path = RESULTS_DIR / f"_clean_reference_{key}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source.wav"
        _duration_wav(wav_path, source, seconds)
        sender, receiver, telemetry = _run_loopback(source, received_path, knobs, seed)
        if name == "clean" and not knobs:
            shutil.copyfile(received_path, reference_path)
        elif not reference_path.exists():
            _run_loopback(source, reference_path, codec, seed)
        reference, reconstructed = _read_pcm(reference_path), _read_pcm(received_path)
        aligned_ref, aligned_out, offset, _ref_start, out_start = _align_frames(
            reference, reconstructed)
        whole_snr = _whole_signal_snr_db(aligned_ref, aligned_out)
        repaired_snr = _repaired_frame_snr_db(
            aligned_ref, aligned_out, receiver.repaired_sequences,
            receiver.first_played_seq, out_start)
        mean_mos = float(np.mean([row["mos"] for row in telemetry.rows]))
    offered = sender.sent + sender.dropped
    delivered = max(0, receiver.received - receiver.incoming_dropped)
    lost = max(0, offered - delivered + receiver.late)
    residual = max(0, lost - receiver.recovered)
    return {
        "name": name, "knobs": dict(knobs), "frames_offered": offered,
        "frames_delivered": delivered, "frames_lost": lost, "frames_late": receiver.late,
        "frames_recovered": receiver.recovered, "frames_concealed": receiver.concealed,
        "measured_loss_pct": 100 * max(0, offered - delivered) / offered if offered else 0.0,
        "residual_loss_pct": 100 * residual / offered if offered else 0.0,
        "mean_mos": mean_mos, "whole_signal_snr_db": whole_snr,
        "repaired_frame_snr_db": repaired_snr, "alignment_offset_frames": offset,
        "received_wav": str(received_path),
    }
