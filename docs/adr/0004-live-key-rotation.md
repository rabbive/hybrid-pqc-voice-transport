# 0004 - Live key rotation over the control channel

Status: Accepted
Date: 2026-10-15

## Context

The live demo needs a "rotate keys" button that works mid-call, plus a hang-up
that ends both peers. `session.rotate` already derives the next key and
direction prefixes from the current key, and `MSG_KEY_ROTATE` / `MSG_CALL_END`
were reserved, but nothing used them during a call. UDP voice and the TCP
control channel are not ordered against each other, so voice datagrams under
the old and new keys interleave at the receiver around every rotation. The
delay/jitter controls allow up to 3 s of one-way delay and jitter, plus a
control round-trip margin.

## Decision

- **A four-value generation tag in the header.** Flags bits 0-1 = generation
  modulo 4. The receiver retains each generation until its retirement time and
  decrypts with exactly the key named by those bits. This lets a peer's next
  rotation coexist with still-live datagrams from two generations earlier,
  without a try-all loop or a wrong-key attempt. The header is AAD, so changing
  the tag fails authentication. Local rotations wait until all older keys have
  retired; an in-sequence peer rotation is applied immediately and older live
  keys remain available.
- **Sequence numbers continue across a rotation.** Uniqueness of
  (key, nonce) comes from the new key, not from resetting seq. Resetting would
  make the jitter buffer and playout treat the new stream as late duplicates.
- **Accept only generation + 1.** `KEY_ROTATE(g)` moves the ring only when
  g == current + 1. The rotating side switches its send key and announces under
  one lock, so its announcements leave in order. If both users press at once,
  each side reaches generation 1 locally and ignores the crossing
  `KEY_ROTATE(1)`, so the pair rotates once. In general a peer that sends
  ROTATE(k) has already sent or received ROTATE(k−1) over the same ordered
  TCP stream, so the receiver is at k−1 or later and the two sides converge on
  the higher generation.
- **Retire each old key after a 4 s window.** `RETIRE_AFTER` is derived from
  the maximum `delay_ms` and `jitter_ms` in `controls._RANGES`, plus 1 s for a
  control round-trip. Retiring on the first current-generation datagram would
  drop older packets reordered by the delay line. After the window, replayed
  old-generation datagrams fail. A local user cannot rotate again until every
  old key has expired; an in-sequence peer rotation remains accepted and can
  leave up to three distinct live generations for the two-bit tag.
- **Call end.** Whoever hangs up sends `CALL_END` and stops. The peer's
  control-reader thread sets the call's stop event when it receives
  `CALL_END` or sees the TCP connection close. `_run_call` always runs a
  `CallHandle`, so CLI calls get the same behaviour as the GUI.

## Consequences

- In-flight datagrams survive a rotation. The only loss is datagrams the new
  sender emits before the peer has processed `KEY_ROTATE` over TCP. On a LAN
  that is a fraction of a frame, and playout repair conceals it.
- The control channel is plain TCP, not authenticated after the handshake. An
  on-path attacker who injects an in-sequence `KEY_ROTATE` can desynchronise
  the keys (denial of service). It learns nothing, because each new key is
  derived from the secret current key. An attacker in that position can
  already drop the connection, so this adds no new capability. Authenticating
  control records is future work.
- The GUI fingerprint is the first 8 hex digits of SHA-256(current key). It is
  for two people to compare aloud after a rotation, not a secret.
- Wire format: flags bits 0-1 carry the generation modulo 4; seq no longer resets on rotation
  (the earlier wire-format note said it did, but nothing implemented it).
