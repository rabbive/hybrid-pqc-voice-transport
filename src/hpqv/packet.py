import struct
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

MAX_DATAGRAM = 1200
VERSION, TYPE_VOICE = 1, 1

def _header(seq: int, flags: int) -> bytes:
    return struct.pack(">BBQB", VERSION, TYPE_VOICE, seq, flags)

def seal(key, nonce_prefix, seq, opus_frame, flags=0):
    header = _header(seq, flags)
    nonce = nonce_prefix + struct.pack(">Q", seq)
    ct = ChaCha20Poly1305(key).encrypt(nonce, opus_frame, header)
    dg = header + ct
    if len(dg) > MAX_DATAGRAM:
        raise ValueError(f"datagram {len(dg)} > {MAX_DATAGRAM}")
    return dg

def open_(key, nonce_prefix, datagram):
    header, ct = datagram[:11], datagram[11:]
    _, _, seq, flags = struct.unpack(">BBQB", header)
    nonce = nonce_prefix + struct.pack(">Q", seq)
    frame = ChaCha20Poly1305(key).decrypt(nonce, ct, header)
    return seq, flags, frame
