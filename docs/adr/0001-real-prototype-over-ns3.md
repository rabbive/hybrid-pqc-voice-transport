# 0001 - Real userspace prototype + tc netem, not NS-3 simulation

Status: Accepted
Date: 2026-09-07

## Context

The project proposal names NS-3 as the evaluation framework. But the design also
depends on real components: liboqs PQC handshake, Opus voice, Opus in-band FEC,
app-layer MTU framing. Investigation found:

- NS-3 cannot run real PQC crypto natively. Its only route to real code (DCE,
  Direct Code Execution) is stale, pinned to old NS-3 + kernel versions, fragile,
  and effectively unmaintained.
- NS-3 has no Opus codec, no voice pipeline, and no MOS computation. Voice can
  only be modeled as a fixed-rate traffic generator; MOS must be post-processed.
- The central thesis — *fragmentation-free* transport — is a property of real IP
  datagrams (Don't-Fragment bit, real MTU). Proving it needs real packets, which
  a pure simulation only models by assumption.

## Decision

Build a real userspace prototype in Python (real TCP + UDP sockets, real
liboqs-python handshake, real Opus voice with in-band FEC). Inject network
impairment (loss, latency, jitter) with Linux `tc netem` on real interfaces
(loopback for measurement, LAN for demo). Compute MOS offline via the ITU-T
E-model (G.107); optionally PESQ on decoded audio.

Use NS-3 only if the guide explicitly requires the literal tool — in which case
NS-3 becomes a *secondary* size+delay model of the handshake, not the primary
evidence.

## Consequences

- Fragmentation-free claim is proven by real packet capture (fragment count = 0
  with DF set), the strongest possible evidence.
- Real handshake gives real TTFB numbers, not modeled ones.
- Lose NS-3's clean topology control; regain it via `tc netem`, which is simpler
  and operates on the real stack.
- If the guide insists on NS-3, extra work is needed to add it as a secondary
  model — flagged as an open risk, resolve early with the guide.
