import wave
import opuslib

FS, CHANNELS, FRAME_SAMPLES = 48000, 1, 960
FRAME_BYTES = FRAME_SAMPLES * 2

class OpusCodec:
    def __init__(self, fec: bool = True):
        self.enc = opuslib.Encoder(FS, CHANNELS, opuslib.APPLICATION_VOIP)
        if fec:
            # opuslib 3.0.1's inband_fec property setter drops the value arg (lib bug);
            # call the CTL directly instead.
            opuslib.api.encoder.encoder_ctl(
                self.enc.encoder_state, opuslib.api.ctl.set_inband_fec, 1)
            self.enc.packet_loss_perc = 10
        else:
            self.enc.packet_loss_perc = 0
        self.dec = opuslib.Decoder(FS, CHANNELS)

    def encode(self, pcm: bytes) -> bytes:
        return self.enc.encode(pcm, FRAME_SAMPLES)

    def decode(self, data: bytes, decode_fec: bool = False) -> bytes:
        return self.dec.decode(data, FRAME_SAMPLES, decode_fec)

def wav_frames(path: str):
    with wave.open(path, "rb") as w:
        while True:
            pcm = w.readframes(FRAME_SAMPLES)
            if len(pcm) < FRAME_BYTES:
                break
            yield pcm
