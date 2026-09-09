# Track 1 — Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the two follow-on security gaps: make the handshake mutually authenticated (responder signs its reply) and add the control-plane messages (call setup/teardown + key rotation) the spec promises.

**Architecture:** Extend the existing handshake so the responder signs its ACCEPT and the initiator verifies it with the already-plumbed `peer_sig_pub`. Add typed control records over the existing TCP length-prefixed framing, plus an HKDF key-rotation ratchet both peers apply in lockstep.

**Tech Stack:** Python 3.11, existing `hpqv` modules, liboqs (ML-DSA-65), cryptography (HKDF).

**Spec:** docs/superpowers/specs/2026-09-08-hybrid-pqc-voice-design.md
**ADR:** docs/adr/0002-add-responder-authentication.md
**Roadmap:** docs/superpowers/plans/2026-09-09-evaluation-and-hardening-roadmap.md (Track 1)

## Global Constraints

- HOST task set (crypto only, no netem/container). Tests: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest <f> -v`. All Bash sandbox-disabled.
- Session stays the 3-tuple `(key, send_prefix, recv_prefix)` — do NOT change its shape (call.py, eval/* depend on it).
- The EXTERNAL signatures of `control.run_initiator(sock, sig_pub, sig_secret, peer_sig_pub)` and `run_responder(...)` MUST stay identical (eval + test_call depend on them). Only the internal handshake functions change.
- KEM = ML-KEM-768, SIG = ML-DSA-65. Update docs/wire-format.md to match.
- TDD, commit per task.

## File Structure
- `src/hpqv/handshake.py` — responder signs ACCEPT; initiator verifies (Task 1)
- `src/hpqv/control.py` — thread the responder secret + verification; add control messages (Tasks 1–2)
- `src/hpqv/session.py` — key-rotation ratchet (Task 2)
- `docs/wire-format.md` — ACCEPT layout + control-message spec
- `tests/test_handshake.py`, `tests/test_control.py` updated; `tests/test_session.py` new

---

### Task 1: Mutual authentication (responder signs ACCEPT)

**Files:** Modify `src/hpqv/handshake.py`, `src/hpqv/control.py`, `docs/wire-format.md`; update `tests/test_handshake.py`.

**Interfaces (internal changes — external run_* signatures unchanged):**
- `build_hello(sig_pub, sig_secret) -> (hello_bytes, kem_object, kem_pub)` — now also returns kem_pub.
- `accept_hello(hello, trusted_peer_sig_pub, my_sig_secret) -> (accept_bytes, session)` — verifies initiator (as before) AND signs the transcript `kem_pub ‖ ct` with `my_sig_secret`; ACCEPT = `VERSION ‖ ct ‖ resp_sig`.
- `finish(accept, kem, kem_pub, trusted_peer_sig_pub) -> session` — verifies `resp_sig` over `kem_pub ‖ ct` against the pinned responder key; raises `ValueError` on bad/forged signature; then decapsulates.
- `control.run_responder` passes its `sig_secret` into `accept_hello`; `control.run_initiator` keeps `kem_pub` from `build_hello` and passes it + `peer_sig_pub` into `finish`.

- [ ] **Step 1: Write the failing tests** (update tests/test_handshake.py)
```python
import pytest
from hpqv import handshake as h

def test_mutual_auth_both_derive_matching_session():
    a_pub, a_sec = h.make_identity()   # initiator identity
    b_pub, b_sec = h.make_identity()   # responder identity
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)     # responder signs
    sess_a = h.finish(accept, kem, kem_pub, b_pub)           # initiator verifies b
    # crossed prefixes, same key (from Track-0 hardening)
    assert sess_a[0] == sess_b[0]
    assert sess_a[1] == sess_b[2] and sess_a[2] == sess_b[1]

def test_initiator_rejects_forged_accept():
    a_pub, a_sec = h.make_identity()
    b_pub, b_sec = h.make_identity()
    evil_pub, evil_sec = h.make_identity()                  # attacker identity
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, evil_sec)      # signed by attacker, not b
    with pytest.raises(ValueError):
        h.finish(accept, kem, kem_pub, b_pub)               # pinned to b -> reject

def test_initiator_rejects_tampered_ciphertext():
    a_pub, a_sec = h.make_identity(); b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, _ = h.accept_hello(hello, a_pub, b_sec)
    bad = bytearray(accept); bad[10] ^= 0xFF                # flip a ciphertext byte
    with pytest.raises(ValueError):
        h.finish(bytes(bad), kem, kem_pub, b_pub)
```

- [ ] **Step 2: Run — expect FAIL** — `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/test_handshake.py -v`

- [ ] **Step 3: Implement** — in handshake.py:
  - `build_hello` returns `(hello, kem, kem_pub)`.
  - `accept_hello(hello, trusted_peer_sig_pub, my_sig_secret)`: verify initiator sig + pin (unchanged), `ct, shared = kem.encap_secret(kem_pub)`, `resp_sig = oqs.Signature(SIG_ALG, secret_key=my_sig_secret).sign(kem_pub + ct)`, `accept = bytes([VERSION]) + ct + resp_sig`, responder session = derive→(key, prefix_r2i, prefix_i2r).
  - `finish(accept, kem, kem_pub, trusted_peer_sig_pub)`: split `ct = accept[1:1+1088]`, `resp_sig = accept[1+1088:]`; `if not oqs.Signature(SIG_ALG).verify(kem_pub + ct, resp_sig, trusted_peer_sig_pub): raise ValueError("bad responder signature")`; `shared = kem.decap_secret(ct)`; initiator session = derive→(key, prefix_i2r, prefix_r2i).
  - Update control.run_initiator/run_responder wiring (keep external signatures).
  - Update docs/wire-format.md ACCEPT to `version(1B) ‖ kem_ciphertext(1088B) ‖ resp_sig(3309B)` and note mutual auth.

- [ ] **Step 4: Run — expect PASS**. Also run the whole suite to confirm test_control/test_call still pass (run_* signatures unchanged): `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/test_handshake.py tests/test_control.py tests/test_call.py -v`

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(handshake): mutual authentication — responder signs ACCEPT"`

---

### Task 2: Control-plane messages + key rotation ratchet

**Files:** Create `src/hpqv/session.py`, add control-message helpers to `src/hpqv/control.py`, `docs/wire-format.md`; create `tests/test_session.py`, extend `tests/test_control.py`.

**Interfaces:**
- `control.MSG_CALL_START, MSG_CALL_END, MSG_KEY_ROTATE` (distinct int constants).
- `control.send_control(sock, msg_type: int, payload: bytes = b"") -> None` and `recv_control(sock) -> tuple[int, bytes]` — a control record = `msg_type(1B) ‖ payload`, sent via the existing length-prefixed framing.
- `session.rotate(session) -> session` — deterministic HKDF ratchet applied identically by both peers on KEY_ROTATE: `okm = HKDF(SHA256, 40, info=b"hpqv rotate v1").derive(key)`, new session = `(okm[:32], okm[32:36], okm[36:40])` but PRESERVE direction: the caller keeps send/recv roles, so ratchet each of key and re-split, keeping send_prefix/recv_prefix ordering. (Simplest correct form: ratchet the key, and derive two fresh prefixes; both peers pass the SAME current session's key so they land on the same new key; each keeps its own send/recv assignment by ratcheting `(key, send_prefix, recv_prefix)` → `(new_key, new_send, new_recv)` where new prefixes come from HKDF over `key` in a fixed order and each side maps them by its existing role.)

  Concrete rule to avoid ambiguity: `rotate((key, send_prefix, recv_prefix))` returns `(new_key, new_send, new_recv)` where `okm = HKDF(key, 40, info=b"hpqv rotate v1")`, `new_key = okm[:32]`. To keep directions distinct and consistent, derive `p0, p1 = okm[32:36], okm[36:40]` and map: the INITIATOR-side and RESPONDER-side must agree. Since both hold mirror sessions (A.send==B.recv), have rotate map `new_send, new_recv = (p0, p1) if send_prefix < recv_prefix else (p1, p0)` — a stable ordering both peers compute identically from their mirrored prefixes. (send_prefix<recv_prefix differs by side, so A and B pick opposite (p0,p1) → A.new_send == B.new_recv. Verify in the test.)

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_session.py
from hpqv import handshake as h, session as s

def test_rotate_keeps_peers_in_sync_and_changes_key():
    a_pub, a_sec = h.make_identity(); b_pub, b_sec = h.make_identity()
    hello, kem, kem_pub = h.build_hello(a_pub, a_sec)
    accept, sess_b = h.accept_hello(hello, a_pub, b_sec)
    sess_a = h.finish(accept, kem, kem_pub, b_pub)
    ra, rb = s.rotate(sess_a), s.rotate(sess_b)
    assert ra[0] == rb[0] and ra[0] != sess_a[0]     # new shared key, changed
    assert ra[1] == rb[2] and ra[2] == rb[1]         # directions still mirror
```
```python
# tests/test_control.py — add
import socket, threading
from hpqv import control as c
def test_control_message_round_trip():
    s1, s2 = socket.socketpair()
    c.send_control(s1, c.MSG_KEY_ROTATE, b"gen1")
    t, payload = c.recv_control(s2)
    assert t == c.MSG_KEY_ROTATE and payload == b"gen1"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement** session.py (rotate per the rule above) and control.py helpers (send_control/recv_control reusing send_record/recv_record with a 1-byte type prefix; define the three MSG_* constants). Update docs/wire-format.md with a "Control messages" section.

- [ ] **Step 4: Run — expect PASS** (plus full suite green on host, container-only eval tests skip).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(control): call setup/teardown + key-rotation ratchet"`

---

## Self-review
- ADR-0002 satisfied: responder signs ACCEPT, initiator verifies with peer_sig_pub (Task 1). ✓
- Spec control-plane (setup/teardown + one key rotation) present (Task 2). ✓
- Session 3-tuple shape unchanged; run_* external signatures unchanged → call.py + eval untouched. ✓
- Nonce discipline: after KEY_ROTATE the key changes, so per-direction sequence counters should reset to 0 in the app when rotating (note for the demo; the seal/open_ API already takes seq explicitly).
