# 0003 - Reinstate NS-3 as a secondary evaluation model

Status: Accepted (amends 0001)
Date: 2026-09-09

## Context

ADR-0001 chose a real userspace prototype + `tc netem` over NS-3, because NS-3
cannot run real PQC crypto and has no Opus/MOS support. That reasoning still
holds for *primary* evidence. But the evaluation panel expects an NS-3 study
(the original proposal named NS-3), and the project has a ~2-month window that
makes a secondary NS-3 track feasible and mark-worthy.

## Decision

Keep the real prototype + `tc netem` as the PRIMARY evidence (real handshake,
real fragmentation-free proof by packet capture, real MOS from decoded audio).
Add NS-3 as a SECONDARY, complementary model that provides what the real
prototype cannot: controlled, repeatable, at-scale topology sweeps and a
head-to-head against a full-TCP transport arrangement and a QUIC (UDP+1RTT)
arrangement. Note: all three modelled schemes (hybrid / tcp / quic) carry the
SAME post-quantum handshake material (real liboqs byte sizes + CPU cost) — they
differ only in TRANSPORT (where the handshake and voice ride), not in the
cryptographic scheme. This isolates the transport effect, which is the project's
subject; it is not a classical-TLS-vs-PQC crypto comparison.

In NS-3, PQC is modelled — not executed — as measured handshake byte volume
(from the real liboqs sizes) plus measured handshake CPU time (from a real
liboqs benchmark) injected as delay. Voice is a constant-bit-rate traffic
generator matching Opus (20 ms frames). MOS is computed offline from the
simulator's delay/jitter/loss via the ITU-T E-model (G.107) — the same E-model
used for the real-prototype numbers, so the two tracks are directly comparable.

The Linux toolchain (NS-3, `tc netem`) runs in a Docker Linux container on the
macOS dev machine (or any Linux box); it does not run natively on macOS.

## Consequences

- Two evaluation tracks that corroborate each other: real prototype for realism
  and the fragmentation-free proof, NS-3 for scale, reproducibility, and the
  QUIC/TLS baselines the panel wants.
- The E-model is the shared bridge — same MOS pipeline both sides.
- New dependency: a Linux environment (Docker container) for both `tc netem` and
  NS-3. This is the evaluation phase's go/no-go, verified before building.
- NS-3 has a real learning/build-time cost; it is the largest single time sink of
  the evaluation phase and is scheduled accordingly.
