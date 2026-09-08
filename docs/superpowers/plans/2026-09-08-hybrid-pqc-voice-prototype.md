# Hybrid PQC Voice — Core Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working two-peer, PQC-secured voice call over a hybrid TCP (control) + UDP (data) transport, running end-to-end on localhost.

**Architecture:** A custom PQC handshake (Dilithium-signed Kyber, self-signed cert) runs over TCP and derives a symmetric key via HKDF. Opus voice frames are encrypted with ChaCha20-Poly1305 and sent over UDP, each datagram capped at 1200 bytes with the Don't-Fragment bit set. Receiver reorders through a fixed 60 ms jitter buffer, decrypts, and decodes.

**Tech Stack:** Python 3.11+, uv, pytest, liboqs-python (ML-KEM-768, ML-DSA-65), cryptography (ChaCha20-Poly1305, HKDF), opuslib, numpy.

**Spec:** docs/superpowers/specs/2026-09-08-hybrid-pqc-voice-design.md

## Global Constraints

- Python 3.11+; dependencies managed with `uv`.
- KEM = `ML-KEM-768`; signature = `ML-DSA-65` (liboqs algorithm names, verbatim).
- Every UDP voice datagram is ≤ 1200 bytes total, and the UDP socket sets the IP Don't-Fragment bit (`IP_MTU_DISCOVER = IP_PMTUDISC_DO` on Linux).
- ChaCha20-Poly1305 nonce = 4-byte prefix (from HKDF) + 8-byte big-endian monotonic sequence counter. A (key, nonce) pair is never reused.
- Audio: 48 kHz, mono, 20 ms frames (960 samples/frame), Opus in-band FEC enabled.
- Package name `hpqv`. Tests under `tests/`. TDD: failing test first, minimal impl, commit each task.

## File Structure

- `pyproject.toml` — project + deps (uv)
- `docs/wire-format.md` — the handshake + datagram byte contract (Phase 0)
- `src/hpqv/__init__.py`
- `src/hpqv/handshake.py` — PQC crypto core + HKDF (Task 1)
- `src/hpqv/control.py` — handshake over TCP, length-prefixed framing (Task 2)
- `src/hpqv/packet.py` — voice datagram encrypt/decrypt + size guard (Task 3)
- `src/hpqv/jitter.py` — fixed jitter buffer (Task 4)
- `src/hpqv/audio.py` — Opus encode/decode + WAV/mic source (Task 5)
- `src/hpqv/udp.py` — UDP socket with DF bit (Task 6)
- `src/hpqv/call.py` — end-to-end call wiring (Task 7)
- `tests/…` — one test module per source module

---

### Task 0: Repo scaffold + wire-format contract

**Files:**
- Create: `pyproject.toml`, `src/hpqv/__init__.py`, `tests/test_smoke.py`, `docs/wire-format.md`, `.gitignore`

**Interfaces:**
- Produces: importable package `hpqv`; the wire-format doc that Tasks 1–3 implement against.

- [ ] **Step 1: Init repo and uv project**

```bash
git init
uv init --package --name hpqv --python 3.11
uv add liboqs-python cryptography opuslib numpy
uv add --dev pytest
```

- [ ] **Step 2: Write the wire-format contract**

Create `docs/wire-format.md` with:

```markdown
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
```

- [ ] **Step 3: Smoke test**

```python
# tests/test_smoke.py
import hpqv
def test_import():
    assert hpqv is not None
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: scaffold hpqv package and wire-format contract"
```

---

### Task 1: PQC handshake crypto core (no sockets)

**Files:**
- Create: `src/hpqv/handshake.py`, `tests/test_handshake.py`

**Interfaces:**
- Produces:
  - `make_identity() -> tuple[bytes, bytes]` returns `(sig_pub, sig_secret)` for ML-DSA-65.
  - `build_hello(sig_pub, sig_secret) -> tuple[bytes, object]` returns `(hello_bytes, kem_object)`; kem_object holds the initiator's KEM secret key.
  - `accept_hello(hello_bytes, trusted_sig_pub) -> tuple[bytes, bytes]` returns `(accept_bytes, session)` where `session = (key, nonce_prefix)`; raises `ValueError` on bad signature.
  - `finish(accept_bytes, kem_object) -> tuple[bytes, bytes]` returns `(key, nonce_prefix)` for the initiator.
  - `derive(shared_secret: bytes) -> tuple[bytes, bytes]` HKDF → `(32B key, 4B prefix)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handshake.py
import pytest
from hpqv import handshake as h

def test_both_sides_derive_same_key():
    sig_pub, sig_sec = h.make_identity()
    hello, kem = h.build_hello(sig_pub, sig_sec)
    accept, resp_session = h.accept_hello(hello, sig_pub)
    init_session = h.finish(accept, kem)
    assert init_session == resp_session
    assert len(init_session[0]) == 32 and len(init_session[1]) == 4

def test_tampered_signature_rejected():
    sig_pub, sig_sec = h.make_identity()
    hello, _ = h.build_hello(sig_pub, sig_sec)
    bad = bytearray(hello); bad[-1] ^= 0xFF
    with pytest.raises(ValueError):
        h.accept_hello(bytes(bad), sig_pub)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_handshake.py -v`
Expected: FAIL (module has no such functions)

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/handshake.py
import oqs
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

KEM_ALG, SIG_ALG = "ML-KEM-768", "ML-DSA-65"
VERSION = 1

def derive(shared_secret: bytes) -> tuple[bytes, bytes]:
    okm = HKDF(algorithm=hashes.SHA256(), length=36, salt=None,
               info=b"hpqv v1").derive(shared_secret)
    return okm[:32], okm[32:36]

def make_identity() -> tuple[bytes, bytes]:
    sig = oqs.Signature(SIG_ALG)
    pub = sig.generate_keypair()
    return pub, sig.export_secret_key()

def build_hello(sig_pub: bytes, sig_secret: bytes):
    kem = oqs.KeyEncapsulation(KEM_ALG)
    kem_pub = kem.generate_keypair()
    signer = oqs.Signature(SIG_ALG, secret_key=sig_secret)
    sig = signer.sign(kem_pub + sig_pub)
    hello = bytes([VERSION]) + kem_pub + sig_pub + sig
    return hello, kem

def accept_hello(hello: bytes, trusted_sig_pub: bytes):
    kem_pub = hello[1:1+1184]
    sig_pub = hello[1+1184:1+1184+1952]
    sig = hello[1+1184+1952:]
    if sig_pub != trusted_sig_pub:
        raise ValueError("unpinned identity")
    verifier = oqs.Signature(SIG_ALG)
    if not verifier.verify(kem_pub + sig_pub, sig, sig_pub):
        raise ValueError("bad signature")
    kem = oqs.KeyEncapsulation(KEM_ALG)
    ct, shared = kem.encap_secret(kem_pub)
    accept = bytes([VERSION]) + ct
    return accept, derive(shared)

def finish(accept: bytes, kem) -> tuple[bytes, bytes]:
    ct = accept[1:]
    shared = kem.decap_secret(ct)
    return derive(shared)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_handshake.py -v`
Expected: PASS. (If liboqs API names differ on the installed version, check `python -c "import oqs; help(oqs.KeyEncapsulation)"` and adjust `encap_secret`/`decap_secret`/`export_secret_key` names — record the correct names in wire-format.md.)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: PQC handshake crypto core with HKDF"
```

---

### Task 2: Handshake over TCP

**Files:**
- Create: `src/hpqv/control.py`, `tests/test_control.py`

**Interfaces:**
- Consumes: `handshake.make_identity/build_hello/accept_hello/finish`.
- Produces:
  - `send_record(sock, payload: bytes) -> None` and `recv_record(sock) -> bytes` (4-byte length prefix).
  - `run_initiator(sock, sig_pub, sig_secret, peer_sig_pub) -> tuple[bytes, bytes]` returns session.
  - `run_responder(sock, sig_pub, sig_secret, peer_sig_pub) -> tuple[bytes, bytes]` returns session.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_control.py
import socket, threading
from hpqv import handshake as h, control as c

def test_handshake_over_tcp_localhost():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    out = {}
    def responder():
        conn, _ = srv.accept()
        out["b"] = c.run_responder(conn, b_pub, b_sec, a_pub)
    t = threading.Thread(target=responder); t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    out["a"] = c.run_initiator(cli, a_pub, a_sec, b_pub)
    t.join()
    assert out["a"] == out["b"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_control.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/control.py
import struct
from hpqv import handshake as h

def send_record(sock, payload: bytes) -> None:
    sock.sendall(struct.pack(">I", len(payload)) + payload)

def _recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf

def recv_record(sock) -> bytes:
    (length,) = struct.unpack(">I", _recvn(sock, 4))
    return _recvn(sock, length)

def run_initiator(sock, sig_pub, sig_secret, peer_sig_pub):
    hello, kem = h.build_hello(sig_pub, sig_secret)
    send_record(sock, hello)
    accept = recv_record(sock)
    return h.finish(accept, kem)

def run_responder(sock, sig_pub, sig_secret, peer_sig_pub):
    hello = recv_record(sock)
    accept, session = h.accept_hello(hello, peer_sig_pub)
    send_record(sock, accept)
    return session
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_control.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: run PQC handshake over TCP control channel"
```

---

### Task 3: Voice datagram encrypt/decrypt + size guard

**Files:**
- Create: `src/hpqv/packet.py`, `tests/test_packet.py`

**Interfaces:**
- Produces:
  - `seal(key, nonce_prefix, seq: int, opus_frame: bytes, flags: int = 0) -> bytes` returns a datagram ≤1200B; raises `ValueError` if it would exceed 1200.
  - `open_(key, nonce_prefix, datagram: bytes) -> tuple[int, int, bytes]` returns `(seq, flags, opus_frame)`; raises on auth failure.
  - Constant `MAX_DATAGRAM = 1200`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_packet.py
import os, pytest
from hpqv import packet as p

def test_seal_open_roundtrip():
    key, prefix = os.urandom(32), os.urandom(4)
    dg = p.seal(key, prefix, 42, b"opusdata", flags=1)
    assert len(dg) <= p.MAX_DATAGRAM
    seq, flags, frame = p.open_(key, prefix, dg)
    assert (seq, flags, frame) == (42, 1, b"opusdata")

def test_oversize_frame_rejected():
    key, prefix = os.urandom(32), os.urandom(4)
    with pytest.raises(ValueError):
        p.seal(key, prefix, 1, b"x" * 1300)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_packet.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/packet.py
import struct
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

MAX_DATAGRAM = 1200
VERSION, TYPE_VOICE = 1, 1

def _header(seq: int, flags: int) -> bytes:
    return struct.pack(">BBQB", VERSION, TYPE_VOICE, seq, flags)

def seal(key, nonce_prefix, seq, opus_frame, flags=0):
    header = _header(seq, flags)
    nonce = nonce_prefix + struct.pack(">Q", seq)
    ct = ChaCha20Poly1305(key).encrypt(nonce, opus_frame, header)
    dg = header + ct
    if len(dg) > MAX_DATAGRAM:
        raise ValueError(f"datagram {len(dg)} > {MAX_DATAGRAM}")
    return dg

def open_(key, nonce_prefix, datagram):
    header, ct = datagram[:11], datagram[11:]
    _, _, seq, flags = struct.unpack(">BBQB", header)
    nonce = nonce_prefix + struct.pack(">Q", seq)
    frame = ChaCha20Poly1305(key).decrypt(nonce, ct, header)
    return seq, flags, frame
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_packet.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: voice datagram AEAD seal/open with 1200B guard"
```

---

### Task 4: Fixed jitter buffer

**Files:**
- Create: `src/hpqv/jitter.py`, `tests/test_jitter.py`

**Interfaces:**
- Produces:
  - `class JitterBuffer(depth: int = 3)` — holds up to `depth` frames (3×20 ms = 60 ms).
  - `.push(seq: int, frame: bytes) -> None` — ignores duplicates and frames older than the last popped seq.
  - `.pop() -> bytes | None` — returns the next in-order frame once buffer is full, else None.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jitter.py
from hpqv.jitter import JitterBuffer

def test_reorders_and_drops_late():
    jb = JitterBuffer(depth=2)
    jb.push(2, b"b"); jb.push(1, b"a")   # arrive out of order
    assert jb.pop() == b"a"              # buffer full -> emit lowest
    jb.push(3, b"c")
    assert jb.pop() == b"b"
    jb.push(1, b"late")                  # older than last popped -> dropped
    jb.push(4, b"d")
    assert jb.pop() == b"c"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_jitter.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/jitter.py
import heapq

class JitterBuffer:
    def __init__(self, depth: int = 3):
        self.depth = depth
        self._heap = []
        self._seen = set()
        self._last = -1

    def push(self, seq: int, frame: bytes) -> None:
        if seq <= self._last or seq in self._seen:
            return
        self._seen.add(seq)
        heapq.heappush(self._heap, (seq, frame))

    def pop(self):
        if len(self._heap) < self.depth:
            return None
        seq, frame = heapq.heappop(self._heap)
        self._seen.discard(seq)
        self._last = seq
        return frame
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_jitter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: fixed-depth jitter buffer with reorder and late-drop"
```

---

### Task 5: Audio pipeline (Opus + WAV source)

**Files:**
- Create: `src/hpqv/audio.py`, `tests/test_audio.py`

**Interfaces:**
- Produces:
  - `class OpusCodec()` with `.encode(pcm: bytes) -> bytes` and `.decode(data: bytes) -> bytes`, 48 kHz mono 20 ms (960 samples), in-band FEC on.
  - `wav_frames(path: str) -> Iterator[bytes]` yields 960-sample (1920-byte) 16-bit PCM frames.
  - `FRAME_SAMPLES = 960`, `FRAME_BYTES = 1920`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audio.py
from hpqv.audio import OpusCodec, FRAME_BYTES

def test_opus_roundtrip_frame_length():
    codec_e, codec_d = OpusCodec(), OpusCodec()
    pcm = b"\x00\x01" * 960                 # one 20ms mono frame
    encoded = codec_e.encode(pcm)
    assert 0 < len(encoded) < FRAME_BYTES   # compressed
    decoded = codec_d.decode(encoded)
    assert len(decoded) == FRAME_BYTES      # 960 samples * 2 bytes
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_audio.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/audio.py
import wave
import opuslib

FS, CHANNELS, FRAME_SAMPLES = 48000, 1, 960
FRAME_BYTES = FRAME_SAMPLES * 2

class OpusCodec:
    def __init__(self):
        self.enc = opuslib.Encoder(FS, CHANNELS, opuslib.APPLICATION_VOIP)
        self.enc.inband_fec = 1
        self.enc.packet_loss_perc = 10
        self.dec = opuslib.Decoder(FS, CHANNELS)

    def encode(self, pcm: bytes) -> bytes:
        return self.enc.encode(pcm, FRAME_SAMPLES)

    def decode(self, data: bytes) -> bytes:
        return self.dec.decode(data, FRAME_SAMPLES)

def wav_frames(path: str):
    with wave.open(path, "rb") as w:
        while True:
            pcm = w.readframes(FRAME_SAMPLES)
            if len(pcm) < FRAME_BYTES:
                break
            yield pcm
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_audio.py -v`
Expected: PASS. (If `opuslib` attribute names differ, check `python -c "import opuslib; help(opuslib.Encoder)"`; some builds use `set_inband_fec()` methods instead of properties — adjust and note it.)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: Opus codec with in-band FEC and WAV frame source"
```

---

### Task 6: UDP transport with Don't-Fragment

**Files:**
- Create: `src/hpqv/udp.py`, `tests/test_udp.py`

**Interfaces:**
- Produces:
  - `open_udp(bind_addr=("127.0.0.1", 0)) -> socket.socket` with DF bit set.
  - Reuses `packet.seal/open_` at call sites (not re-wrapped here).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_udp.py
from hpqv.udp import open_udp

def test_loopback_send_recv():
    a = open_udp(); b = open_udp()
    a.sendto(b"hello", b.getsockname())
    data, _ = b.recvfrom(2048)
    assert data == b"hello"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_udp.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/udp.py
import socket

def open_udp(bind_addr=("127.0.0.1", 0)) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(bind_addr)
    # Set Don't-Fragment so oversize packets error loudly instead of fragmenting.
    if hasattr(socket, "IP_MTU_DISCOVER"):
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MTU_DISCOVER, socket.IP_PMTUDISC_DO)
    return s
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_udp.py -v`
Expected: PASS. (macOS lacks `IP_MTU_DISCOVER`; the guard skips it there. The DF proof runs on Linux, where the eval also runs — note this in wire-format.md.)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: UDP socket helper with Don't-Fragment bit"
```

---

### Task 7: End-to-end call wiring

**Files:**
- Create: `src/hpqv/call.py`, `tests/test_call.py`

**Interfaces:**
- Consumes: `control.run_initiator/run_responder`, `packet.seal/open_`, `jitter.JitterBuffer`, `audio.OpusCodec/wav_frames`, `udp.open_udp`.
- Produces:
  - `send_stream(udp_sock, dest, session, frames) -> int` — encodes+seals each frame, sends, returns count sent.
  - `recv_stream(udp_sock, session, expected: int) -> list[bytes]` — receives, opens, jitter-buffers, decodes; returns decoded PCM frames.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_call.py
import threading, socket
from hpqv import handshake as h, control as c, call
from hpqv.udp import open_udp

def test_end_to_end_voice_over_localhost():
    # handshake over TCP
    a_pub, a_sec = h.make_identity(); b_pub, b_sec = h.make_identity()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    box = {}
    def responder():
        conn, _ = srv.accept(); box["b"] = c.run_responder(conn, b_pub, b_sec, a_pub)
    t = threading.Thread(target=responder); t.start()
    cli = socket.create_connection(("127.0.0.1", port))
    sess_a = c.run_initiator(cli, a_pub, a_sec, b_pub); t.join(); sess_b = box["b"]
    assert sess_a == sess_b

    # voice over UDP
    ua, ub = open_udp(), open_udp()
    pcm = b"\x11\x22" * 960
    frames = [pcm, pcm, pcm, pcm, pcm]
    out = {}
    rx = threading.Thread(target=lambda: out.__setitem__("frames",
        call.recv_stream(ub, sess_b, expected=len(frames))))
    rx.start()
    sent = call.send_stream(ua, ub.getsockname(), sess_a, frames)
    rx.join()
    assert sent == len(frames)
    assert len(out["frames"]) >= 1
    assert all(len(f) == len(pcm) for f in out["frames"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_call.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/hpqv/call.py
from hpqv import packet
from hpqv.audio import OpusCodec
from hpqv.jitter import JitterBuffer

def send_stream(udp_sock, dest, session, frames) -> int:
    key, prefix = session
    codec = OpusCodec()
    n = 0
    for seq, pcm in enumerate(frames):
        opus = codec.encode(pcm)
        dg = packet.seal(key, prefix, seq, opus)
        udp_sock.sendto(dg, dest)
        n += 1
    return n

def recv_stream(udp_sock, session, expected: int) -> list[bytes]:
    key, prefix = session
    codec = OpusCodec()
    jb = JitterBuffer(depth=3)
    out, got = [], 0
    udp_sock.settimeout(2.0)
    while got < expected:
        try:
            dg, _ = udp_sock.recvfrom(2048)
        except OSError:
            break
        seq, _flags, opus = packet.open_(key, prefix, dg)
        jb.push(seq, opus)
        got += 1
        frame = jb.pop()
        if frame is not None:
            out.append(codec.decode(frame))
    while (frame := jb.pop()) is not None:   # drain
        out.append(codec.decode(frame))
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_call.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: end-to-end PQC voice call over hybrid TCP+UDP"
```

---

## What this plan deliberately leaves out

- **Live mic / speaker I/O** (sounddevice) — the pipeline is source-agnostic; mic is a swap-in at demo time. Add a `mic_frames()` generator mirroring `wav_frames()` when building the demo.
- **Call setup/teardown + key-rotation control messages** — the handshake session is enough for the prototype; add control message types to `control.py` in the next plan.
- **Baselines (naive-PQC-over-UDP, TCP/TLS) and the tc netem eval harness (Phases 5–6)** — their own plan, written after this prototype runs and we can capture real packets.

## Follow-on

After this plan passes, write `docs/superpowers/plans/<date>-hybrid-pqc-voice-eval.md` covering baselines + tc netem sweep + fragment-count/MOS/TTFB measurement + graphs (spec §Testing & Evaluation, Phases 5–6).
