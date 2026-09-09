# 0002 - Add responder authentication (mutual auth)

Status: Accepted
Date: 2026-09-09

## Context

The core prototype's handshake is one-way authenticated: the initiator sends a
Dilithium-signed HELLO which the responder verifies and pins, but the
responder's ACCEPT (just the KEM ciphertext) is unsigned. The initiator never
authenticates the responder. An active man-in-the-middle on the TCP control
channel can pass HELLO through untouched, then substitute its own KEM
encapsulation against the initiator's public (signed but not secret) Kyber key —
establishing a shared secret with the initiator while running a separate session
with the real responder.

This matched the original spec (initiator-only signing), but the evaluation
panel and the "post-quantum *secure*" framing of the project make an
authenticated-both-ways handshake worth the small extra cost.

## Decision

Authenticate the ACCEPT message too. The responder signs the handshake transcript
(at minimum `kem_ciphertext` bound to the initiator's `kem_pub`) with its
Dilithium identity key; the initiator verifies it against the responder's pinned
public key (the `peer_sig_pub` parameter already threaded through `run_initiator`
but currently unused). Both identities are pinned out-of-band for the two-peer
prototype.

## Consequences

- Closes the MITM-on-ACCEPT hole; the handshake becomes mutually authenticated.
- The ACCEPT record grows by one Dilithium signature (~3309 B) — still fine over
  TCP, and it reinforces the fragmentation thesis (even more handshake bytes that
  would fragment over naive UDP).
- `wire-format.md` ACCEPT layout changes: `version ‖ kem_ciphertext ‖ sig`. This
  is a breaking change to the handshake, done before the evaluation phase locks
  the protocol.
