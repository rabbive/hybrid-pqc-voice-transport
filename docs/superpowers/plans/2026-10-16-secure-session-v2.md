# Secure session v2: Phase B plan and module contract

Branch: `feat/secure-session-v2` (from `experiment/cain`). Status: approved in chat
2026-10-16. Phase B (the security foundation) only. The work stops for the user's
review before Phase C (audio adaptation) and Phase D (GUI diagnostics).

## Decision summary

- **TCP control channel:** real TLS 1.3 (RFC 9846) through **pyOpenSSL 26.4**
  on `cryptography`'s bundled OpenSSL 4.0.2. No custom handshake code.
- **Key exchange:** X25519MLKEM768 (RFC 10024, codepoint 0x11EC). It is
  required: after the handshake, `Connection.get_group_name()` must equal
  `"X25519MLKEM768"`, or the connection closes (fail closed, no fallback).
- **Peer authentication:** mutual, with self-signed ML-DSA-65 X.509 certificates
  (draft-ietf-tls-mldsa-06, mldsa65 = 0x0905). Each peer pins the other's exact
  public key. There is no CA, and any other key is rejected in the verify
  callback.
- **ALPN:** `b"hpqv/2"` is required on both sides. This binds the application
  protocol version into the TLS transcript; a mismatch closes the connection.
- **Resumption:** no session tickets or resumption (`OP_NO_TICKET`), TLS 1.3 only.
- **Control messages** travel as TLS application data, so injection, replay,
  reordering and truncation are handled by the TLS record layer.
- **UDP media keys** come from the TLS exporter (RFC 8446 section 7.5, the
  DTLS-SRTP pattern), with one independent secret per direction.
- **v2 voice datagram:** a 12-byte header (version and type, flags,
  session_id[4], seq uint48), ChaCha20-Poly1305 with nonce = iv XOR seq,
  a 256-packet replay window, and strict size checks.
- **Library:** liboqs is removed in wave 2. The v1 experiment code that still
  needs the v1 message format is ported to `cryptography` ML-KEM/ML-DSA in
  `hpqv/eval/v1_handshake.py` and labelled historical.

## Module contract (all agents code against exactly this)

### `src/hpqv/identity.py` (wave 1, T1)

```python
class LegacyIdentityError(ValueError): ...   # v1 identity.json (two secret keys) refused

@dataclass(frozen=True)
class Identity:
    key_pem: bytes      # PKCS#8 PEM, unencrypted, ML-DSA-65 (never logged)
    cert_pem: bytes     # self-signed ML-DSA-65 X.509, CN = name, public

def new(name: str, days: int = 3650) -> Identity            # in memory
def save(ident: Identity, key_path: str, cert_path: str) -> None
    # key file created with O_EXCL and mode 0o600; refuses to overwrite either file
def load(key_path: str, cert_path: str) -> Identity        # checks the key matches the cert and is ML-DSA-65
def load_peer_cert(path: str) -> bytes                     # PEM; ML-DSA-65 required;
    # raises LegacyIdentityError if the file is a v1 identity.json
def fingerprint(cert_pem: bytes) -> str                    # SHA-256 of the raw ML-DSA public key,
    # lowercase hex in groups of 4 separated by spaces (16 groups)
```

### `src/hpqv/secure.py` (wave 1, T1)

```python
GROUP = "X25519MLKEM768"; ALPN = b"hpqv/2"; PROTOCOL = "TLSv1.3"
HANDSHAKE_TIMEOUT = 10.0
MAX_CONTROL_PAYLOAD = 1024

class HandshakeError(Exception):
    reason: str   # fixed codes: "timeout", "peer_closed", "bad_peer_cert", "wrong_group",
                  # "alpn_mismatch", "protocol_v1_peer", "tls_error", "not_ready"

@dataclass(frozen=True)
class MediaKeys:
    send_secret: bytes   # 32 B, exporter, this side -> peer
    recv_secret: bytes   # 32 B, exporter, peer -> this side
    session_id: bytes    # 4 B, same on both sides

class SecureChannel:
    role: str                   # "client" (caller) | "server" (listener)
    group: str; cipher: str; protocol: str
    peer_fingerprint: str
    handshake_ms: float         # wall time of the TLS handshake plus the READY exchange
    handshake_bytes: int        # TCP bytes both ways during establish()

    @classmethod
    def establish(cls, sock, role, ident: Identity, peer_cert_pem: bytes,
                  timeout: float = HANDSHAKE_TIMEOUT) -> "SecureChannel"
        # Runs TLS, enforces group, ALPN and pin. Server then sends MSG_READY;
        # client waits for it, which proves the server accepted the client cert
        # (TLS 1.3 clients finish before the server checks them).
        # Every failure raises HandshakeError and closes the TLS connection.
    def send(self, msg_type: int, payload: bytes = b"") -> None   # thread-safe
    def recv(self, timeout: float | None = None) -> tuple[int, bytes] | None
        # None on timeout; ConnectionError when the peer closes (clean or not);
        # ValueError on a malformed frame. Safe to call from one reader thread
        # while other threads call send(); all SSL calls are serialized by one lock.
    def media_keys(self) -> MediaKeys
    def sas(self) -> str        # exporter, 4 bytes as 8 hex chars, shown in the GUI
    def close(self) -> None     # idempotent: TLS close_notify (best effort), then socket close

def local_pair(timeout: float = HANDSHAKE_TIMEOUT) -> tuple[SecureChannel, SecureChannel]
    # (client, server) with fresh in-memory identities over a 127.0.0.1 TCP pair.
    # Used by tests and hpqv/eval.
```

**Control framing inside TLS:** `msg_type (1) ‖ length (uint16 BE) ‖ payload`,
with `length <= MAX_CONTROL_PAYLOAD`. Anything bigger is a ValueError, raised
before the read allocates.

**Exporter labels**, each with an empty context:
- `EXPORTER-hpqv2 media c2s` and `EXPORTER-hpqv2 media s2c` (32 bytes each);
- `EXPORTER-hpqv2 session id` (4 bytes);
- `EXPORTER-hpqv2 sas` (4 bytes).

On the client, `send_secret` is the c2s secret.

### `src/hpqv/control.py` (wave 2 trims it to constants)

```python
MSG_CALL_START, MSG_CALL_END, MSG_KEY_ROTATE, MSG_READY = 1, 2, 3, 4
```

The v1 `send_record`, `recv_record`, `run_initiator` and `run_responder` are
deleted. Nothing in v2 frames raw TCP itself.

### `src/hpqv/packet.py` (wave 1, T2): v2 voice datagram

```python
VERSION, TYPE_VOICE = 2, 1
VER_TYPE = (VERSION << 4) | TYPE_VOICE         # 0x21
HEADER_LEN, TAG_LEN = 12, 16
MAX_DATAGRAM = 1200
MIN_DATAGRAM = HEADER_LEN + TAG_LEN + 1
MAX_SEQ = 2**48 - 1
GEN_MASK, FLAG_FEC = 0x03, 0x04                # bits 3-7 reserved, must be 0

class Reject(ValueError):
    reason: str   # "oversize" | "truncated" | "malformed" | "wrong_session" |
                  # "no_key" | "replay" | "auth"
REJECT_REASONS: tuple[str, ...]

def direction_keys(secret: bytes) -> tuple[bytes, bytes]   # (key32, iv12) = HKDF-Expand-SHA256
    # with info b"hpqv2 media key" / b"hpqv2 media iv"
def next_secret(secret: bytes) -> bytes   # HKDF-Expand-SHA256(secret, b"hpqv2 media upd", 32)
def parse_header(dg: bytes) -> tuple[bytes, int, int]   # (session_id, seq, flags); raises Reject;
    # size checks first, then ver_type, then reserved bits
def seal(key, iv, session_id, seq, frame, flags=0) -> bytes
    # ValueError if seq > MAX_SEQ, or the result is > MAX_DATAGRAM
def open_(key, iv, dg) -> tuple[int, int, bytes]   # (seq, flags, frame); Reject("auth") on tag failure
```

`nonce = iv XOR (0^6 ‖ seq.to_bytes(6))`. The AAD is the full 12-byte header.

### `src/hpqv/replay.py` (wave 1, T2)

```python
class ReplayWindow:
    def __init__(self, size: int = 256): ...
    def check(self, seq: int) -> bool    # pure: False if too old or already seen
    def accept(self, seq: int) -> None   # call only after the AEAD verifies
```

### `src/hpqv/keyring.py` (wave 1, T2): replaces v1 keyring and `session.py`

```python
class Keyring:
    def __init__(self, keys: "secure.MediaKeys", retire_after=RETIRE_AFTER, clock=time.monotonic)
    generation: int                      # property
    rejects: dict[str, int]              # snapshot property: counts per packet.REJECT_REASONS
    def seal(self, seq, frame, extra_flags=0) -> bytes
    def open_(self, dg) -> tuple[int, int, bytes]
        # order: parse_header -> session_id -> generation key -> replay check -> AEAD
        #   -> replay accept. Raises packet.Reject and counts it.
        #   Nothing but the reject counter changes before the AEAD succeeds.
    def rotation_ready(self) -> bool
    def rotate_to(self, generation) -> bool  # same rules as v1 (ADR-0004)
    def rotate_next(self) -> int             # same rules as v1
```

- **Rotation** advances both directions:
  `send_secret = next_secret(send_secret)`, `recv_secret = next_secret(recv_secret)`.
- **Retention** follows v1 (ADR-0004): older receive keys stay for `RETIRE_AFTER`
  seconds, and the generation tag is 2 bits.
- **seq** keeps counting across rotations, and one replay window per Keyring
  covers every generation.
- `Keyring.of()` is deleted: callers always pass a Keyring.

`src/hpqv/session.py` is deleted. Its job moves to `packet.next_secret`.

## Waves and ownership (files outside your list are read-only for you)

| Thread | Model | Owns |
|---|---|---|
| **T1 tls-channel** (wave 1) | Opus | `src/hpqv/identity.py`, `src/hpqv/secure.py`, `tests/test_identity.py`, `tests/test_secure.py` |
| **T2 media-v2** (wave 1) | Opus | `src/hpqv/packet.py`, `src/hpqv/replay.py`, `src/hpqv/keyring.py`, delete `src/hpqv/session.py`; `tests/test_packet.py`, `tests/test_replay.py`, `tests/test_keyring.py`, delete `tests/test_session.py` |
| **T3 docs** (wave 1) | Sonnet | `docs/PROTOCOL-v2.md` (rewrite), `docs/adr/0005-protocol-v2-tls13.md`, `docs/wire-format.md` (add a v2 section, mark v1 historical) |
| **T4 integration** (wave 2) | Sonnet | `src/hpqv/{control,callctl,demo,call,gui,gui_panels,gui_logic,telemetry}.py`; their tests |
| **T5 eval + packaging** (wave 2) | Sonnet | `src/hpqv/eval/*`, `scripts/*`, `packaging/*`, `.github/workflows/*`, `eval/Dockerfile`, `pyproject.toml`/`uv.lock` (remove liboqs-python), `tests/eval/*`, `tests/test_selfcheck.py` |
| **T6 security review** (wave 3) | Opus | read-only review of the whole branch diff |

## Rules for every thread

- `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q <your tests>`.
  If `uv` cannot write its cache, set `UV_CACHE_DIR=$TMPDIR/uvcache`.
- Never implement a cryptographic primitive. Use `cryptography`, pyOpenSSL and
  hashlib only.
- Never log or export key material, secrets, PEM private keys, exporter
  output or audio. Logging uses the `logging` module with structured `extra`
  fields.
- Bounded everything: no unbounded reads, queues or allocations driven by peer input.
- Negative security tests come first (red), then the code (green).
- Do not touch `results/` or claim measured numbers you did not run.
- Commit on your worktree branch with a clear message ending with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (Sonnet threads use
  their own model name). Do not push.

## Wave 2 interface (T4 implements it; T5 calls it)

```python
# src/hpqv/callctl.py
class CallHandle:
    def start(self, channel: SecureChannel, stats, stop_event) -> Keyring
        # builds Keyring(channel.media_keys()); a reader thread applies KEY_ROTATE
        # and CALL_END from channel.recv(timeout=POLL)
    # rotate_keys, rotation_ready, hang_up, key_generation, finish: same as v1.
    # key_fingerprint() is replaced by sas() -> channel.sas().
    # peer_ended (an authenticated CALL_END) and connection_lost (TCP gone)
    # are reported separately.

# src/hpqv/demo.py
def _run_call(channel, udp_sock, dest, args, stats=None, controls=None,
              stop_event=None, call_handle=None, rng_seed=None, quiet=False) -> None
def establish_timed(role, sock, ident, peer_cert_pem, stats) -> SecureChannel
    # replaces timed_handshake; copies channel.handshake_ms / handshake_bytes /
    # group / cipher / peer_fingerprint into stats
def handshake_info() -> dict   # static facts, no liboqs:
    # {"protocol": "TLSv1.3", "group": "X25519MLKEM768", "sig": "ML-DSA-65",
    #  "transport": "TCP", "udp_fragments": 0, "datagram_cap": 1200}
class Stats: v1 fields plus group: str, cipher: str, peer_fingerprint: str, sas: str
    # and rejects: dict[str, int], mirrored from Keyring.rejects by the receiver loop
# CLI:
#   keygen --name NAME --key-out PATH --cert-out PATH   (prints the fingerprint)
#   listen/call --key PATH --cert PATH --peer-cert PATH  (--identity-file removed;
#   pointing --peer-cert at a v1 identity.json exits with the LegacyIdentityError message)
# call_args(**overrides) stays; its fields follow the new flags (key, cert, peer_cert).
```

The receiver loop calls `recvfrom(packet.MAX_DATAGRAM + 1)`. A `packet.Reject` is
counted by the Keyring and the datagram is dropped; it never touches
`stats.received`, `RxMeter` or the jitter buffer.
