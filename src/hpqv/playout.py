"""Turns a stream of received Opus packets into gapless PCM, using FEC on loss.

The encoder embeds a low-bitrate copy of frame N-1 in packet N (in-band FEC),
but that only helps if the decoder is asked for it. Without this, a lost
packet is skipped and the call just gets shorter and choppier.
"""
MAX_GAP = 50          # frames (1 s): a stall is not worth a second of fake audio


class Playout:
    def __init__(self, codec):
        self.codec = codec
        self.next_seq = None
        self.recovered = 0
        self.concealed = 0

    def frames(self, seq, opus):
        """PCM frames to play for this packet, in order, filling any gap before it."""
        out = []
        gap = 0 if self.next_seq is None else seq - self.next_seq
        if 0 < gap <= MAX_GAP:
            for _ in range(gap - 1):
                out.append(self.codec.conceal())
                self.concealed += 1
            out.append(self.codec.decode(opus, decode_fec=True))
            self.recovered += 1
        out.append(self.codec.decode(opus))
        self.next_seq = max(seq + 1, self.next_seq or 0)   # late dup must not rewind
        return out
