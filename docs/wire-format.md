# Wire Format

This file has two parts: **v2** (current, Phase B) first, then **v1** (historical).
The full v2 design is in `docs/PROTOCOL-v2.md`.

## v2

### Control channel (TCP)
TLS 1.3 (X25519MLKEM768, mutual ML-DSA-65 pinned certificates, ALPN `hpqv/2`).
After the handshake, each control frame is TLS application data:
`msg_type(1B) ‖ length(u16, BE) ‖ payload`, with `length <= 1024`.
- MSG_CALL_START = 1 — payload: caller's UDP port (u16, BE).
- MSG_CALL_END = 2 — empty payload.
- MSG_KEY_ROTATE = 3 — payload: new key generation (u32, BE).
- MSG_READY = 4 — empty; the server sends it after accepting the client
  certificate. The client sends nothing else until it has received it.

### Media keys
TLS exporter, empty context: `EXPORTER-hpqv2 media c2s` and `media s2c` (32 B
each), `EXPORTER-hpqv2 session id` (4 B), `EXPORTER-hpqv2 sas` (4 B). The client
sends with c2s and receives with s2c; the server is the reverse.
Per direction: key(32) = HKDF-Expand-SHA256(secret, `hpqv2 media key`),
iv(12) = HKDF-Expand-SHA256(secret, `hpqv2 media iv`). Rotation:
secret' = HKDF-Expand-SHA256(secret, `hpqv2 media upd`, 32).

### Voice datagram (UDP, 29 to 1200 B)
All integers big-endian. The 12-byte header is the AEAD AAD.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | ver_type = 0x21 (version 2 in the high nibble, type 1 = voice) |
| 1 | 1 | flags: bits 0-1 key generation mod 4, bit 2 FEC hint (0x04), bits 3-7 must be 0 |
| 2 | 4 | session_id (exporter) |
| 6 | 6 | seq, uint48, never reused or wrapped |
| 12 | n+16 | ChaCha20-Poly1305 ciphertext and tag |

nonce = iv XOR (0^6 ‖ seq). Receiver order: size, ver_type and reserved bits,
session_id, generation key, replay check (256-packet window), AEAD, then the
replay window update. Rotation rules and the 4 s key retention are as in
ADR-0004. The FEC hint semantics are as described under v1 below.

---

# v1 (historical, experiment/cain and release v0.1.0-demo.1)

The content below describes the v1 protocol. It is not used by v2 and does not
interop with it.

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
