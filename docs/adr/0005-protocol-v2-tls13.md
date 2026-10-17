# 0005 - Protocol v2 on real TLS 1.3

Status: Proposed
Date: 2026-10-16

## Context

A review of v1 (`docs/PROTOCOL-v2.md` section 1, findings F1-F8) found defects
that only a protocol change can fix: a shared identity file holding both secret
keys, an unauthenticated control channel, no key confirmation, a thin handshake
transcript, one key for both directions, no classical component, no UDP replay
window, and unbounded length fields.

The first v2 design was a hand-written profile of the TLS 1.3 handshake. It used
X-Wing (draft-connolly-cfrg-xwing-kem-11; the CFRG's
draft-irtf-cfrg-concrete-hybrid-kems-04 MLKEM768-X25519 is identical) as the key
share and ML-DSA-65 in CertificateVerify. The X-Wing implementation on top of
`cryptography` passed the 3 draft-11 vectors and 10 CFRG vectors. The design
needed ten documented deviations from RFC 8446 (own encoding, no handshake
encryption, own label prefix and others), each needing expert sign-off, because
the security analyses of TLS 1.3 cover its exact encoding and state machine, not
a re-encoding of it.

That premise changed. Python can now run real TLS 1.3 with post-quantum groups
and signatures: pyOpenSSL 26.4 on the OpenSSL 4.0.2 bundled in `cryptography` 50.
A spike on 2026-10-16 showed:

- X25519MLKEM768 negotiated (`Connection.get_group_name()`);
- mutual authentication with self-signed ML-DSA-65 certificates;
- the TLS exporter returned equal values on both sides;
- a wrong pin, a classical-only peer, and a missing client certificate were all
  refused.

Relevant standards: RFC 9846 (TLS 1.3 revision), RFC 10024 (X25519MLKEM768 for
TLS 1.3; its security section says the hybridization relies on the TLS 1.3
message transcript and cannot be assumed secure in other protocols), RFC 9954
(hybrid key exchange design), draft-ietf-tls-mldsa-06 (mldsa65 = 0x0905, empty
FIPS 204 context), RFC 8446 section 7.5 (exporters) and RFC 5764 (DTLS-SRTP
pattern for media keys).

## Decision

- **Control channel:** TLS 1.3 through pyOpenSSL. TLS 1.3 only, no session
  tickets (`OP_NO_TICKET`), no resumption. No handshake code of ours.
- **Key exchange:** X25519MLKEM768 is required. After the handshake the group
  must equal `"X25519MLKEM768"` or the connection is closed. No fallback.
- **Authentication:** mutual, with a self-signed ML-DSA-65 certificate per peer.
  Each peer pins the other's exact public key in the verify callback. No CA.
- **ALPN:** `hpqv/2` required on both sides, so the application protocol
  version is part of the TLS transcript. A listener whose user has opened a
  pairing window also accepts `hpqv-pair/1`, which never carries a call or voice
  keys (ADR-0006).
- **Ready message:** the server sends `MSG_READY` after accepting the client
  certificate; the client waits for it before sending anything else, because a
  TLS 1.3 client finishes before the server checks it.
- **Control messages** are framed inside TLS application data (type, uint16
  length capped at 1024, payload).
- **UDP media keys** come from the TLS exporter, one secret per direction
  (labels `EXPORTER-hpqv2 media c2s` / `s2c`), plus `session id` and `sas`
  labels. Datagram: 12-byte header, ChaCha20-Poly1305, nonce = iv XOR seq,
  256-packet replay window updated only after the AEAD verifies.
- **Library:** liboqs is removed. The v1 handshake code needed for historical
  experiments moves to `hpqv/eval/v1_handshake.py`, ported to `cryptography`.

## Consequences

- No custom handshake, key schedule or Finished logic. The security argument is
  that of TLS 1.3 with RFC 10024 hybrid groups, as implemented by OpenSSL, not
  that of a profile we invented. Our own code is limited to identity handling,
  pin checking, group/ALPN enforcement, framing, and the UDP layer.
- Handshake traffic is larger. v1 was 10,844 B of payload. A CLI probe of the
  v2-style handshake with ML-DSA-65 certificates (`openssl s_client`) read
  10,318 and wrote 10,361 bytes, about 20.7 KB including TLS framing. In the app, one two-process CLI call on
  127.0.0.1 after the B1 fixes reported 20,670 B and 66.4 ms (2026-10-16, a single
  sample, not a benchmark; `docs/PROTOCOL-v2.md` section 3).
- We depend on the OpenSSL bundled in `cryptography` 50 (4.0.2) and on pyOpenSSL
  26.4, not on the OS OpenSSL. Wheels must carry it on every platform we ship.
  Linux and Windows behaviour is untested.
- draft-ietf-tls-mldsa-06 is a draft. The 0x0905 codepoint or the certificate
  encoding could change in a later revision.
- The TLS handshake encrypts certificates, so identity privacy against passive
  observers is better than in the hand-written design.
- The new Python surface (verify callbacks, exporter use, locking around one
  `Connection` shared by a reader and writers) is where bugs can still occur
  and needs the wave 3 review.
- v1 and v2 do not interoperate. A v1 peer fails at the first byte and is
  reported as such. There is no downgrade path.
- Memory zeroisation is still not possible in Python.
- Pinning needs the peer's key to reach the other device. The file swap is the
  baseline; no-files pairing on the same LAN is decided separately in
  [ADR-0006](0006-lan-pairing-tofu-with-code-confirmation.md). This ADR stays
  **Proposed**.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Python stdlib `ssl` | It links the system OpenSSL, 3.0.x on the target machines, which has no ML-KEM or hybrid groups and no ML-DSA, and `SSLContext` cannot select groups |
| tlslite-ng | Pure-Python TLS with post-quantum groups, but its ML-KEM and ML-DSA are not constant time, which breaks the rule of using only vetted primitives |
| Hand-written TLS-1.3-like profile with X-Wing | Designed and its KEM implementation passed the draft-11 and CFRG vectors, but rejected: it re-implements handshake, transcript and key schedule with an own encoding, so the TLS analyses do not directly apply, and real TLS 1.3 with the same properties is now available. Rejected per the review condition not to introduce a new construction when a standard one is available. X-Wing is not used |
| Noise / PQNoise | KEM-based authentication replaces signatures and the ML-DSA identity model, and there is no maintained PQ implementation to reuse |
| Keep v1 and patch it | The findings (shared identity file, plaintext control channel, no transcript) are structural |
