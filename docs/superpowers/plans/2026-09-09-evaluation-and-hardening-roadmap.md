# Evaluation & Hardening Roadmap (2-month window)

Sequences the work after the core prototype (which is merged on `main`).
Decisions: ADR-0002 (mutual auth), ADR-0003 (NS-3 as secondary model).
Spec: docs/superpowers/specs/2026-09-08-hybrid-pqc-voice-design.md

Three tracks. Track 1 is buildable now on macOS. Tracks 2 and 3 need a Linux
environment (Docker container). Each track gets its own detailed bite-sized TDD
plan when it starts — this roadmap is the sequence and the gates, not the
per-task steps.

## Gate 0 — Linux environment go/no-go (do first, before Tracks 2/3)

`tc netem` and NS-3 do not run on macOS. Target: a Docker Linux container.
- Verify: `docker run --rm --cap-add=NET_ADMIN ubuntu:24.04` can `apt-get install
  iproute2` and `tc qdisc add dev lo root netem loss 10% delay 50ms`.
- Deliverable: a `eval/Dockerfile` (Ubuntu + python3.11 + uv + liboqs deps +
  iproute2 + tshark) that reproduces the prototype's test env on Linux.
- If Docker netem is blocked, fall back to a Linux VM or a lab Linux box.

---

## Track 1 — Hardening (buildable now, macOS) — ADR-0002

Improves the security story the panel cares about; unblocks a protocol freeze
before evaluation.

1. **Responder authentication (mutual auth).** Responder signs the handshake
   transcript (kem_ciphertext bound to initiator kem_pub) with its Dilithium key;
   initiator verifies against pinned `peer_sig_pub` (already threaded, currently
   unused). Update `wire-format.md` ACCEPT layout: `version ‖ kem_ciphertext ‖
   sig`. Tests: initiator rejects a tampered/forged ACCEPT.
2. **Control-plane messages.** Typed control records on the TCP channel:
   `CALL_START`, `CALL_END`, `KEY_ROTATE`. Key rotation re-runs HKDF with a
   ratcheted context and resets the per-direction sequence counters. Tests: a
   rotate mid-stream re-keys both directions and old-key frames fail.

Each is one detailed plan + subagent-driven execution.

---

## Track 2 — Real-prototype evaluation (Linux) — Spec Track A, PRIMARY evidence

Gate 0 must pass first.

1. **Baselines.** `naive-PQC-over-UDP` (dump handshake in datagrams, no framing →
   fragments) and `TCP/TLS-PQC`, reusing the existing audio pipeline over
   different transports.
2. **Impairment harness.** Scripted `tc netem` sweeps (loss 0/5/10/20/30% at
   50 ms / 10 ms; latency sweep for TTFB), 5 runs/condition, median.
3. **Metrics.** Fragment count via packet capture (tshark/scapy) — the
   fragmentation-free proof (0 for hybrid, >0 for naive); TTFB; jitter; drop rate.
4. **MOS.** ITU-T E-model (G.107): delay/jitter/loss → R-factor → MOS. Shared
   module (used by Track 3 too).
5. **FEC functional proof.** Loss-recovery test: decode with/without FEC under
   loss, show the MOS/intelligibility delta (closes the deferred FEC item).
6. **Graphs.** Auto-generated loss-vs-MOS, loss-vs-fragment-count, latency-vs-TTFB.

---

## Track 3 — NS-3 model (Linux) — Spec Track B, SECONDARY — ADR-0003

Gate 0 must pass first. Largest time sink — start early in parallel with Track 2.

1. **NS-3 install** in the container (ns-3.44 or current), smoke a hello-sim.
2. **Model the three transports** as byte-size + delay: hybrid, standard TCP/TLS,
   and **pure QUIC** (the baseline the real prototype can't easily host). PQC =
   real liboqs byte sizes as payload + a real liboqs handshake-CPU benchmark
   injected as delay. Voice = CBR 20 ms-frame traffic generator.
3. **Sweeps + flow-monitor** for delay/jitter/loss; feed the SAME E-model module
   from Track 2 → MOS. Directly comparable to Track 2 numbers.
4. **Comparison graphs:** hybrid vs TCP/TLS vs QUIC across the loss/latency sweeps.

---

## Deliverables (unchanged, spec §Deliverables)

Repo + reproducible scripts (incl. `eval/Dockerfile`) + graphs from both tracks +
report + ~2-min demo video + slide deck.

## Suggested sequence

1. Gate 0 (Linux container) — quick.
2. Track 1 hardening — while Gate 0 / Docker image bakes.
3. Track 3 NS-3 install + first sim — start early (long pole).
4. Track 2 real eval — in parallel; shares the E-model + graphing with Track 3.
5. Assemble report/graphs/demo/slides.
