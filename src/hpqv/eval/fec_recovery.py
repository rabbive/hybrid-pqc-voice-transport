"""Does Opus in-band FEC actually recover a lost audio frame?

LBRR embeds a low-bitrate copy of frame N inside packet N+1. Recovery is a
DECODER action: when packet N is lost, decode packet N+1 with decode_fec=True
to reconstruct frame N, then decode packet N+1 normally for frame N+1. If
N+1 is also lost, there is nothing to recover from and we fall back to a
zero (silent) concealment frame, same as the no-FEC baseline.

We compare two receivers against the same lossy trace:
  - FEC-off: every lost packet -> zero frame.
  - FEC-on:  every lost packet -> recovered from decode_fec on the next
             packet if that packet arrived, else zero frame.

Quality metric: segmental SNR (per-frame SNR in dB, averaged) against the
original PCM, since a single whole-signal SNR is dominated by loud frames.
"""
import random

import numpy as np

from hpqv.audio import CHANNELS, FRAME_SAMPLES, FS, OpusCodec

FRAME_MS = 20


def _test_signal(seconds: float) -> np.ndarray:
    """Deterministic chirp, 16-bit PCM samples as float64, length a multiple of FRAME_SAMPLES."""
    n_frames = int(seconds * 1000 / FRAME_MS)
    n_samples = n_frames * FRAME_SAMPLES
    t = np.arange(n_samples) / FS
    # Chirp 200Hz -> 2000Hz so the signal isn't a single trivial tone.
    freq = 200 + (2000 - 200) * (t / t[-1])
    signal = 0.5 * np.sin(2 * np.pi * freq * t)
    return (signal * 32767).astype(np.int16)


def _frames(pcm: np.ndarray):
    for i in range(0, len(pcm), FRAME_SAMPLES):
        yield pcm[i:i + FRAME_SAMPLES]


def _segmental_snr_db(original: np.ndarray, reconstructed: np.ndarray) -> float:
    """Average per-frame SNR in dB across all frames."""
    snrs = []
    for i in range(0, len(original), FRAME_SAMPLES):
        orig = original[i:i + FRAME_SAMPLES].astype(np.float64)
        recon = reconstructed[i:i + FRAME_SAMPLES].astype(np.float64)
        noise = orig - recon
        signal_power = np.sum(orig ** 2)
        noise_power = np.sum(noise ** 2)
        if noise_power == 0:
            snrs.append(100.0)  # perfect reconstruction, cap to keep the average finite
        elif signal_power == 0:
            continue
        else:
            snrs.append(10 * np.log10(signal_power / noise_power))
    return float(np.mean(snrs))


def measure(loss_pct: float, seed: int = 0, seconds: float = 3.0) -> dict:
    pcm = _test_signal(seconds)
    frames = [f.tobytes() for f in _frames(pcm)]
    n_frames = len(frames)

    rng = random.Random(seed)
    lost = [rng.random() * 100 < loss_pct for _ in range(n_frames)]

    enc_fec = OpusCodec(fec=True)
    enc_off = OpusCodec(fec=False)
    packets_fec = [enc_fec.encode(f) for f in frames]
    packets_off = [enc_off.encode(f) for f in frames]

    zero_frame = np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()

    # FEC-on receiver.
    dec_on = OpusCodec(fec=True)
    out_on = []
    recovered = 0
    for i in range(n_frames):
        if not lost[i]:
            out_on.append(dec_on.decode(packets_fec[i]))
            continue
        if i + 1 < n_frames and not lost[i + 1]:
            out_on.append(dec_on.decode(packets_fec[i + 1], decode_fec=True))
            recovered += 1
        else:
            out_on.append(zero_frame)

    # FEC-off receiver: every lost packet is silence, no recovery attempted.
    dec_off = OpusCodec(fec=False)
    out_off = []
    for i in range(n_frames):
        out_off.append(zero_frame if lost[i] else dec_off.decode(packets_off[i]))

    recon_on = np.frombuffer(b"".join(out_on), dtype=np.int16)
    recon_off = np.frombuffer(b"".join(out_off), dtype=np.int16)

    return {
        "loss_pct": loss_pct,
        "snr_fec_on_db": _segmental_snr_db(pcm, recon_on),
        "snr_fec_off_db": _segmental_snr_db(pcm, recon_off),
        "frames_lost": sum(lost),
        "frames_recovered_by_fec": recovered,
    }
