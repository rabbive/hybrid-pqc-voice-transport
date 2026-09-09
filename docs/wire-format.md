# Wire Format

## Handshake (over TCP, length-prefixed records)
KEM: ML-KEM-768
Sig: ML-DSA-65
Each record: 4-byte big-endian length ‖ payload.
- Msg 1 (Initiator→Responder) HELLO: version(1B) ‖ kem_pub(1184B) ‖ sig_pub(1952B) ‖ sig_over(kem_pub‖sig_pub)(3309B)
- Msg 2 (Responder→Initiator) ACCEPT: version(1B) ‖ kem_ciphertext(1088B) ‖ resp_sig_over(kem_pub‖ct)(3309B)
Mutual auth (ADR-0002): the responder signs the transcript kem_pub‖ct with its
Dilithium identity; the initiator verifies resp_sig against the responder's
pinned public key before decapsulating. Both identities are pinned out-of-band.
Both derive: HKDF-SHA256(shared_secret, info=b"hpqv v1") → 32B key ‖ 4B nonce_prefix(initiator→responder) ‖ 4B nonce_prefix(responder→initiator); each direction uses its own prefix so the two streams never share a (key,nonce).

## Control messages (over TCP, post-handshake, length-prefixed records)
Each control record = msg_type(1B) ‖ payload, wrapped in the same 4-byte
length-prefixed framing as the handshake.
- MSG_CALL_START = 1 — begin a voice session.
- MSG_CALL_END   = 2 — tear down.
- MSG_KEY_ROTATE = 3 — both peers ratchet the session key.
Key rotation (session.rotate): new_key ‖ prefixes = HKDF-SHA256(current key,
info=b"hpqv rotate v1"). Both peers derive the same new key; each keeps its
send/recv direction (prefixes ordered by a stable comparison so the two peers
stay mirrored). After a rotate, per-direction sequence counters reset to 0.

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
