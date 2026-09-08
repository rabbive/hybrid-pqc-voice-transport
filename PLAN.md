# Build Plan — Hybrid PQC Voice Transport

See CONTEXT.md for vocabulary, docs/adr/0001 for the prototype-vs-NS-3 decision.

## Settled decisions
- Real Python prototype, real sockets. `tc netem` for impairment. NS-3 only if guide demands.
- Thesis: fragmentation-free (DF bit, ~1200 B cap). Proof = packet capture, 0 fragments.
- Handshake: custom minimal, Dilithium-signed Kyber, self-signed Dilithium cert in handshake.
- Voice cipher: ChaCha20-Poly1305, nonce = 4B handshake prefix + 8B seq counter.
- Control channel: handshake + call setup/teardown + one key-rotation msg.
- Jitter buffer: fixed 60 ms, exposed as a tunable knob.
- FEC: Opus in-band (core). Reed-Solomon = stretch.
- Voice: pre-recorded WAV for measurement, live mic for demo. Same pipeline, swap input.
- Baselines: hybrid vs naive-PQC-over-UDP vs TCP/TLS-PQC. QUIC = stretch.
- MOS: ITU-T E-model primary. PESQ = stretch.
- Experiment: sweep loss 0/5/10/20/30% at latency 50ms jitter 10ms, 5 runs, median.
  Secondary latency sweep for TTFB.
- Tooling: Python 3.11+, uv, single repo, pytest. Libs: liboqs-python, cryptography,
  opuslib/PyOgg, sounddevice, numpy, matplotlib.

## Team (swap by interest)
- **Crypto/Control** — Ashwanth: handshake, Kyber/Dilithium, HKDF, key rotation, TCP channel.
- **Transport/Data** — Cain: UDP framing, MTU+DF, packet format, jitter buffer, voice cipher.
- **Audio/Eval** — Sreenandu: Opus capture/encode/FEC, tc netem harness, metrics, graphs.
- Shared contract = the packet + handshake format doc (Phase 0). Nail it before splitting.

## Phases (each ends with a runnable check)

### Phase 0 — Skeleton + wire-format contract (all, week 1)
Repo, uv, format doc: handshake message layout + UDP packet layout (header fields,
nonce derivation, byte order). This is the interface between the three owners.
Check: format doc reviewed + agreed by all three.

### Phase 1 — Handshake over TCP (Crypto)
Kyber encaps/decaps + Dilithium sign/verify via liboqs-python. Self-signed cert.
HKDF → shared secret. Runs peer-to-peer over real TCP sockets.
Check: pytest — two processes complete handshake, derive identical shared secret.

### Phase 2 — Voice datagram over UDP (Transport)
Frame → ≤1200 B, DF bit set, seq number, ChaCha20-Poly1305 with derived key.
Fixed 60 ms jitter buffer, reorder + drop-late on receive.
Check: pytest — encrypt/decrypt round-trip; oversized frame raises (DF proves no frag).

### Phase 3 — Audio pipeline (Audio/Eval)
WAV/mic → Opus encode (in-band FEC on) → bytes; bytes → Opus decode → speaker/WAV.
Input source is a swappable flag (file vs mic).
Check: encode→decode a WAV, assert output length/format sane.

### Phase 4 — Integration (all)
Wire Phase 1 shared secret into Phase 2 cipher; Phase 3 audio through Phase 2 transport.
End-to-end: two processes, real handshake, live/file voice both directions.
Check: localhost call plays intelligible audio end to end.

### Phase 5 — Baselines (Crypto + Transport)
naive-PQC-over-UDP (dump handshake in datagrams, no framing → fragments) and
TCP/TLS-PQC path. Same audio pipeline, different transport.
Check: packet capture shows naive-UDP fragments; hybrid does not.

### Phase 6 — Eval harness (Audio/Eval)
tc netem sweep script, capture frag count (tshark/scapy), jitter, drop, TTFB;
E-model → MOS. Auto-generate graphs. 5 runs/condition, median.
Check: one full sweep produces the loss-vs-MOS graph.

### Phase 7 — Deliverables (all)
Report, graphs, 2-min demo video (mic call, toggle loss, hear FEC), slide deck.

## Stretch (only if core lands early)
Reed-Solomon burst FEC · PESQ MOS · adaptive jitter buffer · active PMTUD ·
QUIC-PQC baseline · NS-3 secondary model (if guide demands).

## Open risk
Confirm with guide (Dr. Renuka Devi) whether NS-3 is mandatory or "rigorous eval"
suffices. Resolve in week 1 — it is the only thing that reshapes the plan.
