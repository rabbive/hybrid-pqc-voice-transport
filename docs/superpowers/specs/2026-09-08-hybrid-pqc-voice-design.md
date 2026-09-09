# Design: A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

Date: 2026-09-08
Team: Cain Manoj (23BCE1663), Sreenandu G (23BCE1120), Ashwanth Kumaravel (23BCE1244)
Guide: Dr. Renuka Devi S.
Related: CONTEXT.md (glossary), docs/adr/0001-real-prototype-over-ns3.md, PLAN.md

## Problem

Real-time voice needs UDP for low latency. Post-quantum crypto (PQC) keys and
signatures are large: an ML-KEM-768 public key is 1184 bytes and an ML-DSA-65
(Dilithium3) signature is 3309 bytes — a handshake blob well over the 1500-byte
Internet MTU. Sent over UDP, such a blob fragments at the IP layer; middleboxes
drop fragments, and a single lost fragment kills the whole datagram, producing
latency spikes and dropped calls. The project's claim: split the work so the
large PQC handshake rides a reliable stream and the voice rides a
fragmentation-free datagram path, and prove it beats the naive alternative.

## Goals

- A working two-peer prototype: PQC-secured voice call over a hybrid TCP+UDP transport.
- Central thesis, proven by packet capture: **zero IP fragmentation** on our path
  where naive-PQC-over-UDP fragments and drops.
- Supporting evidence: MOS, TTFB, jitter, and drop-rate graphs versus baselines
  under controlled network impairment.

## Non-Goals

- Not production software; not a hardened or audited implementation.
- Not an NS-3 simulation as primary evidence (see ADR-0001). NS-3 only if the
  guide mandates the literal tool, and then only as a secondary size+delay model.
- No CA hierarchy, no codec renegotiation, no congestion control beyond what TCP
  already provides on the control channel.

## Architecture

Two peers connect over two parallel channels.

### Control Channel (TCP)
Reliable, stream-oriented. Carries:
1. The one-time PQC handshake.
2. Call setup and teardown signaling.
3. One key-rotation message (rotate the symmetric voice key mid-call).

Because TCP is a byte stream, oversized PQC blobs never fragment at the datagram
level — TCP segments them transparently. This is why the handshake belongs here.

### Data Channel (UDP)
Unreliable by choice. Carries encrypted Opus voice frames. A lost frame is
skipped or FEC-recovered, never retransmitted (retransmission is too slow for
real-time). Every datagram is kept under a conservative MTU cap (~1200 bytes)
with the IP Don't-Fragment (DF) bit set.

## Components (isolation seams)

Each component has one purpose, a defined interface, and its own tests. The
shared contract between them is the wire-format document (Phase 0).

- **handshake** — Kyber (ML-KEM) encaps/decaps, Dilithium (ML-DSA) sign/verify
  via liboqs-python, self-signed Dilithium cert, HKDF. Input: TCP socket + pinned
  peer root key. Output: 32-byte shared secret + nonce prefix. Owner: Ashwanth.
- **transport** — UDP framing (≤1200 B, DF set), packet header (version, type,
  sequence, timestamp, flags), ChaCha20-Poly1305 AEAD with seq-derived nonce,
  fixed 60 ms jitter buffer (reorder + drop-late). Input: shared secret + byte
  frames. Output: delivered byte frames in order. Owner: Cain.
- **audio** — Opus encode/decode with in-band FEC enabled; input source is a
  swappable flag (pre-recorded WAV for measurement, live mic for demo). Input:
  PCM or WAV. Output: Opus bytes / PCM. Owner: Sreenandu.
- **baselines** — naive-PQC-over-UDP (whole handshake dumped in datagrams, no
  app-layer framing → fragments) and TCP/TLS-PQC. Reuse the audio pipeline,
  different transport. Owners: Ashwanth + Cain.
- **eval** — tc netem impairment harness, packet capture (tshark/scapy) for
  fragment count, jitter/drop/TTFB collection, ITU-T E-model (G.107) → MOS,
  graph generation. Owner: Sreenandu.

## Data Flow

1. Peers open the TCP control channel; run the handshake once. One peer sends a
   Dilithium-signed Kyber public key plus a self-signed Dilithium cert; the other
   verifies against the pinned root, encapsulates, returns the ciphertext. Both
   run HKDF over the Kyber shared secret to derive: a 32-byte voice key and a
   4-byte nonce prefix.
2. Control channel sends a "call start" message.
3. Per 20 ms frame: capture (mic or WAV) → Opus encode with in-band FEC → prepend
   8-byte sequence counter, build ChaCha20-Poly1305 nonce (4-byte prefix + seq) →
   encrypt → wrap in UDP packet header, verify size ≤1200 B, set DF → send.
4. Receiver: reorder in the 60 ms jitter buffer, drop late/duplicate, decrypt,
   Opus decode (FEC fills isolated gaps) → speaker or WAV.
5. Optional mid-call: control channel sends key-rotation; both derive the next
   voice key; sequence counter resets under the new key.
6. "Call end" teardown on the control channel.

## Wire Formats (contract — finalized in Phase 0)

- **Handshake messages:** typed, length-prefixed records over TCP. Fields: version,
  message type, Kyber public key, Dilithium cert, Dilithium signature, Kyber
  ciphertext. Exact byte layout agreed by all three owners before Phase 1.
- **Voice datagram:** fixed header (version, type, 8-byte sequence, timestamp,
  flags incl. FEC) followed by ChaCha20-Poly1305 ciphertext + 16-byte tag. Total
  ≤1200 bytes; nonce never reused (derived from prefix + monotonic sequence).

## Error Handling

- Lost voice frame: skipped or reconstructed by Opus in-band FEC; never
  retransmitted.
- Nonce discipline: sequence counter is monotonic per key; key rotation resets it.
  Never reuse a (key, nonce) pair.
- Oversized datagram: DF bit set, so an accidental >MTU packet errors loudly (ICMP
  "fragmentation needed" / send error) instead of silently fragmenting. This is
  the enforcement mechanism behind the fragmentation-free claim.
- Control-channel loss/reordering: handled by TCP.
- Handshake auth failure (bad signature / unpinned cert): abort the connection.

## Testing & Evaluation

### Unit self-checks (pytest, per component)
- handshake: two processes complete the handshake and derive an identical shared
  secret; a tampered signature aborts.
- transport: encrypt→decrypt round-trip; an oversized frame raises; jitter buffer
  reorders and drops late frames correctly.
- audio: WAV → Opus encode → decode round-trip produces sane output.

### System evaluation — two corroborating tracks

**Track A — real prototype (PRIMARY evidence).** The real handshake + Opus voice,
run on a Linux environment (Docker container on the macOS dev machine, or any
Linux box — `tc netem` does not run on macOS).
- Baselines: hybrid vs naive-PQC-over-UDP vs TCP/TLS-PQC.
- Impairment via `tc netem`: primary sweep on packet loss (0, 5, 10, 20, 30%) at
  fixed 50 ms latency and 10 ms jitter; secondary latency sweep for TTFB. 5 runs
  per condition, report median.
- Metrics: fragment count from packet capture (target 0 for hybrid, >0 for
  naive — the fragmentation-free proof), MOS via E-model, TTFB, jitter, drop
  rate. This track also proves Opus FEC functionally (loss-recovery).

**Track B — NS-3 model (SECONDARY, for scale + panel expectation, see ADR-0003).**
PQC modelled as measured handshake byte volume (real liboqs sizes) + measured
handshake CPU time (real liboqs benchmark) injected as delay; voice as a
constant-bit-rate 20 ms-frame traffic generator. Runs in the same Linux
container.
- Baselines the real prototype can't easily host: standard TCP/TLS and **pure
  QUIC**, alongside the hybrid model.
- Same impairment sweeps; stats via `flow-monitor` (delay/jitter/loss).
- MOS via the SAME ITU-T E-model (G.107) as Track A, so the two tracks are
  directly comparable.

Both tracks share one E-model MOS pipeline and one graphing step, so results
line up side by side in the report.

## Tooling

Python 3.11+, `uv` for dependencies, single git repo, `pytest`. Libraries:
liboqs-python, cryptography (ChaCha20-Poly1305, HKDF), opuslib/PyOgg, sounddevice,
numpy, matplotlib, scapy/tshark for capture.

## Deliverables

Code repo + reproducible experiment scripts + graphs + written report + a ~2-minute
live-demo video (mic call, toggle loss live, hear FEC vs the naive path) + defense
slide deck.

## Risks & Open Questions

- **NS-3 scope — RESOLVED (2026-09-09):** NS-3 is in scope as the secondary
  evaluation model (ADR-0003), because the panel expects it and the ~2-month
  window allows it. The real prototype + `tc netem` remains the primary evidence.
- **Linux environment is required for all evaluation** (`tc netem` and NS-3 do
  not run on macOS). Target: a Docker Linux container on the dev machine. This is
  the evaluation phase's go/no-go — verify `tc netem` works in the container
  (needs `--cap-add=NET_ADMIN`) before building the harness.
- **NS-3 build/learning curve** is the largest time sink of the evaluation phase;
  schedule it first within Track B.
- liboqs-python build/install friction across the team's machines — validated on
  the primary dev machine; re-check on the other two setups.
- Real-time audio timing under Python's scheduler — the 20 ms cadence may need
  care; if Python jitter dominates, revisit buffer sizing before blaming the network.

## Follow-on scope (post core-prototype)

- **Hardening (ADR-0002):** responder authentication (mutual auth) + control-plane
  messages (call setup/teardown, key rotation). Buildable on the existing code.
- **Evaluation (Tracks A + B above):** requires the Linux environment.

## Stretch (only after core lands)

Reed-Solomon burst FEC · PESQ MOS on decoded audio · adaptive jitter buffer ·
active PMTU discovery · QUIC-PQC baseline · NS-3 secondary model (if mandated).

## Phasing

See PLAN.md for the 8-phase build order (Phase 0 wire-format contract → Phase 7
deliverables), each phase with its runnable check.
