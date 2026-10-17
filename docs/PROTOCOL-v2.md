# hpqv protocol v2

Status: **Phase B implemented (B1 security foundation and B2 no-files pairing).
Experimental. Not externally reviewed. GATE 1 review pending.**
Branch: `feat/secure-session-v2`. v1 (`experiment/cain`, release
v0.1.0-demo.1, every file under `results/`) is unchanged. v2 does not
interoperate with v1 and never falls back to it.

The binding module contract is `docs/superpowers/plans/2026-10-16-secure-session-v2.md`.
The reasons for using TLS 1.3 instead of a hand-written handshake are in
`docs/adr/0005-protocol-v2-tls13.md`. The v2 byte layouts are summarised in
`docs/wire-format.md`.

## 1. Why v2

These v1 findings are fixed only by a protocol change. Code references are to
v1 on `experiment/cain`.

| # | v1 finding | Effect |
|---|---|---|
| F1 | `keygen` writes **both** peers' ML-DSA secret keys into one shared file | Either peer can impersonate the other. Authentication means "holds the file", not "is that peer" |
| F2 | Post-handshake TCP control records are plaintext and unauthenticated | An on-path attacker can inject CALL_END (teardown), KEY_ROTATE (silently kills audio), or a rewritten CALL_START port (redirects media) |
| F3 | No key confirmation; the HELLO signature covers only `kem_pub‖sig_pub` | A recorded HELLO can be replayed; the responder believes it is talking to the initiator |
| F4 | Thin transcript: the version byte is never checked, the responder signs only `kem_pub‖ct`, no randoms, no responder identity | Nothing binds version, both identities or session parameters |
| F5 | One key for both directions, split by nonce prefix; HKDF with no salt and no transcript | No independent directional keys and no key separation by purpose |
| F6 | PQ-only KEM, no classical component | Not a hybrid key exchange |
| F7 | No UDP replay window; auth failures dropped silently with no counter | Replays inside a key generation are accepted by the AEAD and skew the stats |
| F8 | `recv_record` trusts a 32-bit length; `recvfrom(2048)` truncates silently | 4 bytes from an unauthenticated peer cause a 200 MB allocation (measured; up to 4 GiB); an oversize datagram is not detected |

Replacing liboqs with `cryptography` fixes none of these by itself. They are
protocol defects.

## 2. Building blocks

| Purpose | Algorithm / standard | Library and version |
|---|---|---|
| Control channel | TLS 1.3 (RFC 9846), `OP_NO_TICKET`, no resumption | pyOpenSSL 26.4 on OpenSSL 4.0.2 bundled in `cryptography` 50.x |
| Key exchange | X25519MLKEM768, codepoint 0x11EC (RFC 10024; design in RFC 9954) | OpenSSL 4.0.2 (via `cryptography` 50.x) |
| Peer authentication | ML-DSA-65 (FIPS 204), TLS SignatureScheme mldsa65 = 0x0905 (draft-ietf-tls-mldsa-06), empty FIPS 204 context | OpenSSL 4.0.2; keys and certificates built with `cryptography` |
| Identity | Self-signed X.509 certificate over an ML-DSA-65 key, pinned by the peer | `cryptography` |
| Media key source | TLS exporter (RFC 8446 section 7.5), DTLS-SRTP pattern (RFC 5764) | pyOpenSSL `export_keying_material` |
| Media key expansion, rotation | HKDF-Expand with SHA-256 | `cryptography` |
| Media AEAD | ChaCha20-Poly1305 | `cryptography` |
| Replay protection | Sliding window (RFC 4303 section 3.4.3, RFC 6479) | `hpqv.replay` (bit window; no cryptography) |

Exact versions are pinned in `uv.lock`. No primitive is implemented in this
repository.

A pure-Python hand-written profile of TLS 1.3 with X-Wing was designed first and
rejected. See ADR-0005.

## 3. Message flow

Client = the caller. Server = the listener. All of it runs over one TCP
connection.

```
Client                                                       Server
TLS 1.3 ClientHello ------------------------------------------>
   supported_groups = X25519MLKEM768
   signature_algorithms = mldsa65
   ALPN = "hpqv/2"
   <---------------- ServerHello, EncryptedExtensions (ALPN),
                     CertificateRequest, Certificate,
                     CertificateVerify, Finished
Certificate, CertificateVerify, Finished ---------------------->
                                           check pin, group, ALPN
   <----------------------------------------- MSG_READY (TLS app data)
[client waits for MSG_READY]
MSG_CALL_START (client UDP port) ------------------------------>
UDP voice both ways, keys from the TLS exporter
```

The TLS handshake is exactly what OpenSSL runs. No handshake message is built
by this code.

**What is offered.** Both offers are restricted on the OpenSSL context, so the
ClientHello lists one group and one signature algorithm, with one key share:

- `supported_groups = [0x11EC]`, with a 1,216 B key share (ML-KEM-768 ek, then X25519).
- `signature_algorithms = [0x0905]`.

This uses pyOpenSSL's private bindings `SSL_CTX_set1_curves_list` and
`SSL_CTX_set1_sigalgs_list`, since there is no public setter. If either call
fails, the context is refused, so OpenSSL's defaults are never used. The
ServerHello key share is 1,120 B (ML-KEM-768 ciphertext, then X25519).

A peer without X25519MLKEM768 fails inside the handshake (TLS alert
`handshake_failure`, reason `tls_error`). Before the 2026-10-16 B1 fix, the
ClientHello also offered 10 other groups and a classical X25519 share, and a
classical peer was refused only after the handshake.

Verified:
- on the wire, by `tests/test_t6_regressions.py::test_t6_clienthello_offers_only_x25519mlkem768_and_mldsa65`;
- against Go's independent TLS stack, by `tests/test_interop_go.py`.

The application then checks, in this order:

1. Negotiated protocol is `TLSv1.3` (TLS 1.3 only, no ticket, no resumption).
2. `Connection.get_group_name()` is `"X25519MLKEM768"`. Anything else closes
   the connection with reason `wrong_group`. This is the backstop if the offer
   restriction were ever lost (`test_post_handshake_group_check_is_the_backstop`).
3. ALPN is `hpqv/2`. Otherwise `alpn_mismatch`.
4. The peer's certificate public key is byte-equal to the pinned key (checked
   in the verify callback). Otherwise `bad_peer_cert`.
5. Before any TLS byte, a pinned key equal to our own identity's key is refused
   (`bad_peer_cert`). Copying one identity onto both machines would bring back
   v1 finding F1.

**Why MSG_READY exists.** In TLS 1.3 the client sends its Finished and
considers the handshake complete before the server has checked the client's
certificate. If the server then rejects the client, the client does not learn
that until it reads. The server therefore sends `MSG_READY` only after it has
accepted the client certificate, and the client does not send CALL_START or
start media until it has received it. A client that gets an alert or a close
instead of `MSG_READY` fails with `peer_closed` or `not_ready`.

**v1 peers.** A v1 peer's first bytes are a 4-byte length followed by version
`0x01`, which is not a TLS record. A v2 **listener** detects it from the first
byte and fails with `protocol_v1_peer`. A v2 **caller** dialling a v1 listener
fails with `timeout`: the v1 listener reads the TLS record header as a length
and waits. There is no fallback in either direction.

**Handshake size.** v1 was 10,844 bytes of handshake payload. A CLI probe of the
v2-style TLS handshake with ML-DSA-65 self-signed certificates (`openssl
s_client`) read 10,318 and wrote 10,361 bytes, about 20.7 KB in total including
TLS framing. In the app, `SecureChannel.handshake_bytes` measured 20,714 B in
both directions together (two-process CLI call on 2026-10-16, before the B1
offer restriction). The B1 change removes the 32 B classical key share and the
extra group and signature IDs. One two-process CLI call to a paired friend on
127.0.0.1 after B1 (2026-10-16, this branch) printed `66.4 ms, 20670 B,
X25519MLKEM768`. That is a single sample on loopback, not a benchmark.

## 4. Identity model

- Each peer generates **its own** ML-DSA-65 key and a self-signed X.509
  certificate (CN = name, default validity 3650 days). Only the certificate is
  shared. The private key never leaves its owner.
- There is no CA. Each peer is started with the other peer's certificate file
  and accepts exactly that public key. Any other key is rejected in the verify
  callback, even if the certificate is otherwise well-formed. Certificate
  expiry and the name are not used for authentication; the pin is.
- **Key file** is an unencrypted PKCS#8 PEM, created with `O_EXCL` and mode
  0600. `save()` refuses to overwrite either file. **Certificate file** is
  public.
- **Fingerprint** = SHA-256 of the raw ML-DSA public key, lowercase hex in 16
  groups of 4 separated by spaces. Users compare it out of band.
- **v1 `identity.json`** holds two secret keys. It is refused with
  `LegacyIdentityError` and the user is told to generate v2 identities. It is
  never converted, because the secret of the other peer would carry over.

### 4a. No-files pairing (B2, experimental)

Instead of exchanging certificate files, two devices on the same Wi-Fi can
pair. The design and its limitations are in
[reviews/2026-10-16-b2-pairing-design.md](reviews/2026-10-16-b2-pairing-design.md);
the decision is [ADR-0006](adr/0006-lan-pairing-tofu-with-code-confirmation.md).

- **Device identity:** each device creates its own identity in its app directory
  (`hpqv.paths`; `HPQV_HOME` overrides it).
- **Separate ALPN, `hpqv-pair/1`.** The TLS settings are the same as for calls
  (TLS 1.3 only, X25519MLKEM768 only, ML-DSA-65 only, no resumption). The
  difference is the pin policy: a pairing channel accepts any ML-DSA-65 key
  **except this device's own**, because the point is to learn a new key. The
  post-handshake check then sets `mode = "pair"`.
  - A pairing channel carries no voice keys: `media_keys()` raises.
  - A call (`hpqv/2`) always needs a pinned key, even on a listener that also
    accepts pairing (`secure._check_negotiated`).
- **The listener's window** (`hpqv.pairing.PairingWindow`, `hpqv.listener`):
  pairing is accepted only while the user has opened a 120 s window, one pairing
  at a time, at most 3 attempts per opening. A "codes differ", a failed
  commitment check or a peer decline closes the window. Stray connections do not
  stop the accept loop; at most `MAX_HANDSHAKES` (4) handshakes run at once and
  the rest are closed.
- **Saving.** The friend is stored (`hpqv.trust`, section 4b) only after the
  local user said yes **and** the peer's `CONFIRM = 1` arrived.
- **Discovery** (`hpqv.discovery`, section 4c) is a UDP broadcast of hints. It is
  never trusted.
- **CLI:** `python -m hpqv.demo pair --allow` on one device and `pair
  <device|HOST:PORT>` on the other; then `listen` / `call <friend|HOST:PORT>`
  with no identity flags. `peers` lists friends and nearby devices (`paired`,
  `new`, `key changed?`); `forget` removes a friend. The file-based flags
  (`--key/--cert/--peer-cert`) still work, pin only and never pair.
- **GUI:** leaving the three identity fields empty uses this device's identity
  and the paired friends. The "Pair a device" box has "Allow new device (2 min)"
  and "Pair with…" (a `HOST:PORT` or device name), and a yes/no dialog shows the
  code. The GUI does not yet show a list of nearby devices; the CLI `peers`
  command does.
- **Experimental:** this is our own application of the Bluetooth/ZRTP
  commit-then-reveal pattern. It is not formally verified or externally
  reviewed.

#### Pairing messages

All messages travel as TLS application data on the `hpqv-pair/1` channel, in the
section 6 frame (`msg_type ‖ length ‖ payload`). R = responder = the listener
(TLS server), I = initiator = the device that pairs (TLS client). `CB` is the RFC
9266 channel binding (exporter label `EXPORTER-Channel-Binding`, empty context,
32 B). `fpX` is the SHA-256 of device X's raw ML-DSA-65 public key, taken from
the certificate TLS authenticated. `nX` is 32 random bytes.

| Step | Message | Type | Payload |
|---|---|---|---|
| 1 | R to I `MSG_READY` | 4 | as in section 3 |
| 2 | R to I `MSG_COMMIT` | 0x12 | `C = SHA-256("hpqv-pair/1 commit\0" ‖ CB ‖ fpR ‖ fpI ‖ nR)`, 32 B |
| 3 | I to R `MSG_NONCE` | 0x13 | `nI`, 32 B, drawn only after `C` arrived |
| 4 | R to I `MSG_REVEAL` | 0x14 | `nR`, 32 B. I checks `C`; a mismatch is `bad_commit` |
| 5 | both `MSG_CONFIRM` | 0x15 | 1 byte: `0x01` = this user pressed "Codes match", `0x00` = no, cancel or timeout |

Each of steps 2-4 has a 10 s timeout (`STEP_TIMEOUT`); the human decision has 60 s
(`DECISION_TIMEOUT`). A message of the wrong type or length is a `protocol`
failure. A broken UI callback counts as "no". On every failure nothing is saved
and both nonces are overwritten (best effort, since Python may have copied them).

#### The code (what the 48-bit plan became)

The comparison code is

```
code = int.from_bytes(SHA-256("hpqv-pair/1 code\0" ‖ CB ‖ fpI ‖ fpR ‖ nI ‖ nR)[0:4], "big") mod 10^6
shown as "ddd ddd"
```

It is **6 decimal digits, about 19.9 bits**, not 48 bits. The final plan
(Task 2) first specified a 48-bit exporter-derived code without a commitment
step, to push an attacker's grinding out of reach. Design revision 3 replaced it
with the commit-then-reveal ordering above, which prevents grinding instead of
pricing it, so a short code is enough. The reason, in one line as the plan asked:
*without a commitment step an active man-in-the-middle can grind its own session
to steer one side's code; the commitment fixes the attacker's values before it
sees the honest nonce.* `pairing.comparison_code` is the reference.

- **What it gives:** an attacker relaying two TLS sessions cannot choose its values
  so that the two codes match. Its chance per attempt is about 1 in 10^6 and a
  window allows 3 attempts, so about 3 in 10^6 per opened window. This is
  arithmetic from the code length and the limits, assuming SHA-256 behaves as a
  random function; it is not a measurement.
- **What it does not give:** it authenticates nobody, keeps nothing secret and
  does not decide trust by itself. That the users really compare is an
  assumption: if both press "Codes match" without comparing, an active attacker
  wins.
- **Separate from the call code.** After a call is established, the GUI and CLI
  also show `sas` (exporter label `EXPORTER-hpqv2 sas`, 4 bytes, 8 hex
  characters; section 5). It is informational: both ends are already pinned, so
  it is not part of the trust decision and is not the pairing code.

### 4b. Trust store

`hpqv.trust.TrustStore` (`trust.json` in the app directory) holds the paired
friends. Only certificates are stored, which are public.

- A friend is a record `(fingerprint, label, cert_pem, paired_at)`. The key is
  the **fingerprint**. The label is the certificate CN, truncated to 32
  printable characters, and is only for display.
- It is written only by a completed pairing. The same fingerprint refreshes its
  record. A **different key under a known name is a separate entry**: the prompt
  shows "key changed" and requires a fresh code check, and the old record is kept
  until the user runs `forget`. Nothing is replaced silently.
- Writes are atomic (temp file in the same directory, `fsync`, `os.replace`) and
  owner-only. An unreadable or inconsistent file raises `TrustStoreError` instead
  of being overwritten, and each record's fingerprint is re-derived from its
  certificate when read.
- Not protected: another program running as the same user can edit the file.

### 4c. Discovery

`hpqv.discovery` announces this device on the LAN so that another device can list
it. It is a phonebook, not a trust signal.

- A UDP broadcast on port 47123 every 2 s, each message at most 512 B of JSON:
  `app`, `v` (2), `name` (at most 32 printable characters), `port` (TCP),
  `fp` (the first 16 hex characters of the identity fingerprint) and `pairing`
  (whether a window is open). Anything malformed, oversized or of the wrong type
  is dropped.
- Entries expire after 6 s; at most 64 are kept and a new one is ignored when
  the table is full; a device ignores its own announcement. The receive buffer
  is `MAX_ANNOUNCE + 1` bytes.
- A failed `sendto` (client-isolated or broadcast-blocked Wi-Fi, firewall) is
  swallowed: the list stays empty and a call by `HOST:PORT` still works.
- Trust is keyed by fingerprint, never by name. Two devices announcing the same
  name appear as separate entries. In the CLI `peers`, a known fingerprint hint
  is marked `paired`; an unknown one under a known name is `key changed?`; an
  unknown one under an unknown name is `new`. The hint is 64 bits of the
  fingerprint and is a display aid only; the pin in TLS decides.
- A spoofed announcement can cause a wrong list entry or a connection attempt to
  the wrong address. It cannot create trust, because pairing needs both users to
  confirm the code and a call needs the pinned key.

## 5. Key derivation

All media secrets come from the TLS exporter (RFC 8446 section 7.5), so they
depend on the whole authenticated transcript, including both certificates, the
key shares and the ALPN. Every exporter call uses an empty context.

| Exporter label | Length | Use |
|---|---|---|
| `EXPORTER-hpqv2 media c2s` | 32 | Media secret, client to server |
| `EXPORTER-hpqv2 media s2c` | 32 | Media secret, server to client |
| `EXPORTER-hpqv2 session id` | 4 | UDP `session_id` |
| `EXPORTER-hpqv2 sas` | 4 | Short authentication string (8 hex characters) shown in the GUI |

**Direction mapping.** On the client, `send_secret` = c2s and `recv_secret` =
s2c. On the server it is the reverse. `session_id` is the same on both sides.

**Per-direction AEAD keys** (`packet.direction_keys(secret)`), HKDF-Expand with
SHA-256 and no salt, since the exporter output is already a uniform 32-byte
key:

- `key` (32 bytes) = HKDF-Expand(secret, info = `b"hpqv2 media key"`)
- `iv` (12 bytes) = HKDF-Expand(secret, info = `b"hpqv2 media iv"`)

**Rotation** (`packet.next_secret(secret)`, ADR-0004 semantics):
`secret' = HKDF-Expand(secret, info = b"hpqv2 media upd", 32)`. A rotation
advances both directions of the local Keyring (`send_secret` and `recv_secret`).
Because the direction labels differ, the two directions never share a key.

Each (direction, generation) has its own key. The 48-bit `seq` keeps counting
across rotations, so a (key, nonce) pair is never reused.

## 6. Control channel

Control messages travel as TLS application data. The TLS record layer gives
confidentiality, integrity, ordering, replay and truncation protection. A
forged CALL_END (v1 F2) is no longer possible.

```
frame = msg_type (1) ‖ length (uint16, BE) ‖ payload
```

- `length <= MAX_CONTROL_PAYLOAD` (1024). A larger value raises `ValueError`
  before any read of the payload is sized, so a peer cannot cause a large
  allocation (v1 F8).
- Message types: `MSG_CALL_START = 1` (client UDP port, u16), `MSG_CALL_END =
  2`, `MSG_KEY_ROTATE = 3` (generation, u32), `MSG_READY = 4`.
- A malformed frame or an unknown type is a protocol error and ends the call.
- `recv()` returns `None` on timeout and raises `ConnectionError` when the peer
  closes. A TCP close still ends the call, but the UI reports "connection
  lost", not "peer hung up". An attacker who can reset TCP can always cause a
  denial of service; that is out of scope.
- One lock serialises all SSL calls on a connection, so one reader thread and
  several sender threads are safe.

## 7. UDP voice datagram

All integers are big-endian. The whole 12-byte header is the AEAD AAD.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | `ver_type` | High nibble = version 2, low nibble = type 1 (voice). Value `0x21` |
| 1 | 1 | `flags` | Bits 0-1 key generation mod 4 (ADR-0004). Bit 2 encoder FEC hint (`FLAG_FEC = 0x04`). Bits 3-7 reserved, must be 0 |
| 2 | 4 | `session_id` | From the exporter |
| 6 | 6 | `seq` | uint48, per direction, starts at 0, never reused |
| 12 | n | ciphertext, then 16-byte Poly1305 tag | ChaCha20-Poly1305 |

`nonce = iv XOR (0^6 ‖ seq.to_bytes(6, "big"))`. The key and iv come from
section 5 for the generation named in `flags`.

Limits: `MAX_DATAGRAM = 1200`; `MIN_DATAGRAM = 12 + 16 + 1 = 29`. `seal()` raises
`ValueError` if `seq > 2^48 - 1` or the result exceeds 1200 bytes. A sender that
reaches `2^48 - 1` stops; it never wraps. At 50 packets per second that is about
178,000 years.

### 7.1 Receiver check order

Cheapest checks first. **No state changes before the AEAD succeeds**, except
the per-reason reject counter.

1. `recvfrom(1201)`. Length above 1200: `oversize`. Length below 29:
   `truncated`.
2. `ver_type == 0x21` and reserved flag bits zero, else `malformed`.
3. `session_id` equals this session's, else `wrong_session`. This is an early
   reject for stray packets, not the security boundary.
4. The generation tag selects one retained key, else `no_key`.
5. Replay window **check** (pure, no update): too old or already seen is
   `replay`.
6. AEAD open. Failure is `auth`.
7. Only now `ReplayWindow.accept(seq)` updates the window, and only now do
   counters, receive meters and the jitter buffer see the packet.

There is one try per datagram. Reject counts are exposed as
`Keyring.rejects`, one counter per reason in `packet.REJECT_REASONS`.

### 7.2 Replay window size

The window is 256 packets (`ReplayWindow(size=256)`). The impairment controls
allow up to 1 s of jitter, which is about 50 frames at 20 ms. ADR-0004 keeps a
retired generation's key for 4 s, which is 200 frames, so a delayed packet
under an old key is still inside the window. 256 covers 200 with margin. One
window per Keyring is enough, because `seq` is unique per sender across all
generations. Whether 256 ever rejects legitimate traffic under every impairment
setting is not proven; see section 11.

## 8. Binding UDP to the session

- Media keys and `session_id` exist only after the TLS handshake and the READY
  exchange.
- The server starts UDP only after it has accepted the client certificate and
  received an authenticated `MSG_CALL_START`. It sends to the TCP peer's IP and
  the UDP port in that message.
- The client sends to the server's IP and TCP port number, as in v1.
- The source address of an incoming datagram is **not** trusted. Acceptance
  depends on the session id, the key and the replay window, which allows NAT
  rebinding. An attacker who knows the session id (it is in clear) can make the
  receiver run at most one AEAD operation per datagram. That bounded cost is
  not yet measured.

## 9. Failure handling

Every failure in `SecureChannel.establish()` raises
`HandshakeError(reason)` with one of the fixed codes `timeout`, `peer_closed`,
`bad_peer_cert`, `wrong_group`, `alpn_mismatch`, `protocol_v1_peer`,
`tls_error`, `not_ready`. On failure:

- the TLS connection and the socket are closed and nothing else is sent;
- the reason code is logged, never keys, exporter output, certificates' private
  parts or payloads;
- there is no retry with other parameters, no fallback to v1, to a
  classical-only group, or to a different ALPN.

The whole handshake has a deadline (`HANDSHAKE_TIMEOUT`, 10 s) so a stalled peer
cannot hold the listener open.

## 10. Classical-only baseline (benchmark only)

A classical-only key exchange (for example X25519) will exist only as a
benchmark comparison, in an isolated module under `hpqv/eval/`, labelled "BENCHMARK
ONLY, NOT SECURE AGAINST QUANTUM ADVERSARIES". Production code (`secure`, `demo`,
`gui`) never imports it and rejects any group other than X25519MLKEM768. A test
will assert both. **Not built yet; this is Phase D.**

## 11. Threat model

**Adversary.** Dolev-Yao on both channels: it can read, drop, inject, replay,
reorder and delay any TCP segment or UDP datagram, and open connections to the
listener. It may store traffic now and attack it later with a quantum computer
("harvest now, decrypt later"). It may break X25519 *or* ML-KEM-768, but not
both.

**Assets.** Voice content, integrity of control actions (hang-up, rotation,
port), peer identities, session keys.

| Goal | Mechanism |
|---|---|
| Mutual authentication of the pinned peers | ML-DSA-65 CertificateVerify over the TLS transcript in both directions, plus pin check; MSG_READY confirms the server accepted the client |
| Key secrecy if either X25519 or ML-KEM-768 holds | X25519MLKEM768 hybrid (RFC 10024). Its security argument relies on the TLS 1.3 transcript; RFC 10024 warns that a similar hybridization cannot be assumed secure in other protocols, which is why keys here come only from the TLS key schedule and exporter |
| Forward secrecy against later theft of long-term keys | Ephemeral key shares per session; the long-term key only signs |
| Key confirmation both ways | TLS Finished messages |
| Downgrade resistance | TLS 1.3 only, one required group, one required ALPN, no resumption; any mismatch closes the connection |
| Control integrity, replay and order protection | TLS record layer (section 6) |
| Media confidentiality, integrity, replay protection, session binding | Section 7 and 8 |
| A new friend's key is learned only if both users confirm a code (pairing, section 4a) | Separate ALPN with no voice keys; commit-then-reveal code; save only after both `CONFIRM = 1`; window, attempt and handshake limits. **Experimental, our construction, not formally verified** |
| Bounded resources under unauthenticated input | Control length cap before allocation, datagram size checks first, one AEAD per datagram, handshake deadline |

**Out of scope.**

- **Compromised endpoints**, including malware and a stolen unencrypted private
  key file.
- **Side channels** in Python or the OS. Constant-time behaviour is only as good
  as OpenSSL and `cryptography`.
- **Traffic analysis.** Opus VBR packet sizes leak speech activity and possibly
  phrases (Wright et al., IEEE S&P 2008); timing leaks talk-spurts. A CBR option
  is noted for Phase C and is not a full mitigation.
- **Denial of service** by dropping, resetting or flooding.
- **Pin distribution.** Assumed correct (A1).
- **Memory zeroisation.** Python `bytes` are immutable, so key material cannot
  be reliably erased (A5).

## 12. Implemented, assumed, unverified

### Implemented and tested

Source of every count: one full run on this branch on macOS, 2026-10-16, with
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src
.venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_gui_window.py`.
Last line: `544 passed, 15 skipped, 1 warning in 112.13s (0:01:52)`. Counts below are
the test cases per file in that run's JUnit XML (all passed unless stated).
The 15 skips are the Linux `tc`/`tshark` and ns-3 tests in `tests/eval/`, which run
only in the evaluation container. The 9 Tk window tests in
`tests/test_gui_window.py` are excluded from this command (568 tests collected
in all) and were **not** run for this table.

| Item | Test file and count |
|---|---|
| `hpqv.identity` (ML-DSA-65 self-signed identity, fingerprint, v1 refusal) | `tests/test_identity.py` 11; CLI identity handling `tests/test_demo_identity.py` 24 |
| `hpqv.secure` (TLS 1.3 channel, group/ALPN/pin enforcement, READY, exporter keys, SAS) | `tests/test_secure.py` 47; T6 review regressions `tests/test_t6_regressions.py` 36; independent TLS stack (Go 1.27 `crypto/tls`) `tests/test_interop_go.py` 4 |
| `hpqv.packet` (v2 datagram, `direction_keys`, `next_secret`, reject reasons) | `tests/test_packet.py` 14 |
| `hpqv.replay` (256-packet window, check/accept split) | `tests/test_replay.py` 9 |
| `hpqv.keyring` (rotation, retention, check order, reject counters) | `tests/test_keyring.py` 27 |
| Integration of v2 into call, GUI and telemetry (call control, control channel, UDP, loss control, playout, telemetry, GUI logic and shell) | `tests/test_call.py` 3, `test_callctl.py` 18, `test_control.py` 3, `test_udp.py` 1, `test_loss_control.py` 6, `test_playout.py` 24, `test_telemetry.py` 17, `test_gui_logic.py` 34, `test_gui_shell.py` 29, `test_demo_loop.py` 7 |
| Pairing, trust store, listener (`hpqv.pairing`, `hpqv.trust`, `hpqv.listener`) | `tests/test_pairing.py` 31 |
| Discovery (`hpqv.discovery`) | `tests/test_discovery.py` 6 |
| Pairing and a pinned call through the real CLI, one process per device | `tests/test_pair_cli.py` 4 |
| `cryptography` ML-KEM-768 and ML-DSA-65 against NIST ACVP vectors (added in commit 92412b4) | `tests/test_pqc_kat.py` 101 |
| Docs agree with committed v1 results | `tests/test_docs_current.py` 1 |
| Spike on this machine (not a repo test): X25519MLKEM768 negotiated, mutual ML-DSA-65 auth, exporter equal on both sides; wrong pin, classical-only peer and missing client certificate all refused | Observed 2026-10-16 with pyOpenSSL 26.4 and OpenSSL 4.0.2 |

**Live two-process pairing and call on 127.0.0.1** (2026-10-16, two CLI
processes with separate `HPQV_HOME`, answers `y` piped on stdin, so no person
compared the codes in this run): both printed the code `786 403`; each listed the
other's fingerprint as a friend; a call then connected with the code `15944f99`,
group X25519MLKEM768, and ended with `sent=150 received=150 dropped=0`. The
transcript is in the GATE 1 report, not committed.

**Known test instability on this machine.** In five full
runs on this branch, `tests/test_pairing.py::test_listener_pairs_through_window_and_survives_stray_connections`
failed in three (`ConnectionResetError` from `connect()` during its burst of 100
stray connections; the listener's backlog is 8). A fourth run had one failure that
was not identified, and the fifth, the run quoted above, had none. Run alone, it failed
2 times out of 2. In one of the runs `tests/eval/test_full_handshake.py` also failed
on a timing bound (`median_ms < 45`, observed 45.37).

*Update (fix 83f7efe):* the `ConnectionResetError` was the kernel resetting
connections that overflowed the listener's 8-entry accept backlog. Run alone, the
test failed 10/10 with backlog 8 and 0/10 with 128. The listener now uses
`socket.SOMAXCONN`, and the test's stray connects tolerate a reset. Under extreme
machine load (load average about 90, several agents running) the real pairing
connect could still be reset in about 1 run in 10. The timing-bound flake in
`test_full_handshake.py` is load-related and unchanged.

### Assumptions

- **A1.** Certificates are exchanged over an authentic out-of-band channel and
  users compare fingerprints, or, for paired friends, the two users really
  compare the 6-digit pairing code (section 4a).
- **A2.** OpenSSL 4.0.2 and `cryptography` implement TLS 1.3, ML-KEM-768,
  ML-DSA-65, X25519, ChaCha20-Poly1305 and HKDF correctly, without exploitable
  side channels. Known-answer tests show correctness on the tested inputs only.
- **A3.** The OpenSSL TLS 1.3 implementation, RFC 10024 and draft-ietf-tls-mldsa-06
  mean what they say. The ML-DSA draft is not yet an RFC and its details may
  change.
- **A4.** The exporter output is independent of other TLS secrets for different
  labels (RFC 8446 section 7.5), and the label strings above are unused by any
  other protocol.
- **A5.** Keys left in Python memory after a call cannot be erased reliably.

### Unverified or not yet done

- That the v2 implementation is correct and secure. It has not been reviewed
  outside the project (wave 3 internal security review is pending).
- Every v2 size, latency and CPU figure measured in the app. The handshake size
  figures are the CLI probe and the single loopback call in section 3.
- That the 256-packet window never rejects legitimate traffic under every
  impairment setting.
- Behaviour on platforms other than macOS (Linux, Windows) with the bundled
  OpenSSL 4.0.2.
- The classical-only benchmark module and its isolation test (Phase D).
- A formal or symbolic model. None exists, including for the pairing exchange
  (section 4a).
- Pairing on two real devices over real Wi-Fi. Only 127.0.0.1 has been run.
- That a person who is shown the code actually compares it. The design assumes
  they do (limitation 1 in the pairing design).
- Discovery on a real LAN: the tests cover loopback and a simulated blocked
  broadcast only.
- The GUI pairing window on screen: `tests/test_gui_window.py` is not part of the
  command above.
- A GUI list of nearby devices. The GUI takes a `HOST:PORT`; only the CLI
  resolves names through discovery.
