import ctypes
import platform
import wave
import opuslib

FS, CHANNELS, FRAME_SAMPLES = 48000, 1, 960
FRAME_BYTES = FRAME_SAMPLES * 2


def _encoder_set(enc, request, value):
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        ctl = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                               *[ctypes.c_long] * 6, ctypes.c_int)(
            ("opus_encoder_ctl", opuslib.api.libopus))
        rc = ctl(ctypes.cast(enc.encoder_state, ctypes.c_void_p), request,
                 0, 0, 0, 0, 0, 0, value)
    else:
        ctl = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                               ctypes.c_int)(("opus_encoder_ctl", opuslib.api.libopus))
        rc = ctl(ctypes.cast(enc.encoder_state, ctypes.c_void_p), request, value)
    if rc:
        raise RuntimeError(f"opus_encoder_ctl SET {request} failed: {rc}")


def _encoder_get(enc, request) -> int:
    value = ctypes.c_int()
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        ctl = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                               *[ctypes.c_long] * 6, ctypes.POINTER(ctypes.c_int))(
            ("opus_encoder_ctl", opuslib.api.libopus))
        rc = ctl(ctypes.cast(enc.encoder_state, ctypes.c_void_p), request,
                 0, 0, 0, 0, 0, 0, ctypes.byref(value))
    else:
        ctl = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                               ctypes.POINTER(ctypes.c_int))(
            ("opus_encoder_ctl", opuslib.api.libopus))
        rc = ctl(ctypes.cast(enc.encoder_state, ctypes.c_void_p), request,
                 ctypes.byref(value))
    if rc:
        raise RuntimeError(f"opus_encoder_ctl GET {request} failed: {rc}")
    return value.value


class OpusCodec:
    def __init__(self, fec: bool = True):
        self.enc = opuslib.Encoder(FS, CHANNELS, opuslib.APPLICATION_VOIP)
        _encoder_set(self.enc, 4012, int(fec))
        _encoder_set(self.enc, 4014, 10 if fec else 0)
        self.dec = opuslib.Decoder(FS, CHANNELS)

    def encode(self, pcm: bytes) -> bytes:
        return self.enc.encode(pcm, FRAME_SAMPLES)

    def decode(self, data: bytes, decode_fec: bool = False) -> bytes:
        return self.dec.decode(data, FRAME_SAMPLES, decode_fec)

    def conceal(self) -> bytes:
        """Opus packet-loss concealment: decoding no data extrapolates the last frame."""
        return self.dec.decode(b"", FRAME_SAMPLES)

def wav_frames(path: str):
    with wave.open(path, "rb") as w:
        while True:
            pcm = w.readframes(FRAME_SAMPLES)
            if len(pcm) < FRAME_BYTES:
                break
            yield pcm
