# Evaluation Report — Hybrid TCP/UDP Post-Quantum Voice Transport

**Project:** A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat
**Team:** Cain Manoj (23BCE1663), Sreenandu G (23BCE1120), Ashwanth Kumaravel (23BCE1244)
**Guide:** Dr. Renuka Devi S.
**Last updated:** 2026-10-16 (sections 7–12 added for the final review)

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

(Each message also carries a 1-byte version, so the wire payload is HELLO 6,446 B + ACCEPT 4,398 B = 10,844 B. The key-material figure is used in prose; the GUI panel and `results/full_handshake.csv` report the wire figure.)

That is **7× the 1500-byte Internet MTU**. Sent naively over UDP it is chopped into IP
fragments; middleboxes drop fragments and a single lost fragment destroys the whole datagram.

**Our claim:** put the big handshake on a reliable TCP *control channel* and the voice on a
*data channel* of UDP datagrams each capped at 1200 bytes with the Don't-Fragment bit set,
so **nothing ever fragments** — while keeping voice latency low.

---

## 2. The three layers of testing

### Layer 1 — Unit and integration tests (211 collected)
Each module has tests that fail if its logic breaks. Run with `pytest`.

| Test file(s) | Tests | What it protects |
|---|---|---|
| `test_handshake.py` | 4 | Both peers derive the same key; mutual auth — a **forged** ACCEPT and a **tampered** ciphertext are both rejected |
| `test_control.py` | 3 | Handshake completes over real TCP; control messages round-trip |
| `test_session.py` | 2 | Key rotation keeps peers in sync and actually changes the key |
| `test_packet.py` | 3 | AEAD seal/open round-trip; **oversized datagram is refused**; bad header version rejected |
| `test_jitter.py` | 2 | Frames reordered, late/duplicate dropped, buffer fully drained |
| `test_audio.py` | 2 | Opus round-trip at 48 kHz / 20 ms; **the encoder's FEC and packet-loss settings read back as set** (the Apple Silicon regression, §8) |
| `test_udp.py`, `test_call.py` | 3 | UDP helper; **end-to-end call** over localhost with **different nonce prefixes** per direction |
| `test_playout.py` | 24 | Live repair: FEC rebuild from the next packet, PLC for burst interiors, silence mode, late/duplicate drop, LBRR detection, bounded gap, jitter-depth change while running |
| `test_keyring.py`, `test_callctl.py` | 11 + 7 | Live key rotation and call end (§9): in-flight old-generation packets decrypt, retired keys fail, out-of-sequence ROTATE ignored, simultaneous presses rotate once, hang-up and TCP close end the peer |
| `test_controls.py`, `test_impair.py`, `test_loss_control.py` | 4 + 7 + 4 | Knob clamping; random and burst loss hit their targets; delay line reorders under jitter; a mid-call loss change takes effect in the sender |
| `test_telemetry.py` | 15 | Receiver-side loss/residual/MOS, RFC 3550 jitter, segments, export files, WAV tee, bounded speaker backlog, handshake size/timing (§10) |
| `test_gui_logic.py`, `test_gui_graph.py`, `test_gui_shell.py` | 29 + 17 + 19 | Presets, slider mapping, tile text, graph scaling, poll loop and close handling, rotate-button gating, export off the UI thread — all without a window |
| `test_gui_window.py` | 9 | Builds the real Tk window; **skipped unless run from a terminal or with `HPQV_GUI_TESTS=1`** |
| `test_demo_identity.py`, `test_smoke.py` | 3 | Identity file handling; the package imports |
| `test_docs_current.py` | 1 | The generated tables in these docs still match `results/*.csv` |
| `eval/test_mos.py`, `test_stats.py` | 5 + 4 | E-model MOS is monotonic in loss and delay, clamped, continuous at the G.107 knee; drop rate, jitter, TTFB maths |
| `eval/test_capture.py`, `test_baselines.py`, `test_netem.py`, `test_runner.py` | 2 + 2 + 1 + 3 | Fragment counter; the naive baseline **really does fragment**; netem applies and is cleaned up; scenario runner — *container only* |
| `eval/test_pqc_cost.py`, `test_ns3_smoke.py`, `test_graphs.py`, `test_run_*_smoke.py` | 3 + 2 + 1 + 2 | Real liboqs sizes and timing; ns-3 compiles and behaves under loss; figures and sweep drivers produce output |
| `eval/test_fec_recovery.py` | 3 | Opus in-band FEC **actually recovers** lost frames; quality degrades monotonically with loss |
| `eval/test_handshake_survival.py` | 3 | A fragmented handshake fails under loss where the hybrid survives — *container only* |
| `eval/test_full_handshake.py` | 11 | The full-handshake experiment: sizes, common deadline, retry reuses the ACCEPT, deadline expiry joins the peer, naive timing excludes responder polling |

**Status (macOS, 2026-10-16, `chore/docs-final`):**
`pytest -q --ignore=tests/test_gui_window.py` → **186 passed, 16 skipped**. The 16
skips are the container-only tests above (they need `tc`/`tshark`/ns-3, skip
cleanly, never fail). The 9 window tests make up the rest of the 211 collected;
they were not run in that session (no display) and need a terminal or
`HPQV_GUI_TESTS=1`. This documentation pass did not re-run the container, so the
container pass count is not restated here.

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
The fragment count proves the *structural* property; Sections 6 and 7 measure its *consequence*.

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

> **Superseded as the headline by §7.** This experiment delivered a random 10,842-byte blob
> with a 1 s UDP timeout against a 20 s TCP timeout and did not run the authenticated
> handshake. §7 runs the real handshake with one deadline for every transport and gives
> lower, more defensible figures (84 % vs 18 % at 20 % loss, not 99 % vs 19 %).

---

## 7. Full authenticated handshake under a common deadline

Full write-up: `docs/EXPERIMENT-full-handshake.md`. Data: `results/full_handshake.csv`,
figure `results/full_handshake.png`.

**What it does.** The real ML-KEM-768 + ML-DSA-65 handshake (signatures verified, KEM
encapsulated and decapsulated, key and direction prefix checked on both sides) over three
transports, each attempt held to **one 3 s deadline**: TCP (the hybrid's control channel),
one-shot UDP (`udp-naive`: HELLO and ACCEPT each one datagram, 8 IP fragments in total), and
UDP with the HELLO resent every 250 ms (`udp-retry`). 100 attempts per cell, loopback MTU
pinned to 1500, i.i.d. `tc netem` loss, run 2026-10-15.

| Loss | TCP success | UDP naive success | (1−p)^8 | UDP retry success |
|---:|---:|---:|---:|---:|
| 0 % | 100 % | 100 % | 100 % | 100 % |
| 5 % | 100 % | 72 % | 66 % | 100 % |
| 10 % | 99 % | 44 % | 43 % | 100 % |
| 20 % | 84 % | 18 % | 17 % | 86 % |
| 30 % | 56 % | 6 % | 6 % | 52 % |

Median time over successful attempts (from the CSV): TCP 1.0 ms at 0 % loss rising to
1,017 ms at 30 %; UDP retry 257 ms at 10 %, 770 ms at 20 %, 1,269 ms at 30 %.

**What it supports.**
- One-shot UDP fails as fragment arithmetic predicts (naive tracks `(1−p)^8`).
- TCP completes 84 % / 56 % at 20 % / 30 % loss against 18 % / 6 % for naive UDP, same budget.
- **A UDP design with HELLO retry performs about as well as TCP** (86 % vs 84 %, 52 % vs 56 %).
  The gain is retransmission, not TCP.

**What it does not support.** It does not show TCP beats every reliable-UDP design. UDP retry
matches it here but re-sends the whole 6.4 KB HELLO (5 IP fragments) on any single lost fragment
and relies on IP fragments reaching the peer, which loopback does not challenge. The hybrid's
remaining advantage is structural: zero fragments, so it does not depend on fragment delivery.
The retry interval (250 ms) versus Linux TCP's SYN-retransmission schedule (~1 s, then 3 s) means
that at a 3 s deadline the comparison is largely between retry schedules; other deadlines or
networks could move it. Loopback, not a real call or network; loss is independent, not bursty.

**Why the numbers are lower than §6.** The earlier experiment used a 20 s TCP timeout, a 1 s
no-retry UDP timeout and a random payload. Quote §7 for "does a real call set up".

---

## 8. Live FEC recovery and the Apple Silicon Opus CTL bug

**What changed.** Until Review III the live receiver called plain Opus decode: it did not
detect missing frames or ask for FEC. Now `hpqv.playout.Playout` (used by `demo._receiver_loop`)
sees a sequence gap and, per the repair mode:

| Repair mode (receiver) | Gap behaviour |
|---|---|
| `silence` | zero frames for every missing slot |
| `plc` | Opus packet-loss concealment for every missing slot |
| `fec` (default) | PLC for the earlier frames of a burst; the **last** missing frame is rebuilt with `decode_fec` on the next packet — **only if that packet carries LBRR data** |

A frame counts as **recovered** only when it was rebuilt from LBRR data the next packet really
carried; PLC and silence fills count as **concealed**. Whether a packet has LBRR is asked of
libopus (`opus_packet_has_lbrr`, libopus ≥ 1.5), with the sender's header flag bit 2 as a
fallback on older libopus; CELT-only packets never carry LBRR (`docs/wire-format.md`). A gap
over 50 frames (1 s) is not synthesised. Late or duplicate packets are dropped without touching
the decoder and counted as **late**.

**The Apple Silicon bug.** On macOS arm64 the Opus encoder settings (in-band FEC, expected
packet loss) set through opuslib were silently not applied: `opus_encoder_ctl` is variadic, and
Apple's arm64 ABI passes variadic arguments on the stack, so a ctypes call with a fixed
prototype put the value where libopus never read it. On those machines FEC was effectively off,
with no error. The fix (`audio._encoder_set`) pads the call so the value lands on the stack on
Darwin/arm64; `tests/test_audio.py` now reads the settings back from the encoder and asserts
them. Linux/x86_64 was never affected, and the committed FEC and handshake results came from the
Linux container, so they are unchanged.

**What is and is not shown.**
- The offline experiment (`docs/EXPERIMENT-fec-recovery.md`, §VI-D of `REPORT.md`) shows FEC
  leading no-FEC by 24–30 dB segmental SNR on a synthetic chirp. That encoder chose CELT, where
  `decode_fec` is equivalent to concealment, so part of that gap may be concealment, not LBRR.
  Segmental SNR is not a perceptual score.
- The live path is covered by unit tests that pin the call order (PLC, then `decode_fec`),
  show an LBRR-bearing SILK stream recovers better than PLC, and check the receiver loop
  recovers a dropped datagram. No live listening test has been done.
- FEC looks one frame ahead. Consecutive losses defeat it; the GUI's *Burst loss* preset shows
  this. With a burst of B lost packets only the last is recoverable.

---

## 9. Live key rotation and call-end signalling

Design record: `docs/adr/0004-live-key-rotation.md`; byte layout: `docs/wire-format.md`.

- **Rotation.** *Rotate keys* moves both peers to the next generation mid-call: the rotating
  side switches its send key and sends `MSG_KEY_ROTATE(gen+1)` over the TCP control channel;
  the peer applies it only if it equals its current generation + 1. The new key is HKDF of the
  current key (`session.rotate`). Header flags bits 0–1 carry the generation modulo 4; the
  receiver makes exactly one AEAD attempt with the key the tag names. Sequence numbers
  continue across a rotation, so the jitter buffer and playout see one stream.
- **Retirement.** Old keys stay valid for 4 s (largest configured delay + jitter + 1 s control
  round trip), so datagrams still in the delay line decrypt; after that, replays under the old
  key fail. The GUI disables *Rotate keys* until the old key has retired.
- **Simultaneous presses** rotate once (each side ignores the crossing ROTATE for a generation it
  already reached). Out-of-sequence, malformed or unknown control records are dropped.
- **Call end.** Whoever hangs up sends `MSG_CALL_END` and stops; the peer ends on receiving it or
  on TCP close. CLI and GUI share this path.
- **Limits.** The control channel is unauthenticated TCP after the handshake. An on-path
  attacker injecting an in-sequence `KEY_ROTATE` can desynchronise the keys (denial of
  service); it learns nothing, and could already cut the connection. Authenticating control
  records is future work. Datagrams the new sender emits before the peer has processed the
  ROTATE are lost to the receiver; on a LAN this is a fraction of a frame and repair conceals
  it. The fingerprint (first 8 hex digits of SHA-256 of the current key) is for people to
  compare aloud, not a secret.
- **Evidence.** `test_keyring.py` and `test_callctl.py` (§2), including a live loopback call that
  rotates mid-call and ends both sides on hang-up. Not measured: behaviour across a real
  network.

---

## 10. Receiver-side live telemetry and live MOS

`hpqv.telemetry.Telemetry` samples the running call about once a second and is what the GUI
tiles, graph and **Export session…** use. It exports `timeline.csv`, `summary.md` and three
charts (loss vs recovered, MOS, datagram size vs the 1200 B cap).

**What it measures.** Every loss, recovery, delay and MOS figure describes the stream **this
peer receives**, counted by the local receiver. Your own sender's loss is seen on the *other*
screen. Local knob values are logged beside the data for context.

| Quantity | Definition |
|---|---|
| expected | span of sequence numbers from the peer (its sender advances the sequence on frames it drops, so this counts every frame its encoder made; loss at the very end of an interval is counted when a later packet arrives, as in RFC 3550) |
| loss % | (expected − delivered) / expected; delivered = received − dropped on receipt |
| residual loss % | (lost − FEC-recovered) / expected, where lost includes **late** packets — frames never played as real audio |
| rx jitter | RFC 3550 interarrival jitter |
| added delay | mean one-way transit this interval minus the minimum seen this call (peer clocks are not synced, so absolute delay is unknown and a constant base delay is not included) |
| MOS | ITU-T G.107 **E-model estimate** from residual loss and delay = 20 ms × jitter depth + added delay; the model's jitter term is set to 0 because the buffer is already in the delay |

**What it is not.** The MOS is a model output, not a listening test, and not PESQ/POLQA. It
reflects loss and delay, not codec artefacts, echo or what the audio sounds like. The
handshake panel reports measured handshake time and the HELLO/ACCEPT byte counts actually
exchanged. Tile values come from one-second intervals and are noisy at low packet counts. The
speaker queue is bounded (jitter depth + 2 frames); frames skipped to keep latency bounded are
counted separately (`skipped`) and appear in the export, not as loss.

---

## 11. Live-pipeline scenarios

The design calls for scripted headless loopback calls through the real pipeline (WAV input,
real encoder, keyring, impairment, jitter buffer, playout) covering clean calls, random loss
under each repair mode, burst loss, and delay/jitter against jitter depth. Method, results and
caveats are in **`docs/EXPERIMENT-live-scenarios.md`** (data in `results/live_scenarios.csv`).
This report deliberately does not restate those figures: read them there, with that document's
caveats, not here.

---

## 12. Limitations and remaining work

Status of the Review III list (`docs/REVIEW-2-TO-REVIEW-3-CHANGES.md` §5):

| Item | Status |
|---|---|
| Integrate missing-frame detection and next-packet FEC recovery into live playback | **Closed** — §8 |
| Integrate live key rotation and complete control-message handling | **Closed** — §9 (rotation, call end) |
| Measure the complete authenticated setup under impairment with a common deadline | **Closed** — §7 |
| Evaluate paced speech, per-frame playback deadlines and realistic burst loss | **Partly closed** — the live pipeline now has a Gilbert–Elliott burst-loss model and 20 ms-paced WAV input (§11); no speech corpus, no perceptual scoring; the §7 handshake loss is still i.i.d. |
| Automatic loss feedback / adaptive FEC | **Open** — repair mode, FEC and bitrate are manual knobs |
| Validate the intended two-device demonstration and representative real network paths | **Open** — no recorded two-device run in the repo; no real middleboxes |
| Dynamic PMTU handling | **Open** — fixed 1200 B cap |
| Full QUIC/TLS implementations before making claims about those protocols | **Open** — NS-3's QUIC is a proxy |

Other limitations to state:
- Loopback and `tc netem` with independent loss; real loss is bursty and path-dependent.
- Quality figures are E-model estimates and segmental SNR; **no listening tests**.
- UDP-with-retry matches TCP on handshake completion (§7); the argument for the hybrid rests on
  zero fragments and not depending on fragment delivery, which this testbed cannot stress.
- Unauthenticated in-call control channel (§9). Identities are pinned out-of-band; no CA.
- Not security-audited.

---

## 13. Reproducing everything

```bash
# Build the evaluation container (once)
docker build -t hpqv-eval ./eval

# Full unit-test suite inside the container (container pass count not restated here)
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run pytest -q'

# Track 2 — real prototype sweeps (5-run medians; takes several minutes)
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run python scripts/run_eval.py'

# Track 3 — NS-3 sweeps
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run python scripts/run_ns3.py'

# Full-handshake experiment (§7); also scripts/run_handshake_survival.py, run_fec_recovery.py
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval \
  bash -c 'cd /work && uv sync -q && uv run python scripts/run_full_handshake.py'
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
`handshake_survival.csv`, `fec_recovery.csv`, `full_handshake.csv`, and the figures `loss_vs_mos.png`, `loss_vs_fragments.png`, `latency_vs_ttfb.png`,
`ns3_loss_vs_mos.png`, `ns3_latency_vs_ttfb.png`.

On macOS: `pytest -q --ignore=tests/test_gui_window.py` (186 pass, 16 container-only tests skip; the 9
window tests need a terminal or `HPQV_GUI_TESTS=1`).
Note macOS needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for opuslib.

---

## 14. Metric glossary

| Metric | Meaning |
|---|---|
| **Fragment count** | IP datagrams with the More-Fragments flag set or a non-zero fragment offset, counted from a packet capture. **0 is the goal.** |
| **MOS** | Mean Opinion Score, 1–5. Perceived call quality, computed from delay/jitter/loss with the ITU-T G.107 E-model (not human listeners). ≥4 good, ~3 fair, <2.5 poor. |
| **TTFB** | Time To First Byte — how long from connection start until voice can flow. Dominated by handshake cost. |
| **Jitter** | Variation in frame arrival spacing (ms). High jitter forces a bigger buffer, which adds delay. |
| **Drop rate / voice loss** | Fraction of voice frames that never arrived. Measured at the application layer. |
