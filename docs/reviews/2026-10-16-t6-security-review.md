# T6 security review of feat/secure-session-v2 (v1 -> v2 Phase B)

Reviewer: bb thread thr_abrqmxinkv (Claude Opus 5.5, read-only, 2026-10-16). AI review, not an external human review. Attack scripts were in the reviewer's $TMPDIR (not kept in the repo).

I found no way to break authentication or key secrecy in the v1→v2 diff. Pinning, the PQ group check, ALPN, READY, the media keys and the UDP receive path all held up when I attacked them. There are six real defects, the worst a Windows-only medium: one forged datagram can end a call. The rest are low, plus several doc claims the code doesn't match. Nothing was edited; the attack scripts are in `/private/tmp/claude-501/t6/`. The security-relevant test files pass (271 tests); I did not run `tests/test_gui_window.py`.

## Findings, by severity

**1. Medium: on Windows, one unauthenticated UDP datagram ends the call.** `src/hpqv/demo.py:458` and `:463`
- **Scenario:** v2 shrank the receive buffer from `recvfrom(2048)` (v1) to `recvfrom(1201)`. On Windows, a datagram bigger than the buffer raises an error (WinError 10040) instead of being cut short. The `except OSError:` branch then sets `stop_event`, so the call ends. Anyone who knows the UDP port can do this with one datagram of 1,202 bytes or more; the listener's UDP port is its public TCP port. It never reaches the Keyring, so nothing is counted as rejected. Windows is a shipped target (`desktop-demo.yml`, `windows-2022`).
- **Verified:** `winmsgsize.py` feeds `_receiver_loop` a socket that behaves like Windows, plus one 1,300-byte datagram of zeros:
  `receiver exited: True | call stop_event set: True | rejects: 0`
  On macOS the same datagram is just cut short: `recvfrom(1201) -> 1201 bytes (truncated, no error)`.
- **Fix:** call `recvfrom(65535)` and let `parse_header` reject it as `oversize` (works on every platform). Don't end the call on `ConnectionResetError` either: on Windows a spoofed ICMP message causes it (already true in v1), so also call `sock.ioctl(SIO_UDP_CONNRESET, False)`. `call.recv_stream` has the same `break`.

**2. Low–medium: the same identity on both machines, or pinning your own certificate, is accepted silently.** `secure.py` `_context` / `demo._load_identity`
- **Scenario:** v1 told users to "privately copy the identity file to your other computer". A user who copies `.key` and `.crt` to both machines gets a working v2 call with no warning. That quietly brings back v1 finding F1: either key holder can pose as the other.
- **Verified:** `poc.py` part 1 → `established: X25519MLKEM768 | peer fp == own fp: True`
- **Fix:** in `_load_identity` (or `establish`), refuse when `raw_public_key(peer_cert) == raw_public_key(ident.cert_pem)`.

**3. Low: the PQ group and signature algorithm are checked only after the handshake, and PROTOCOL-v2 §3 says otherwise.** `secure.py:62-90`
- **Scenario:** §3 says the client offers only `supported_groups = X25519MLKEM768` and `signature_algorithms = mldsa65`. In fact the ClientHello offers 11 groups and 21 signature algorithms, and sends a classical X25519 key share. Security still holds: the post-handshake check fails closed, and the transcript is signed, so an attacker can't force a downgrade. But the doc is wrong, and a wrong group is only refused after the handshake has finished.
- **Verified:** `ch.py` →
  `supported_groups: ['X25519MLKEM768', 'SecP256r1MLKEM768', …, 'x25519', 'secp256r1', …, 'ffdhe3072']`, `key_share: [('X25519MLKEM768', 1216), ('x25519', 32)]`
- **Fix (tested, `fixgroups.py`):** in `_context`, call `SSL._lib.SSL_CTX_set1_curves_list(ctx._context, b"X25519MLKEM768")` and `SSL_CTX_set1_sigalgs_list(ctx._context, b"mldsa65")`. The call still works (`X25519MLKEM768 TLS_AES_256_GCM_SHA384`), and the ClientHello becomes `supported_groups: ['X25519MLKEM768']`, `signature_algorithms: ['0x905']`, one key share. Keep the post-handshake check as well.

**4. Low: `media_keys()` can be read twice, and a second Keyring reuses (key, nonce).** `secure.py:270`, `keyring.py:28`
- **Scenario:** the check that seq never repeats lives inside each Keyring. A second `Keyring(channel.media_keys())`, for example a reused `CallHandle.start` on the same channel, starts seq at 0 again under the same key. No production path does this today; it is an API hazard.
- **Verified:** `poc.py` part 2 → `same header: True | c1^c2 == p1^p2: True` (the two ciphertexts XOR to the two plaintexts XORed).
- **Fix:** make `media_keys()` single-use (raise on the second call), or have the channel own its one Keyring.

**5. Low: `identity.load()` accepts a key file that others can read.** `identity.py:66`
- **Verified:** after `chmod 644 a.key`, `identity.load` gives `loaded key with mode 0o644`.
- **Fix:** on POSIX, refuse (or warn) when `st_mode & 0o077` is set, as ssh does.

**6. Low (already in v1, DoS): any connection uses up the one-shot listener.** `demo.py:616-629`, same in the GUI
- **Scenario:** the listener accepts one TCP connection only. One stray connection or port scan ends it after the 10 s handshake deadline.
- **Verified:** `dos.py` → `listener exit=1 after 10.0s … handshake failed: the other side did not answer in time`
- **Fix:** keep accepting after a failed handshake, until a peer authenticates or the user cancels.

## Places where the docs don't match the code

- **§3, groups and signature algorithms:** wrong, see finding 3.
- **§3, v1 peers:** the doc says a v1 peer fails as `protocol_v1_peer` or `tls_error`. That only holds when the listener is v2. A v2 caller dialling a v1 listener gets `timeout`, and the v1 listener reads the TLS header as a 369,295,621-byte length (its F8 bug). Checked with `v1peer.py` → `v2 client reason: timeout`.
- **§7.1, "no state changes before the AEAD":** `Keyring.open_` drops expired generations before the AEAD check. This is harmless (it depends only on time, not on the attacker), but the sentence is wrong. `poc.py` part 3: retained generations go from `[0, 1]` to `[1]` on a forged datagram.
- **§9 and the plan, "every failure raises HandshakeError":** not quite. An `SSL.Error` from `bio_write` or the READY send would escape without the socket being closed. I could not trigger one.
- **§12, implemented/assumed/unverified:** the "Implemented and tested" rows still say "TO BE FILLED AFTER WAVE 2", and the status line still says "Implementation in progress".
- **Tests:** `test_demo_does_not_import_liboqs_or_the_v1_handshake` checks for `hpqv.handshake`, which no longer exists, so it proves nothing. The static grep test in `tests/eval` does cover it, and the runtime check is clean.

## Checked and sound

- **Pinning:** only the pinned key at depth 0 passes, and it is re-checked after the handshake. Chain tricks or the same key in different certificate parameters still need the private key. A missing client certificate is refused.
- **Version, group, ALPN:** TLS 1.3 only; a wrong group or missing ALPN fails closed on both sides.
- **Resumption:** a client offering an earlier session is not resumed (`resume.py`: `client reused: 0` on the second connection).
- **READY:** it travels inside TLS, so it can't be forged, and the client sends neither CALL_START nor UDP before it arrives. The listener takes only a valid CALL_START (non-zero port) as the first message.
- **Slowloris:** the handshake deadline holds against a trickling client (exit at 10.0 s).
- **Exporter keys:** the direction mapping is correct and reflected packets fail. A session_id collision only gets a packet as far as an AEAD failure.
- **Key rotation:** matches ADR-0004. Simultaneous presses converge on one generation, and 2-bit slot reuse only drops the generation four back.
- **Replay window:** correct at 255/256 and on huge jumps; check before the AEAD, update only after it. One AEAD per datagram.
- **Control frames:** the length cap applies before any read is sized; unknown types end the call.
- **Locking:** lock order is free of cycles, as far as I could trace it (stats→ring only).
- **Secrets:** `MediaKeys` and `Identity` hide secrets from repr; logs and telemetry CSV/summary never include keys, the SAS or the fingerprint. The key file is created with `O_EXCL` and mode 0600 and symlinks are refused.
- **Isolation:** production imports load no `v1_handshake` and no classical-only code. A v1 `identity.json` is refused in the CLI and the GUI (both go through `_load_identity`).

## For a human cryptographer

- **Exporter labels:** the `EXPORTER-hpqv2 …` labels are unregistered (RFC 5705 reserves `EXPERIMENTAL` for private use). Check that an empty context and no context are treated the same.
- **Key derivation:** HKDF-Expand is used directly on exporter output, and key and IV come from the same secret.
- **Rotation:** it gives no forward or post-compromise secrecy within a call. Generation 0 stays in `channel._keys`, and the TLS exporter secret stays live in OpenSSL, so every later generation can be derived.
- **SAS:** is 32 bits enough, given it only backs up the pin?
- **ML-DSA in TLS:** the mldsa65 codepoint is still a draft; also needed are OpenSSL 4.0.2's hybrid-group implementation and broader ACVP coverage.
- **No formal model** exists for how TLS, READY, the exporter keys and the UDP layer fit together.

---

## Disposition (coordinator, 2026-10-16)

| T6 item | Disposition |
|---|---|
| 1. Windows WSAEMSGSIZE ends the call | **Fixed**, 61eadd1. 65535 B receive buffer; WSAEMSGSIZE/WSAECONNRESET never end the call; `SIO_UDP_CONNRESET` off. Tested with fakes only (no Windows host) |
| 2. Same identity on both machines accepted | **Fixed**, 61eadd1, in `establish` and in the CLI/GUI loader |
| 3. Classical groups and signature algorithms offered in ClientHello | **Fixed**, 61eadd1. Only `0x11EC` and `0x0905` are offered (verified on the wire and against Go); the post-handshake check is kept |
| 4. `media_keys()` reusable, so (key, nonce) could repeat | **Fixed**, 61eadd1. Single use, behind its own lock |
| 5. World-readable key accepted | **Fixed**, 61eadd1. POSIX owner plus 0o077 check; Windows ACL set on save and checked on load, failing closed. Windows path tested with fakes only |
| 6. A stray connection uses up the one-shot listener | **Open.** Deferred to the B2 pairing listener design |
| Docs §3, groups and signature algorithms | **Fixed** in PROTOCOL-v2 §3 (now true) |
| Docs §3, v2 caller vs v1 listener | **Fixed** in PROTOCOL-v2 §3 |
| Docs §7.1, §9, §12 wording; status line | **Open.** Phase B docs task |
| `SSL.Error` from `bio_write` / READY send can escape `establish` | **Open.** Not reproduced by T6 or here |
| `test_demo_does_not_import_liboqs_or_the_v1_handshake` checks a dead module name | **Open.** The static grep test in `tests/eval` covers it |
| Questions for a human cryptographer | Unchanged. Listed in `docs/reviews/2026-10-16-b1-verification.md` §8 of the B1 report |

Verification record: [2026-10-16-b1-verification.md](2026-10-16-b1-verification.md).
