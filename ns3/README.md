# NS-3 secondary model (Track 3)

`hpqv_sim.cc` is a **size+delay model** of three transports over a lossy,
delayed point-to-point link. It corroborates the real-prototype evaluation
(Track 2) and adds the QUIC comparison the real prototype can't easily host.
See ADR-0003 for why NS-3 is secondary, not primary, evidence.

## What it models
- Two nodes, a PointToPoint link (configurable one-way delay = `delayMs/2`), and
  a `RateErrorModel` on the receiver that drops a `loss` fraction of packets.
- **Handshake** as a one-time transfer of the *real* PQC byte volume
  (`hsBytes` = 10842 B, from `pqc_cost.handshake_bytes`) plus the *real* PQC CPU
  cost (`hsMs`, from a liboqs benchmark). PQC is modelled, never executed.
- **Voice** as a 20 ms-frame CBR stream after the handshake.
- Schemes: `hybrid` (TCP handshake + UDP voice), `tcp` (all TCP),
  `quic` (UDP+1RTT handshake + UDP voice).
- Metrics per run: TTFB, voice one-way delay, jitter, and app-level voice loss;
  MOS via the shared ITU-T E-model (`hpqv.eval.mos`).

## Run it
```
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && DYLD_FALLBACK_LIBRARY_PATH= uv run python scripts/run_ns3.py'
```
→ `results/ns3_eval.csv`, `results/ns3_latency.csv`, and the comparison PNGs.

## Documented limitations (state these in the report/defense)

1. **QUIC is a proxy, not real QUIC.** NS-3 has no official QUIC module, so QUIC
   is modelled as UDP voice with a 1-RTT (UDP-burst) handshake. It captures
   QUIC's UDP-transport loss behaviour, not its congestion control or 0-RTT.

2. **TCP's head-of-line latency is understated.** Voice loss is measured at the
   application layer (delivered bytes / sent bytes), so `tcp` correctly shows
   ~0 loss — TCP is reliable. But FlowMonitor timestamps each retransmitted
   segment as a fresh packet, so the `voice_delay_ms` metric does **not** capture
   the head-of-line blocking / retransmit wait that makes TCP poor for real-time
   voice. Consequence: `tcp` MOS stays high (~4.4) even at 20% loss in this model.
   Read it as "TCP does not *drop* voice frames" — NOT as "TCP is good for
   real-time voice." The real per-frame latency/jitter penalty is measured on the
   real prototype in Track 2, and TCP's setup cost shows here as elevated TTFB.

3. **Jitter ≈ 0.** A single flow over a constant-delay link with no cross-traffic
   produces no queueing variance, so simulated jitter is ~0. NS-3 MOS here is
   driven by loss and delay; real jitter is in the Track 2 measurements.

## How to read the comparison
- **loss vs MOS:** UDP-based paths (`hybrid`, `quic`) drop frames under loss and
  their MOS falls; `tcp` does not drop (see limitation 2 for the caveat).
- **latency vs TTFB:** the full-TCP path pays more handshake round-trips; the
  hybrid keeps voice on a low-latency UDP path while its handshake rides TCP.
- The hybrid's structural win — reliable, fragmentation-free handshake (Track 2)
  **plus** low-latency UDP voice — is the point the two tracks make together.
