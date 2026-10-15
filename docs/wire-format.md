# Wire Format

## Handshake (over TCP, length-prefixed records)
KEM: ML-KEM-768
Sig: ML-DSA-65
Each record: 4-byte big-endian length ‖ payload.
- Msg 1 (Initiator→Responder) HELLO: version(1B) ‖ kem_pub(1184B) ‖ sig_pub(1952B) ‖ sig_over(kem_pub‖sig_pub)(3309B)
- Msg 2 (Responder→Initiator) ACCEPT: version(1B) ‖ kem_ciphertext(1088B) ‖ resp_sig_over(kem_pub‖ct)(3309B)
Mutual auth (ADR-0002): the responder signs kem_pub‖ct with its
Dilithium identity; the initiator verifies resp_sig against the responder's
pinned public key before decapsulating. Both identities are pinned out-of-band.
Both derive: HKDF-SHA256(shared_secret, info=b"hpqv v1") → 32B key ‖ 4B nonce_prefix(initiator→responder) ‖ 4B nonce_prefix(responder→initiator); each direction uses its own prefix so the two streams never share a (key,nonce).

## Control messages (over TCP, post-handshake, length-prefixed records)
Each control record = msg_type(1B) ‖ payload, wrapped in the same 4-byte
length-prefixed framing as the handshake.
- MSG_CALL_START = 1 — payload: caller's UDP port (u16, big-endian).
- MSG_CALL_END   = 2 — empty payload. Sent by whichever side hangs up; the
  receiver ends its call without replying. A closed TCP connection also ends
  the call.
- MSG_KEY_ROTATE = 3 — payload: new key generation (u32, big-endian).
Key rotation (session.rotate): new_key ‖ prefixes = HKDF-SHA256(current key,
info=b"hpqv rotate v1"). Both peers derive the same new key; each keeps its
send/recv direction (prefixes ordered by a stable comparison so the two peers
stay mirrored). Generation 0 is the handshake key.

Rotation rules (ADR-0004, `hpqv.keyring` / `hpqv.callctl`):
- The side that rotates switches its send key at once, then sends
  KEY_ROTATE(gen + 1).
- A receiver applies KEY_ROTATE(g) only when g == its generation + 1. Anything
  else (old, skipped-ahead, wrong length, unknown type) is ignored. Two
  simultaneous presses each send KEY_ROTATE(1); each side has already reached
  generation 1, so both land on generation 1 with the same key.
- Sequence numbers keep counting across a rotation; they do not reset. Each
  generation has a fresh key and fresh prefixes, so prefix ‖ seq under the new
  key never repeats a (key, nonce) pair, and the jitter buffer keeps one
  monotonic sequence space.
- Each retired generation's key stays available for 4 s. This is derived from
  the largest delay and jitter values in `controls._RANGES`, plus 1 s for a
  control round-trip. Local rotation waits until every older key has expired;
  a peer's next generation is applied immediately while older keys remain
  available for in-flight datagrams.

## Voice datagram (over UDP, ≤1200B total, enforced by `packet.seal`)
On Linux the socket also sets Don't-Fragment (`IP_PMTUDISC_DO`); the macOS and
Windows builds do not set DF.

header ‖ ciphertext‖tag
- header: version(1B) ‖ type(1B) ‖ seq(8B, big-endian) ‖ flags(1B)  = 11 bytes
- flags bits 0–1 = key generation modulo 4. The receiver decrypts with the one
  retained key named by those bits. Up to three generations can be live when a
  peer rotates again before an older key retires. One AEAD attempt per
  datagram, never a try-all. The header is AAD, so changing the generation tag
  fails authentication. Bit 2 is the FEC hint (below); bits 3–7 are 0.
- nonce = nonce_prefix(4B) ‖ seq(8B)  → 12B ChaCha20-Poly1305 nonce
- ciphertext = ChaCha20Poly1305(key, nonce, opus_frame, aad=header); includes 16B tag

### Flags bit 2: encoder FEC hint (`packet.FLAG_FEC = 0x04`)

Bits 0–1 belong to the keyring;
`Keyring.seal(extra_flags=...)` masks them out of anything else it is given.

- Set when the sender's Opus encoder had in-band FEC on when it coded this frame,
  so the packet *may* carry LBRR data for the previous frame. A hint, not a
  guarantee: CELT-only packets never carry LBRR, and SILK only adds it when it
  judges it worthwhile.
- The receiver asks libopus (`opus_packet_has_lbrr`, libopus ≥ 1.5) when it is
  available and uses this bit only as a fallback (e.g. Ubuntu 24.04's libopus
  1.4), still treating CELT-only TOC configs (16–31) as having no LBRR.
- A gap frame counts as FEC-recovered only when the next packet has (or, in the
  fallback, may have) LBRR; otherwise it is concealed by PLC and counted as such.
- Covered by the AEAD tag like the rest of the header.

## Runtime notes

- Python is pinned to 3.11 (`requires-python = ">=3.11,<3.12"`). Later dependencies
  (liboqs-python, opuslib) are not verified against 3.14, the system default.
- macOS + opuslib: `ctypes.util.find_library('opus')` returns `None` under SIP,
  so opuslib cannot locate libopus via the normal ctypes lookup. Any process that
  imports opuslib must run with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` set
  (assumes `brew install opus`).
