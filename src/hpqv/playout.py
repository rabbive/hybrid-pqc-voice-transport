"""Turns a stream of received Opus packets into gapless PCM, using FEC on loss.

The encoder embeds a low-bitrate copy of frame N-1 in packet N (in-band FEC),
but that only helps if the decoder is asked for it. Without this, a lost
packet is skipped and the call just gets shorter and choppier.
"""
MAX_GAP = 50          # frames (1 s): a stall is not worth a second of fake audio


class Playout:
    def __init__(self, codec, repair="fec"):
        self.codec = codec
        self.repair = repair
        self.next_seq = None
        # Frames missing at playout time (lost or arrived too late), rebuilt
        # from FEC / concealed by PLC. Can exceed datagrams actually lost.
        self.recovered = 0
        self.concealed = 0

    def frames(self, seq, opus):
        """PCM frames to play for this packet, in order, filling any gap before it."""
        if self.next_seq is not None and seq < self.next_seq:
            return []                # late or duplicate: already played or rebuilt
        out = []
        gap = 0 if self.next_seq is None else seq - self.next_seq
        decoded_current = False
        if 0 < gap <= MAX_GAP:
            if self.repair == "silence":
                current = self.codec.decode(opus)
                out.extend([bytes(len(current))] * gap)
                self.concealed += gap
                out.append(current)
                decoded_current = True
            elif self.repair == "plc":
                out.extend(self.codec.conceal() for _ in range(gap))
                self.concealed += gap
            else:
                for _ in range(gap - 1):
                    out.append(self.codec.conceal())
                    self.concealed += 1
                out.append(self.codec.decode(opus, decode_fec=True))
                self.recovered += 1
        if not decoded_current:
            out.append(self.codec.decode(opus))
        self.next_seq = seq + 1
        return out
