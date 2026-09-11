# Evaluation Report — Hybrid TCP/UDP Post-Quantum Voice Transport

**Project:** A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat
**Team:** Cain Manoj (23BCE1663), Sreenandu G (23BCE1120), Ashwanth Kumaravel (23BCE1244)
**Guide:** Dr. Renuka Devi S.
**Last updated:** 2026-09-11

This document explains **everything we test, how, and what the numbers mean** — written so
you can pick it up months later and understand the whole evaluation without re-deriving it.

---

## 1. What the project claims

Post-quantum crypto keys are large. Our handshake carries **10,842 bytes** of PQC material:

| Message | Contents | Bytes |
|---|---|---|
| HELLO | ML-KEM-768 public key (1184) + ML-DSA-65 public key (1952) + signature (3309) | 6,445 |
| ACCEPT | ML-KEM-768 ciphertext (1088) + responder signature (3309) | 4,397 |
| **Total** | | **10,842** |

That is **7× the 1500-byte Internet MTU**. Sent naively over UDP it is chopped into IP
fragments; middleboxes drop fragments and a single lost fragment destroys the whole datagram.

**Our claim:** put the big handshake on a reliable TCP *control channel* and the voice on a
*data channel* of UDP datagrams each capped at 1200 bytes with the Don't-Fragment bit set,
so **nothing ever fragments** — while keeping voice latency low.

---

## 2. The three layers of testing

### Layer 1 — Unit tests (44 tests)
Each module has tests that fail if its logic breaks. Run with `pytest`.

| Test file | Tests | What it protects |
|---|---|---|
| `test_handshake.py` | 4 | Both peers derive the same key; mutual auth — a **forged** ACCEPT and a **tampered** ciphertext are both rejected |
| `test_control.py` | 3 | Handshake completes over real TCP; control messages round-trip |
| `test_session.py` | 2 | Key rotation keeps peers in sync and actually changes the key |
| `test_packet.py` | 3 | AEAD seal/open round-trip; **oversized datagram is refused**; bad header version rejected |
| `test_jitter.py` | 2 | Frames reordered, late/duplicate dropped, buffer fully drained |
| `test_audio.py` | 1 | Opus encode→decode round-trip at 48 kHz / 20 ms frames |
| `test_udp.py` | 1 | UDP socket helper works (DF bit set on Linux) |
| `test_call.py` | 2 | **End-to-end call** over localhost; the two directions use **different nonce prefixes** |
| `eval/test_mos.py` | 5 | E-model MOS is monotonic in loss and delay, clamped, continuous at the G.107 knee |
| `eval/test_stats.py` | 4 | Drop rate, jitter, TTFB maths |
| `eval/test_capture.py` | 2 | Fragment counter finds fragments in a crafted pcap, and reports 0 when there are none |
| `eval/test_baselines.py` | 2 | The naive baseline **really does fragment**; TCP-voice round-trips |
| `eval/test_netem.py` | 1 | Network impairment applies and is cleaned up |
| `eval/test_runner.py` | 3 | Scenario runner produces sane metrics |
| `eval/test_pqc_cost.py` | 3 | Real liboqs byte sizes and timing |
| `eval/test_ns3_smoke.py` | 2 | The ns-3 simulation compiles and behaves under loss |
| `eval/test_graphs.py`, `test_run_*_smoke.py` | 3 | Figures and sweep drivers produce real output |

**Status:** 44 tests. In the Linux container all 44 pass. On macOS, 32 pass and 12 skip
(they need `tc`/`tshark`/ns-3, which only exist in the container — they skip cleanly, never fail).

### Layer 2 — Track 2: real-prototype measurement (PRIMARY evidence)
The actual code — real PQC handshake, real Opus audio, real sockets — measured under
controlled network impairment (`tc netem`). This is the evidence that counts.

### Layer 3 — Track 3: NS-3 simulation (SECONDARY, corroborating)
A model where PQC is represented by its *real* byte size and *real* CPU cost, used to
compare transport arrangements — including QUIC, which the real prototype can't easily host.
See `docs/adr/0003-*` for why this is secondary, and `ns3/README.md` for the model's limits.

---

## 3. Test environment (and why it's a container)

`tc netem` (network impairment) and ns-3 do **not** run on macOS. Everything evaluation-related
runs in a reproducible Ubuntu 24.04 container defined by `eval/Dockerfile`, which contains:
Python 3.11 (via uv), liboqs (built for ML-KEM-768 + ML-DSA-65 only), libopus, `iproute2`
(`tc`), `tshark`, and ns-3 3.41.

> **Critical detail:** Docker's loopback MTU defaults to **65536**, so *nothing fragments* there.
> Every experiment pins `lo` to **MTU 1500** for its duration (restored afterwards). Without this
> pin the fragmentation comparison silently measures nothing.

---

## 4. Track 2 — real prototype

### Method
Three transports are compared:

| Scenario | Handshake | Voice | Role |
|---|---|---|---|
| `hybrid` | TCP, app-framed ≤1200 B | UDP, ≤1200 B, DF set | **our design** |
| `naive` | one oversized UDP datagram, no framing | UDP | the villain — fragments |
| `tcp` | TCP | TCP | reliable but latency-prone |

Impairment via `tc netem`; packets captured with `tshark` and fragments counted by the filter
`ip.flags.mf==1 || ip.frag_offset>0`. Primary sweep: loss 0/5/10/20/30 % at 50 ms delay,
10 ms jitter, **median of 5 runs**. Secondary sweep: delay 20/50/100/200 ms at 0 % loss.
MOS is computed from measured delay/jitter/loss with the ITU-T G.107 E-model.

### Results — loss sweep (`results/eval.csv`)

<!-- BEGIN GENERATED: track2-loss -->
| loss | hybrid frags | naive frags | tcp frags | hybrid MOS | naive MOS | tcp MOS |
|---|---|---|---|---|---|---|
| 0 % | **0** | 5 | 0 | 4.35 | 4.35 | 4.35 |
| 5 % | **0** | 5 | 0 | 3.54 | 3.54 | 4.35 |
| 10 % | **0** | 5 | 0 | 3.17 | 3.06 | 4.35 |
| 20 % | **0** | 4 | 0 | 2.18 | 2.08 | 4.35 |
| 30 % | **0** | 4 | 0 | 1.90 | 1.90 | 4.35 |
<!-- END GENERATED: track2-loss -->

TTFB (full values in `results/eval.csv`): the hybrid's stays flat across the loss sweep, while
tcp's is consistently higher and noisier — it pays for retransmissions during setup.

### Results — latency sweep (`results/eval_latency.csv`)

<!-- BEGIN GENERATED: track2-latency -->
| link delay | hybrid TTFB | naive TTFB | tcp TTFB |
|---|---|---|---|
| 20 ms | 13.6 ms | 14.4 ms | 23.6 ms |
| 50 ms | 45.2 ms | 45.3 ms | 48.9 ms |
| 100 ms | 98.1 ms | 100.3 ms | 106.0 ms |
| 200 ms | 196.8 ms | 199.8 ms | 205.1 ms |
<!-- END GENERATED: track2-latency -->

### How to read this
1. **The fragmentation claim is proven.** Hybrid fragments **zero** packets at every loss level;
   naive always fragments. This is from real packet capture, not a model.
2. **TCP is reliable but slow to start.** It never loses voice — its MOS is flat across the whole
   sweep — but its setup cost is consistently the highest of the three, at every loss level and
   every RTT.
3. **Voice quality degrades with loss as expected** for the UDP-based paths.

### Honest caveat you must be able to answer
Naive's MOS is *not* meaningfully worse than hybrid's — across the sweep above the two track each
other closely, and depending on the run either can come out ahead at a given loss level. The
differences are within run-to-run noise.

**Why:** fragmentation affects the **handshake**, not the voice stream. Voice frames (~160 B of
Opus) never fragment in either design; only the 10.8 KB handshake does. And on a local link the
kernel reassembles fragments reliably, so fragmentation never converts into a quality penalty here.
The fragment count proves the *structural* property; Section 6 measures its *consequence*.

---

## 5. Track 3 — NS-3 simulation

### Method
Two nodes on a point-to-point link (10 Mbps, configurable delay, `RateErrorModel` for loss).
The handshake is modelled as a transfer of the **real** 10,842 bytes plus the **real** measured
PQC CPU cost (≈0.34 ms, from a liboqs benchmark). Voice is a 20 ms-frame CBR stream. Three
arrangements: `hybrid` (TCP handshake + UDP voice), `tcp` (all TCP), `quic` (UDP 1-RTT
handshake + UDP voice). Metrics from FlowMonitor; MOS via the **same** E-model as Track 2.

### Results — loss sweep at 50 ms (`results/ns3_eval.csv`)

<!-- BEGIN GENERATED: ns3-loss -->
| loss | hybrid (loss / TTFB / MOS) | tcp (loss / MOS) | quic (loss / TTFB / MOS) |
|---|---|---|---|
| 0 % | 0.0 % / 131 ms / 4.38 | 0.0 % / 4.38 | 0.0 % / 35 ms / 4.38 |
| 5 % | 4.6 % / 179 ms / 3.78 | 0.0 % / 4.38 | 4.8 % / 35 ms / 3.75 |
| 10 % | 10.0 % / 180 ms / 3.11 | 0.0 % / 4.38 | 10.2 % / 35 ms / 3.09 |
| 20 % | 20.6 % / 179 ms / 2.26 | 0.0 % / 4.38 | 20.0 % / 227 ms / 2.29 |
| 30 % | 27.7 % / 182 ms / 1.92 | 0.0 % / 4.38 | 27.3 % / 227 ms / 1.94 |
<!-- END GENERATED: ns3-loss -->

### Results — latency sweep (`results/ns3_latency.csv`)
TTFB at 0 % loss:

<!-- BEGIN GENERATED: ns3-latency -->
| link delay | hybrid TTFB | tcp TTFB | quic TTFB |
|---|---|---|---|
| 20 ms | 56 ms | 56 ms | 20 ms |
| 50 ms | 131 ms | 131 ms | 35 ms |
| 100 ms | 256 ms | 256 ms | 60 ms |
| 200 ms | 506 ms | 506 ms | 110 ms |
<!-- END GENERATED: ns3-latency -->

QUIC's 1-RTT setup needs roughly *half* the round-trips of a TCP-based handshake, so its TTFB
grows far more slowly with link delay.

### How to read this
- QUIC's UDP 1-RTT handshake is the **fastest to first byte** when the link is clean, but its
  retries make it the slowest under heavy loss (see the table above).
- TCP never drops voice, but see the limitation below before calling it "best".
- Hybrid sits where it should: TCP-grade reliable setup, UDP-grade voice latency.

### Documented model limitations (state these; details in `ns3/README.md`)
1. **QUIC is a proxy** — ns-3 has no official QUIC module; we model UDP + 1-RTT, not real
   congestion control or 0-RTT.
2. **TCP's head-of-line latency is understated** — FlowMonitor timestamps each retransmitted
   segment as a fresh packet, so TCP's MOS stays ~4.38 even at 30 % loss. Read that as
   *"TCP does not drop voice frames"*, **not** *"TCP is good for real-time voice."* The real
   per-frame latency penalty is what Track 2 measures.
3. **Jitter ≈ 0** — one flow on a constant-delay link has no queueing variance. Real jitter is
   in the Track 2 numbers.

---

## 6. Handshake survival under loss

Sections 4–5 show hybrid never fragments. This section shows **what that is worth**. Full
write-up: `docs/EXPERIMENT-handshake-survival.md`.

Fragmentation does not hurt the voice stream (voice frames are small and never fragment) — it
hurts the **handshake**, which is 10,842 bytes and splits into **8 IP fragments** under the naive
design. Losing any one fragment destroys the whole datagram. We attempted 100 handshakes per
condition:

<!-- BEGIN GENERATED: survival -->
| packet loss | naive success (95% CI) | theory `(1-p)^8` | hybrid success (95% CI) | hybrid median time |
|---|---|---|---|---|
| 0 % | **100 %** (96–100) | 100 % | **100 %** (96–100) | 0.1 ms |
| 5 % | **75 %** (66–82) | 66 % | **100 %** (96–100) | 0.2 ms |
| 10 % | **39 %** (30–49) | 43 % | **100 %** (96–100) | 0.3 ms |
| 20 % | **19 %** (13–28) | 17 % | **99 %** (95–100) | 420.8 ms |
| 30 % | **2 %** (1–7) | 6 % | **83 %** (74–89) | 1019.6 ms |
<!-- END GENERATED: survival -->

**Once loss reaches 20 %, only a small minority of naive handshakes complete, while the hybrid
still completes nearly all of them.** The hybrid pays for this in time rather than failure: its
median handshake climbs into the hundreds of milliseconds and then past a second as TCP
retransmits, and at the highest loss level it too starts failing a fraction of the time — the
claim is a large margin, not invulnerability. Measurement tracks the `(1-p)^8` prediction,
confirming the mechanism is fragment-loss amplification. Read the confidence intervals rather
than the point values; at n=100 a cell moves several points between runs without meaning
anything.

This is the answer to *"if the MOS is the same, why does fragmentation matter?"* — because the
naive call does not connect at all.

---

## 7. Reproducing everything

```bash
# Build the evaluation container (once)
docker build -t hpqv-eval ./eval

# Full unit-test suite (all 44 pass here)
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run pytest -q'

# Track 2 — real prototype sweeps (5-run medians; takes several minutes)
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run python scripts/run_eval.py'

# Track 3 — NS-3 sweeps
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run python scripts/run_ns3.py'
```

After re-running any sweep, regenerate the tables in this document so they match the new data:

```bash
python scripts/render_tables.py          # rewrite the generated tables
python scripts/render_tables.py --check  # CI/test mode: non-zero exit if stale
```

Every result table here sits between `<!-- BEGIN GENERATED: ... -->` markers and is produced from
`results/*.csv` — do not edit them by hand. `tests/test_docs_current.py` fails if a table drifts
out of sync with the committed data, which is how we stop the report quietly contradicting its
own numbers.

Outputs land in `results/`: `eval.csv`, `eval_latency.csv`, `ns3_eval.csv`, `ns3_latency.csv`,
and the figures `loss_vs_mos.png`, `loss_vs_fragments.png`, `latency_vs_ttfb.png`,
`ns3_loss_vs_mos.png`, `ns3_latency_vs_ttfb.png`.

On macOS you can run only the pure-logic tests: `uv run pytest -q` (12 container-only tests skip).
Note macOS needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for opuslib.

---

## 8. Metric glossary

| Metric | Meaning |
|---|---|
| **Fragment count** | IP datagrams with the More-Fragments flag set or a non-zero fragment offset, counted from a packet capture. **0 is the goal.** |
| **MOS** | Mean Opinion Score, 1–5. Perceived call quality, computed from delay/jitter/loss with the ITU-T G.107 E-model (not human listeners). ≥4 good, ~3 fair, <2.5 poor. |
| **TTFB** | Time To First Byte — how long from connection start until voice can flow. Dominated by handshake cost. |
| **Jitter** | Variation in frame arrival spacing (ms). High jitter forces a bigger buffer, which adds delay. |
| **Drop rate / voice loss** | Fraction of voice frames that never arrived. Measured at the application layer. |
