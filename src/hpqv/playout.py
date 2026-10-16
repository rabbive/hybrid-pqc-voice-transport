"""Turns a stream of received Opus packets into gapless PCM, using FEC on loss.

The encoder embeds a low-bitrate copy of frame N-1 in packet N (in-band FEC),
but that only helps if the decoder is asked for it. Without this, a lost
packet is skipped and the call just gets shorter and choppier.
"""
import ctypes

MAX_GAP = 50          # frames (1 s): a stall is not worth a second of fake audio


def _load_has_lbrr():
    """libopus >= 1.5 exports opus_packet_has_lbrr; older builds (Ubuntu 24.04's
    1.4) do not, and then the sender's FEC flag is the best evidence there is."""
    try:
        import opuslib.api
        fn = opuslib.api.libopus.opus_packet_has_lbrr
    except (ImportError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_int32]
    fn.restype = ctypes.c_int
    return fn


_HAS_LBRR = _load_has_lbrr()


def packet_has_lbrr(opus, fec_hint):
    """Does this packet carry LBRR (FEC) data for the frame before it?

    `fec_hint` is the sender's flag (wire header bit 2: its encoder had FEC on),
    used only when libopus cannot answer. It means "possibly", not "certainly".
    """
    if opus and opus[0] >> 3 >= 16:
        return False         # TOC config 16-31 is CELT-only: no SILK layer, no LBRR
    if _HAS_LBRR is not None:
        found = _HAS_LBRR(opus, len(opus))
        if found >= 0:
            return found == 1
    return bool(fec_hint)


class Playout:
    def __init__(self, codec, repair="fec", has_lbrr=packet_has_lbrr):
        self.codec = codec
        self.repair = repair
        self._has_lbrr = has_lbrr
        self.next_seq = None
        # Frames missing at playout time (lost or arrived too late), rebuilt
        # from FEC / concealed by PLC. Can exceed datagrams actually lost.
        # `recovered` only counts rebuilds from LBRR the next packet carried.
        self.recovered = 0
        self.concealed = 0

    def frames(self, seq, opus, fec_hint=False):
        """PCM frames to play for this packet, in order, filling any gap before it.

        `fec_hint`: the sender flagged its encoder FEC on (see packet_has_lbrr).
        """
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
                if self._has_lbrr(opus, fec_hint):
                    out.append(self.codec.decode(opus, decode_fec=True))
                    self.recovered += 1
                else:        # no LBRR: decode_fec would only run PLC under another name
                    out.append(self.codec.conceal())
                    self.concealed += 1
        if not decoded_current:
            out.append(self.codec.decode(opus))
        self.next_seq = seq + 1
        return out
