# A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

**Cain Manoj (23BCE1663), Sreenandu G (23BCE1120), Ashwanth Kumaravel (23BCE1244)**
Guide: Dr. Renuka Devi S.

> **How to use this document.** Sections III–VI are drafted from the committed
> measurements in `results/` and are ready to edit for tone. Sections marked
> ✍️ **WRITE THIS** are yours — they carry the analysis a panel will question you
> on, and they should be in your own words. Each one lists what to cover, the
> source material, and a target length.
>
> Result tables between `<!-- BEGIN GENERATED -->` markers are produced by
> `scripts/render_tables.py` from `results/*.csv`. If you re-run any sweep,
> re-run that script; `pytest` fails if they drift out of sync.

---

## Abstract

✍️ **WRITE THIS** — ~200 words, last thing you write.

Cover, one or two sentences each: (1) post-quantum handshakes exceed the MTU and
fragment; (2) fragmentation destroys datagrams, so naive PQC-over-UDP voice fails
to connect under loss; (3) we split control (TCP) from data (UDP) with app-layer
framing under a 1200 B cap; (4) the result — zero fragments at every loss level,
and handshake completion of 99 % vs 19 % at 20 % loss; (5) corroborated by an
NS-3 model comparing against TCP and QUIC arrangements.

---

## I. Introduction

✍️ **WRITE THIS** — ~1.5 pages.

The argument, in order:

1. **Motivation.** Real-time voice needs UDP; "harvest now, decrypt later" forces
   a move to post-quantum key exchange. These two requirements conflict.
2. **The conflict, concretely.** One handshake carries 10,842 bytes of ML-KEM-768
   and ML-DSA-65 material — 7× the 1500 B MTU — so over UDP it becomes 8 IP
   fragments. Middleboxes drop fragments, and one lost fragment kills the datagram.
3. **The gap in existing work.** PQC has been studied in TLS and QUIC (see §II),
   but not for the specific problem of a *voice* transport whose handshake will
   not fit in a datagram.
4. **Our contribution.** State these four plainly — they are what you defend:
   - a hybrid TCP-control / UDP-data transport that never fragments;
   - a mutually-authenticated PQC handshake with per-direction nonce separation;
   - measurement showing the *consequence* of fragmentation (call setup failure),
     not merely its occurrence;
   - an NS-3 model placing the design against TCP and QUIC arrangements.
5. **Roadmap paragraph.** One sentence per remaining section.

Source: `README.md`, §1 of `docs/EVALUATION-REPORT.md`.

---

## II. Related Work

✍️ **WRITE THIS** — ~1 page. All 15 references are in §IX, grouped below by theme.

- **PQC cost and standardisation** [12], [15], [5] — NIST selections, and what
  PQC costs TLS 1.3 in bytes and handshake time.
- **PQC in QUIC** [1], [2] — the closest prior art. Note what they measure
  (QUIC handshake performance under PQC) and what they do not (a voice transport
  whose handshake exceeds the MTU).
- **Transport-level workarounds for large handshakes** [7] — TurboTLS runs the
  handshake over UDP *and* TCP to save a round trip. This is the nearest idea to
  ours; be explicit about the difference: TurboTLS optimises *latency* of setup,
  we are solving *fragmentation* of an oversized handshake on the voice path.
- **QUIC performance and transport behaviour** [8], [9], [10], [11], [13], [14] —
  background for the QUIC comparison in §VI.
- **Formal guarantees and deployment** [4], [6], [3] — hybrid key exchange proofs
  and real deployments, supporting the choice of a hybrid/PQC construction.

End with a short paragraph naming the gap you fill: *none of the above addresses
a real-time voice transport where the PQC handshake cannot fit in a datagram.*

---

## III. System Design

Two peers connect over two parallel channels, chosen so the oversized handshake
never touches the datagram path.

**Control channel (TCP).** Reliable and stream-oriented. It carries the one-time
post-quantum handshake, call setup and teardown, and key rotation. Because TCP is
a byte stream, the transport segments the handshake transparently: an oversized
PQC blob never becomes an IP fragment.

**Data channel (UDP).** Unreliable by design, carrying Opus voice frames. A lost
frame is concealed or recovered by forward error correction, never retransmitted,
because retransmission arrives too late to be useful in a real-time call. Every
datagram is held under a conservative 1200-byte cap with the IP Don't-Fragment
bit set, so an oversized packet fails loudly instead of fragmenting silently.

**Cryptographic construction.** The initiator sends a HELLO containing its
ML-KEM-768 public key, its ML-DSA-65 public key and a signature over both. The
responder verifies this against a pinned identity, encapsulates, and returns an
ACCEPT containing the KEM ciphertext **and its own signature over the transcript**
— so both peers authenticate each other. Both sides then derive, via HKDF-SHA256,
a 32-byte voice key plus **two** 4-byte nonce prefixes, one per direction.

| Message | Contents | Bytes |
|---|---|---|
| HELLO | ML-KEM-768 public key (1184) + ML-DSA-65 public key (1952) + signature (3309) | 6,445 |
| ACCEPT | ML-KEM-768 ciphertext (1088) + responder signature (3309) | 4,397 |
| **Total** | | **10,842** |

Two design decisions are worth stating explicitly, because both were changed
during development after review (see `docs/adr/`):

1. **Mutual authentication.** The first design authenticated only the initiator,
   leaving the responder's ACCEPT unsigned and open to a man-in-the-middle. The
   responder now signs the transcript `kem_pub ‖ ct`.
2. **Per-direction nonce separation.** A single nonce prefix shared by both
   directions would have caused both peers to use the same (key, nonce) pair at
   sequence 0 — catastrophic for ChaCha20-Poly1305. Each direction now has its own
   prefix derived from the same HKDF output.

Source: `docs/wire-format.md`, `docs/adr/0002`, `docs/adr/0003`.

---

## IV. Implementation

Implemented in Python 3.11 (~1,100 lines excluding tests), using liboqs for
post-quantum primitives, `cryptography` for ChaCha20-Poly1305 and HKDF, and
libopus for the codec.

| Module | Responsibility |
|---|---|
| `handshake.py` | ML-KEM-768 encapsulation, ML-DSA-65 signing/verification, HKDF derivation |
| `control.py` | length-prefixed records over TCP; handshake driver; control messages |
| `session.py` | HKDF key-rotation ratchet, applied in lockstep by both peers |
| `packet.py` | voice datagram: 11-byte header, ChaCha20-Poly1305 AEAD, 1200 B guard |
| `jitter.py` | fixed-depth reordering buffer with late/duplicate drop |
| `audio.py` | Opus encode/decode, 48 kHz mono, 20 ms frames, in-band FEC |
| `udp.py` | UDP socket with the Don't-Fragment bit set |
| `call.py`, `demo.py` | end-to-end call assembly; live mic/speaker demo |

**Voice datagram.** An 11-byte header (version, type, 8-byte sequence, flags) is
authenticated as associated data; the nonce is the direction's 4-byte prefix
concatenated with the 8-byte sequence number, so a (key, nonce) pair is never
reused. The `seal` function raises if the assembled datagram would exceed 1200
bytes, making the fragmentation-free property an enforced invariant rather than
an intention.

**Forward error correction.** Opus in-band FEC (LBRR) embeds a low-bitrate copy
of frame *N* inside packet *N+1*. Recovery is therefore a decoder action: when a
packet is lost, the next packet is decoded with the FEC flag set to reconstruct
the missing frame, falling back to concealment when consecutive packets are lost.

**Testing.** 53 automated tests: unit tests per module, integration tests for the
end-to-end call, and experiment-level tests. Security-relevant behaviour is
tested directly — a forged ACCEPT and a tampered ciphertext must both be
rejected, an oversized datagram must be refused, and the two directions must use
distinct nonce prefixes.

---

## V. Experimental Setup

**Environment.** `tc netem` and ns-3 do not run on macOS, so all evaluation runs
in a reproducible Ubuntu 24.04 container (`eval/Dockerfile`) containing Python
3.11, liboqs, libopus, `iproute2`, `tshark` and ns-3 3.41.

**A methodological detail that matters.** Docker's loopback MTU defaults to
65536, at which *nothing fragments*. Every experiment pins `lo` to 1500 for its
duration. Without this pin the fragmentation comparison silently measures nothing
— an early run produced exactly that false negative.

**Track 2 — real prototype (primary evidence).** The actual implementation, with
real PQC handshakes and real Opus audio, measured under controlled impairment.
Three transports are compared: our `hybrid`; `naive`, which sends the handshake
as one oversized UDP datagram with no app-layer framing; and `tcp`, which carries
both handshake and voice over TCP. Primary sweep: loss 0/5/10/20/30 % at 50 ms
delay and 10 ms jitter, reporting the **median of 5 runs**. Secondary sweep: link
delay 20/50/100/200 ms at 0 % loss. Fragments are counted from packet captures
using the filter `ip.flags.mf==1 || ip.frag_offset>0`.

**Track 3 — NS-3 model (secondary).** PQC is *modelled*, never executed: the real
10,842-byte handshake volume and the real measured handshake CPU cost (≈0.34 ms
from a liboqs benchmark) are injected into a two-node point-to-point simulation.
This allows comparison against a QUIC-style arrangement that the real prototype
cannot easily host.

**Metrics.** Fragment count (target: 0); MOS computed from measured delay, jitter
and loss via the ITU-T G.107 E-model; TTFB (time to first byte, dominated by
handshake cost); interarrival jitter; and application-level frame loss.

---

## VI. Results

### A. Fragmentation and voice quality

<!-- BEGIN GENERATED: track2-loss -->
| loss | hybrid frags | naive frags | tcp frags | hybrid MOS | naive MOS | tcp MOS |
|---|---|---|---|---|---|---|
| 0 % | **0** | 5 | 0 | 4.35 | 4.35 | 4.35 |
| 5 % | **0** | 5 | 0 | 3.54 | 3.54 | 4.35 |
| 10 % | **0** | 5 | 0 | 3.17 | 3.06 | 4.35 |
| 20 % | **0** | 4 | 0 | 2.18 | 2.08 | 4.35 |
| 30 % | **0** | 4 | 0 | 1.90 | 1.90 | 4.35 |
<!-- END GENERATED: track2-loss -->

The hybrid transport produces **zero IP fragments at every loss level**, while the
naive baseline always fragments. This is the central claim, measured from packet
captures of the real implementation.

**An honest reading of the MOS columns.** Naive's MOS is *not* meaningfully worse
than the hybrid's; the two track each other within run-to-run noise. This is
expected and should be stated rather than glossed: fragmentation does not affect
the *voice stream*, because Opus frames are ~160 bytes and never fragment in
either design. It affects the *handshake*. Section VI-C measures that consequence
directly. Note also that `tcp` holds a flat MOS because it never loses voice — it
pays elsewhere, in setup latency.

### B. Setup latency versus link delay

<!-- BEGIN GENERATED: track2-latency -->
| link delay | hybrid TTFB | naive TTFB | tcp TTFB |
|---|---|---|---|
| 20 ms | 13.6 ms | 14.4 ms | 23.6 ms |
| 50 ms | 45.2 ms | 45.3 ms | 48.9 ms |
| 100 ms | 98.1 ms | 100.3 ms | 106.0 ms |
| 200 ms | 196.8 ms | 199.8 ms | 205.1 ms |
<!-- END GENERATED: track2-latency -->

The full-TCP arrangement pays a consistent penalty at every round-trip time,
since voice as well as control must complete TCP setup.

### C. Does the handshake survive? (the decisive experiment)

The 10,842-byte handshake becomes **8 IP fragments** under the naive design, and
losing any one destroys the datagram. With independent per-packet loss `p`,
survival should therefore decay as `(1-p)^8`. We attempted 100 handshakes per
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

At 20 % loss roughly one naive call in five connects, against 99 in 100 for the
hybrid. The measured curve tracks the `(1-p)^8` prediction, confirming that the
mechanism is fragment-loss amplification rather than an artefact of the setup.
The hybrid pays for this in *time* rather than failure: its median handshake rises
into the hundreds of milliseconds and beyond a second as TCP retransmits. It is
not invulnerable — at the highest loss level a fraction of hybrid handshakes also
fail inside the timeout — but the margin is large.

### D. Forward error correction

Measured against the undamaged decode of the same packet stream, which isolates
loss damage from Opus's own (perceptual, non-waveform-preserving) coding error:

<!-- BEGIN GENERATED: fec-recovery -->
| loss | FEC on | FEC off | frames lost | recovered by FEC |
|---|---|---|---|---|
| 0 % | 99.0 dB (cap) | 99.0 dB (cap) | 0 | 0 |
| 5 % | 63.6 dB | 37.3 dB | 8 | 7 |
| 10 % | 60.1 dB | 30.6 dB | 11 | 10 |
| 20 % | 47.4 dB | 23.9 dB | 26 | 20 |
| 30 % | 33.7 dB | 10.0 dB | 39 | 28 |
<!-- END GENERATED: fec-recovery -->

FEC leads by 24–30 dB at every lossy level. Recovery degrades as loss rises (28 of
39 frames at 30 %) because single-frame lookahead cannot recover *consecutive*
losses.

### E. NS-3 comparison against TCP and QUIC

<!-- BEGIN GENERATED: ns3-loss -->
| loss | hybrid (loss / TTFB / MOS) | tcp (loss / MOS) | quic (loss / TTFB / MOS) |
|---|---|---|---|
| 0 % | 0.0 % / 131 ms / 4.38 | 0.0 % / 4.38 | 0.0 % / 35 ms / 4.38 |
| 5 % | 4.6 % / 179 ms / 3.78 | 0.0 % / 4.38 | 4.8 % / 35 ms / 3.75 |
| 10 % | 10.0 % / 180 ms / 3.11 | 0.0 % / 4.38 | 10.2 % / 35 ms / 3.09 |
| 20 % | 20.6 % / 179 ms / 2.26 | 0.0 % / 4.38 | 20.0 % / 227 ms / 2.29 |
| 30 % | 27.7 % / 182 ms / 1.92 | 0.0 % / 4.38 | 27.3 % / 227 ms / 1.94 |
<!-- END GENERATED: ns3-loss -->

<!-- BEGIN GENERATED: ns3-latency -->
| link delay | hybrid TTFB | tcp TTFB | quic TTFB |
|---|---|---|---|
| 20 ms | 56 ms | 56 ms | 20 ms |
| 50 ms | 131 ms | 131 ms | 35 ms |
| 100 ms | 256 ms | 256 ms | 60 ms |
| 200 ms | 506 ms | 506 ms | 110 ms |
<!-- END GENERATED: ns3-latency -->

QUIC's 1-RTT setup needs roughly half the round-trips of a TCP-based handshake, so
its TTFB grows far more slowly with link delay — though its retries make it the
slowest under heavy loss.

**Stated limitations of the model** (detailed in `ns3/README.md`): QUIC is
approximated as UDP with a 1-RTT handshake, as ns-3 has no official QUIC module;
FlowMonitor timestamps retransmitted segments as fresh packets, so the model
*understates* TCP's head-of-line latency and TCP's MOS stays high — read that as
"TCP does not drop voice frames", not "TCP is good for real-time voice"; and a
single flow on a constant-delay link produces negligible jitter.

---

## VII. Discussion

✍️ **WRITE THIS** — ~1 page. The material is above; the interpretation is yours.

Points to make, in roughly this order:

1. **What the evidence actually supports.** The fragmentation-free property is
   proven structurally (fragment counts) *and* consequentially (handshake
   survival). Be precise: the win is in **call establishment under loss**, not in
   steady-state voice quality. Claiming better MOS would overstate the result.
2. **Why the MOS results are flat, and why that is fine.** Explain the mechanism
   (§VI-A). A panel is likely to probe this — you are better off raising it first.
3. **Cost of the design.** TCP setup latency and, under heavy loss, retransmission
   delay. Argue why a slow connect beats a failed connect for a voice call.
4. **Threat model.** Mutual authentication with out-of-band pinning; no CA
   hierarchy; what an attacker can and cannot do.
5. **Validity.** Emulated local link, not the open Internet. Note that real
   middleboxes commonly discard fragments outright, which would penalise the naive
   baseline *further* — the results are conservative in our favour.

---

## VIII. Conclusion and Future Work

✍️ **WRITE THIS** — ~0.5 page.

Restate the contribution and the two headline numbers. Then future work, seeded
from deferred items (`docs/superpowers/plans/`, §"Stretch"):

- Reed–Solomon block FEC for burst losses, beyond Opus's single-frame lookahead.
- An adaptive jitter buffer in place of the fixed depth.
- Active path-MTU discovery instead of the conservative fixed 1200 B cap.
- PESQ/POLQA perceptual scoring alongside the E-model.
- Validation across the open Internet, including fragment-dropping middleboxes.
- A formal security proof of the handshake construction, in the style of [4].

---

## IX. References

[1] M. Kempf, N. Gauder, B. Jaeger, J. Zirngibl, and G. Carle, "A Quantum of QUIC: Dissecting Cryptography with Post-Quantum Insights," in *Proc. IFIP Networking Conf.*, 2024.

[2] B. Dong and Q. Wang, "EPQUIC: Efficient Post-Quantum Cryptography for QUIC-Enabled Secure Communication," in *Proc. Great Lakes Symp. VLSI (GLSVLSI)*, 2025.

[3] P. Otero-García, A. Pastor, D. Lopez, and V. Martin, "Introducing Post-Quantum Algorithms in Open RAN Interfaces," 2025.

[4] B. Blanchet and C. Jacomme, "Post-quantum Sound CryptoVerif and Verification of Hybrid TLS and SSH Key-Exchanges," in *Proc. IEEE Computer Security Foundations Symp. (CSF)*, 2024.

[5] E. Crockett, C. Paquin, and D. Stebila, "Prototyping Post-Quantum and Hybrid Key Exchange and Authentication in TLS and SSH," Cryptology ePrint Archive, Report 2019/858, 2019.

[6] T. Mallick et al., "Study of Post-Quantum Status of Widely Used Protocols," 2026.

[7] C. Aguilar-Melchor, T. Bailleux, J. Goertzen, A. Mendelsohn, and D. Stebila, "TurboTLS: TLS Connection Establishment with 1 Less Round Trip," 2024.

[8] J. Iyengar and M. Thomson, "QUIC: A UDP-Based Multiplexed and Secure Transport," RFC 9000, IETF, 2021.

[9] M. Thomson and S. Turner, "Using TLS to Secure QUIC," RFC 9001, IETF, 2021.

[10] E. Rescorla, "The Transport Layer Security (TLS) Protocol Version 1.3," RFC 8446, IETF, 2018.

[11] B. Jaeger, J. Zirngibl, M. Kempf, K. Ploch, and G. Carle, "QUIC on the Highway: Evaluating Performance on High-Rate Links," in *Proc. IFIP Networking Conf.*, 2023.

[12] National Institute of Standards and Technology, "Post-Quantum Cryptography Standardization," 2023.

[13] X. Yang, L. Eggert, J. Ott, S. Uhlig, Z. Sun, and G. Antichi, "Making QUIC Quicker With NIC Offload," in *Proc. Workshop on the Evolution, Performance, and Interoperability of QUIC*, 2020.

[14] A. Yu and T. A. Benson, "Dissecting Performance of Production QUIC," in *Proc. Web Conf.*, 2021.

[15] M. Sosnowski, F. Wiedner, E. Hauser, L. Steger, D. Schoinianakis, S. Gallenmüller, and G. Carle, "The Performance of Post-Quantum TLS 1.3," in *Proc. CoNEXT*, 2023.
