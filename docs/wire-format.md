# Wire Format

## Handshake (over TCP, length-prefixed records)
Each record: 4-byte big-endian length ‖ payload.
- Msg 1 (Initiator→Responder) HELLO: version(1B) ‖ kem_pub(1184B) ‖ sig_pub(1952B) ‖ sig_over(kem_pub‖sig_pub)(3309B)
- Msg 2 (Responder→Initiator) ACCEPT: version(1B) ‖ kem_ciphertext(1088B)
Both derive: HKDF-SHA256(shared_secret, info=b"hpqv v1") → 32B voice key ‖ 4B nonce prefix.

## Voice datagram (over UDP, ≤1200B total, DF set)
header ‖ ciphertext‖tag
- header: version(1B) ‖ type(1B) ‖ seq(8B, big-endian) ‖ flags(1B)  = 11 bytes
- nonce = nonce_prefix(4B) ‖ seq(8B)  → 12B ChaCha20-Poly1305 nonce
- ciphertext = ChaCha20Poly1305(key, nonce, opus_frame, aad=header); includes 16B tag

## Runtime notes

- Python is pinned to 3.11 (`requires-python = ">=3.11,<3.12"`). Later dependencies
  (liboqs-python, opuslib) are not verified against 3.14, the system default.
- macOS + opuslib: `ctypes.util.find_library('opus')` returns `None` under SIP,
  so opuslib cannot locate libopus via the normal ctypes lookup. Any process that
  imports opuslib must run with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` set
  (assumes `brew install opus`).
