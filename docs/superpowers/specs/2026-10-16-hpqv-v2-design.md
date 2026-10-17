# hpqv v2: design and decisions

Date: 2026-10-16. Branch: `feat/secure-session-v2`.

- **Requirements:** [2026-10-16-original-requirements.md](2026-10-16-original-requirements.md).
  It covers the five features, the engineering rules and the required tests.
- **Protocol detail:** [../../PROTOCOL-v2.md](../../PROTOCOL-v2.md).
- **Phase B module contract:**
  [../plans/2026-10-16-secure-session-v2.md](../plans/2026-10-16-secure-session-v2.md).

This document records what the user decided in conversation. Where it and the
requirements differ, this document wins.

## Intent

Two people on the **same WiFi** make an end-to-end encrypted, post-quantum
hybrid voice call. It should be as easy as v1 (open the app, pick the other
person, talk), but without v1's security holes. The project is also an
evaluation artifact, so every number it reports must come from a run that
actually happened.

## Decisions (made by the user, in order)

1. **v2 is a deliberate breaking change.**
   - v1 code and results stay as they are on `experiment/cain` and in release v0.1.0-demo.1.
   - Nothing under `results/` (v1) is overwritten. v2 numbers go under `results/v2/`.
2. **Real TLS 1.3 instead of a hand-rolled handshake.**
   - Key exchange: X25519MLKEM768 (RFC 10024).
   - Mutual ML-DSA-65 certificate authentication.
   - Library: pyOpenSSL 26.4 on `cryptography` 50's bundled OpenSSL.
   - Media keys come from the TLS exporter.
   - X-Wing was validated, then dropped, because using it would have required a
     custom handshake.
3. **liboqs is removed.** Every primitive comes from `cryptography`, checked
   against NIST ACVP vectors.
4. **Work in parallel phases, and stop for the user's review** after the
   security foundation, before audio adaptation and the diagnostics GUI.
5. **No file exchange on the same WiFi:**
   - The app creates each person's identity automatically.
   - Peers find each other with a LAN broadcast, so there is no IP typing.
   - On the **first** call both screens show a short code. The two people read
     it aloud and both press "Codes match", and the app then remembers the peer
     (pin on first use, with explicit confirmation).
   - Later calls connect directly. A different key under a known name is never
     trusted silently.
   - "Call by IP" stays as a fallback. The file-based CLI flags stay for
     advanced use.

## Goals

- **Security:** every property in requirements Features 1–2, delivered with TLS
  1.3 plus the v2 datagram format, and proven by negative tests.
- **Seamless same-WiFi calling:** no files, no IP typing, one code check per new
  friend.
- **Audio resilience (Feature 3):**
  - playout driven by a clock;
  - a bounded jitter buffer;
  - FEC recovery from the *next* packet, and PLC;
  - adaptive FEC and bitrate with smoothing and hysteresis;
  - each of those switchable on its own.
- **Diagnostics (Feature 4):** a "Diagnostics" tab in the existing Tk GUI.
  Values are labelled measured or estimate, and can be reset and exported as
  CSV or JSON.
- **Simulation and benchmarks (Feature 5):**
  - a seeded, isolated UDP impairment proxy with named profiles;
  - FEC off, on and adaptive compared under identical seeds;
  - the hybrid handshake against an isolated classical-only baseline;
  - machine-readable results.

## Non-goals

- Calls across the internet or through NAT. The app is same-LAN only;
  Call-by-IP covers routed LANs.
- Video, group calls, PKI/CAs, account systems.
- POLQA or other licensed quality metrics. The objective metric stays the
  existing segmental/whole-signal SNR, plus the E-model MOS clearly labelled as
  an estimate.
- Re-establishing a call automatically after the TCP connection dies. A lost
  TCP connection ends the call by design. A short **UDP** outage is survived.

## Anti-goals (failure even if it "works")

- Any path that trusts a peer without either a pinned key or both users
  confirming the code.
- A classical-only key exchange reachable from the CLI or GUI.
- Secrets, private keys, exporter output or audio in logs, exports or crash text.
- Blocking the Tk thread or the audio path on network, handshake or a human answer.
- Claiming a benchmark number that was not produced by a run in this repo.

## Constraints

- Python 3.11 only.
- Dependencies: `cryptography` 50.x, `pyopenssl>=26.4,<27`, plus the existing
  ones (opuslib, sounddevice, numpy, matplotlib, scapy). **Nothing new.**
  Discovery uses only the standard library.
- Every UDP datagram is at most 1200 bytes. The handshake stays on TCP.
- macOS needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for opuslib.
- Tk tests run only with `HPQV_GUI_TESTS=1` or from a terminal.

## Left to the builder

Widget layout details, wording of non-security UI text, internal helper names
beyond the interfaces fixed in the plan.
