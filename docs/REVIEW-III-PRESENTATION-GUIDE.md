# Review III presentation guide
## A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

Prepared: 15 September 2026  
Team: Cain Manoj (23BCE1663), Sreenandu G (23BCE1120), Ashwanth Kumaravel (23BCE1244)  
Guide: Dr. Renuka Devi S.

## Instructions for Claude and Canva

### Ready-to-paste creation prompt

Use the Canva connector to create a 16:9 academic Review III presentation from this guide and the attached chart images. Create 10 main slides and backup slides for detailed results and questions.

Use the "On the slide" sections as the main content. Use the speaker notes as presenter notes where supported; otherwise provide a separate notes document. Do not put the entire guide onto slides.

Use the supplied chart PNGs directly. Preserve their aspect ratios, axes, legends and plotted values. Do not regenerate the experimental charts with AI, estimate values from pixels, replace them with decorative graphics, or invent additional results. Use the exact numeric tables in this guide for callouts.

Create a clean academic layout with readable text and consistent colors. Give each main results chart enough room to read its axes. Split crowded results across a main slide and a backup slide if needed.

For the architecture, use the text diagram in Slide 3 as the specification for labeled boxes and arrows. No standalone architecture image is included in this package. Do not describe the text diagram as an uploaded image. If a separate architecture image is supplied later, use it directly.

Preserve every material qualification:
- "Handshake-sized payload delivery" is synthetic payload delivery, not authenticated call success.
- Naive UDP and TCP use different timeout policies.
- MOS is model-estimated, not listener-rated.
- First-frame timing excludes the cryptographic handshake.
- The FEC recovery experiment is separate from the live receive path.
- ns-3 results are model outputs and QUIC is a simplified proxy.
- Saved results are historical runs. Do not present them as newly rerun experiments.

Check the slide text against this guide, particularly all numbers and claims. Return the editable Canva design link. Check supported export formats and export a PowerPoint .pptx when available. Inspect the exported deck for cropping, unreadable labels and changed layout. Report any export or presenter-note limitations.

### Package contents and upload instructions

Upload this Markdown file and the images inside the charts folder to Claude. A local Markdown link alone does not give the Canva connector access to the image. If the connector cannot access an attached image, upload that image to Canva and place it manually rather than substituting an invented chart.

The package includes seven existing chart images. It does not include a separate architecture diagram image, demo video, identity file or source-code archive. Links to source code and supporting documents are for local verification within the repository; they are not required attachments for slide creation.

### Chart placement and required captions

| Image | Placement | Slide title or caption |
|---|---|---|
| loss_vs_fragments.png | Main results slide | Captured IP fragments under configured packet loss. Hybrid benchmark capture excludes the actual cryptographic handshake. |
| handshake_survival.png | Main results slide | Handshake-sized payload delivery under packet loss. Synthetic 10,842-byte payload; 100 attempts per condition; naive UDP has a 1-second timeout, TCP uses 20-second wait settings. |
| loss_vs_mos.png | Main voice-results slide | Model-estimated MOS. Simplified assumptions; not a listening study or a complete playback-latency measurement. |
| fec_recovery.png | Backup slide, or main slide if time permits | Separate FEC reconstruction experiment on a generated signal, compared with silence filling. Not the live receiver's recovery behavior. |
| latency_vs_ttfb.png | Backup slide | Sending-start to first received voice frame. Excludes the cryptographic handshake; benchmark is not paced as real-time audio. |
| ns3_loss_vs_mos.png | Backup slide | Secondary ns-3 model: estimated MOS under loss. QUIC is a simplified proxy; TCP playback delay is not fully represented. |
| ns3_latency_vs_ttfb.png | Backup slide | Secondary ns-3 modeled first-byte timing. Do not compare directly with the differently timed real-prototype benchmark. |

Some existing image titles use shorthand such as "handshake" or "TTFB." Keep the image intact and add the precise slide title and caption above. Do not repeat the shorthand as a stronger claim in the narration.

### Presentation priorities

1. Show the working implementation and actual team contributions.
2. Explain the TCP-control / UDP-voice design.
3. Present fragmentation and payload delivery as the primary experimental results.
4. Explain the limits of the MOS and FEC results.
5. Finish with remaining validation work.

Do not claim a completed physical two-device test, a completion percentage, fresh experiment runs or verified individual contributions unless the team supplies that evidence.

---

## How to use this document

Build your slides from the **On the slide** sections. Put the **Speaker notes** in PowerPoint's notes field. Use the appendices to prepare for questions and collect evidence.

Suggested format: 10 slides, approximately 10–12 minutes including a 2-minute demo, followed by questions. Adjust to the allotted panel time. For an 8-minute slot, combine slides 2–3 and move slide 9 to backup.

This guide reflects inspected code and committed results. The local test run on 15 September produced **40 passed and 15 skipped in 25.15 seconds**. The Linux experiment suite and physical two-device demo were not rerun for this guide. Saved results are historical measurements, not new runs.

The repository plan is our working scope reference. Confirm it matches the scope actually approved by the guide before assigning a completion percentage.

---

## 1. What the rubric requires

| Criterion | Marks | Evidence to show |
|---|---:|---|
| Implementation | 5 | Executable modules, working integrated demo, current test output, actual team contributions |
| Technical accuracy | 5 | Architecture, exact algorithms and parameters, accurate experiment definitions, justified limitations |
| Results obtained so far | 5 | Clear tables/graphs, baseline comparison, sample size and conditions, interpretation |
| Presentation and clarity | 5 | Logical explanation, readable slides, practiced demo, accurate answers |
| Total | 20 | Give each criterion visible attention |

Main message:

> We implemented a prototype that separates post-quantum connection setup over TCP from encrypted voice over UDP. Controlled experiments show the consequences of oversized UDP payloads and the reliability benefit of TCP delivery. Further validation will measure complete authenticated call setup and real-time audio performance.

Do not present a numerical completion percentage until each approved deliverable has evidence.

---

# Slide-by-slide content

## Slide 1 — Title and project objective
**Time: 30 seconds**

### On the slide

**A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat**

- Review III: implementation and interim evaluation
- Reliable post-quantum setup over TCP
- Encrypted voice over size-limited UDP
- Team names, registration numbers, guide

### Speaker notes

“Our project investigates how to carry large post-quantum handshake messages without placing them in oversized UDP datagrams, while retaining UDP for voice. We will show the implementation, demonstrate a call, and explain the measurements obtained so far.”

### Visual

A simple split into “TCP: setup/control” and “UDP: voice.”

---

## Slide 2 — Problem and motivation
**Time: 50 seconds**

### On the slide

- Post-quantum handshake material is larger than a typical 1500-byte Ethernet MTU.
- An oversized UDP datagram may become multiple IP fragments.
- Missing one fragment prevents reconstruction of that datagram.
- Setup reliability and timely voice delivery have different requirements.

**Research question:** Can separating control and voice avoid oversized UDP handshake traffic while supporting an encrypted voice application?

### Speaker notes

“MTU is the maximum IP packet size supported by a link. Our experiments use an MTU of 1500 bytes. If a large UDP datagram is fragmented, the receiver needs every fragment to reconstruct it. We therefore use TCP for the large setup messages and UDP for small voice packets.”

Say “the experiment uses 1500 bytes,” not “all Internet links have an MTU of 1500.”

### Backup calculation

| Cryptographic material | Bytes |
|---|---:|
| ML-KEM-768 public key | 1,184 |
| ML-DSA-65 public key | 1,952 |
| Initiator signature | 3,309 |
| HELLO cryptographic subtotal | 6,445 |
| ML-KEM-768 ciphertext | 1,088 |
| Responder signature | 3,309 |
| ACCEPT cryptographic subtotal | 4,397 |
| Total cryptographic material | 10,842 |

Actual application records also contain two one-byte version fields and two four-byte record lengths: **10,852 bytes across the two framed records**, excluding transport/network headers and other control messages.

The survival experiment combines 10,842 bytes into one synthetic payload. The real protocol exchanges two separate messages in opposite directions.

---

## Slide 3 — Architecture and implemented scope
**Time: 60 seconds**

### On the slide

- TCP control: framed handshake and control-message support.
- UDP data: Opus audio protected with ChaCha20-Poly1305.
- Each application voice datagram is limited to 1200 bytes.
- CLI and desktop GUI assemble the call.
- Tests and evaluation scripts support verification.

### Diagram to recreate

```text
Peer A                                              Peer B
Identity / ML-KEM / ML-DSA <-- TCP handshake --> Identity / ML-KEM / ML-DSA
             |                                      |
        Derived key                            Derived key
             |                                      |
Microphone -> Opus -> Encrypt ---- UDP ----> Decrypt -> Buffer -> Decode -> Speaker
Speaker <- Decode <- Buffer <- Decrypt <---- UDP ---- Encrypt <- Opus <- Microphone
```

### Speaker notes

“Hybrid refers to the combination of TCP and UDP. It does not mean that we combine a classical and a post-quantum key exchange. We use existing cryptographic libraries and codecs; our project contribution is their transport integration and evaluation.”

“TCP handles stream segmentation and retransmission. Splitting application writes is not by itself a guarantee about packet boundaries.”

### Implementation evidence

| Module | Evidence available | Qualification |
|---|---|---|
| Handshake and TCP framing | Source and automated tests | Custom protocol, not TLS |
| Voice encryption and size guard | Packet tests and call integration | Bound applies to application datagram bytes |
| Opus encode/decode | Audio tests and demo code | Physical microphone/speaker validation still needed |
| Reordering buffer | Unit tests and receive path | Fixed packet depth, not adaptive playout |
| Key-rotation helper and control records | Source and unit tests | Do not claim automatic live-call rekeying without demonstrating integration |
| GUI/CLI | Source, packaging and demo instructions | Test the exact device pairing before review |
| FEC recovery | Dedicated evaluation code/results | Recovery is separate from the current live receive path |
| Network evaluation | Scripts, CSVs, figures | Historical container results |

---

## Slide 4 — Technical design
**Time: 60 seconds**

### On the slide

| Function | Implementation |
|---|---|
| Key establishment | ML-KEM-768 |
| Identity signatures | ML-DSA-65 with pinned peer public keys |
| Key derivation | HKDF-SHA256 |
| Voice protection | ChaCha20-Poly1305 |
| Audio | Opus, 48 kHz, mono, 20 ms frames |
| Voice packet bound | 1200 bytes, including application header and authentication tag |

### Speaker notes

“ML-KEM establishes a shared secret. ML-DSA signatures are checked against pinned peer identities. HKDF derives the voice key and direction-specific nonce prefixes. ChaCha20-Poly1305 encrypts voice frames and authenticates their headers.”

“We use a custom academic protocol. We are not claiming a formally verified or production-audited secure calling system.”

### Packet diagram

```text
Version  Type  Sequence number  Flags  Encrypted Opus bytes  Authentication tag
  1 B    1 B       8 B          1 B        variable               16 B
|------------- 11-byte header ------|
|------------------- maximum 1200 bytes -------------------------------|
```

- Maximum encoded audio payload under this layout: 1173 bytes.
- Nonce: 4-byte derived direction prefix + 8-byte sequence number.
- Avoid repeating a nonce under the same key. Explain this requirement; do not claim formal uniqueness across every possible session.
- A 1200-byte UDP payload occupies 1228 bytes with basic IPv4 and UDP headers, or 1248 bytes with basic IPv6 and UDP headers. Extra headers/tunnels can change that.
- The UDP helper conditionally requests Don't Fragment behavior when the platform exposes the relevant option. Confirm actual behavior with packet capture on the evaluation platform.
- Default reordering depth is three packets. This is not a guaranteed 60 ms wall-clock playout delay.

---

## Slide 5 — Working demonstration and team evidence
**Time: 2 minutes**

### On the slide

**Demonstration**

1. Select identity and connect two peers.
2. Speak in both directions.
3. Increase outgoing voice loss to 20%.
4. Show packet counters and describe audible behavior.
5. Return loss to zero, then hang up.

**Verification:** latest local run: 40 passed, 15 skipped.

### Speaker notes

“The slider drops outgoing voice packets in the application. It lets us demonstrate behavior during a call. It does not emulate network loss for the TCP handshake.”

“Some automated tests need Linux network tools or ns-3, so they were skipped on this Mac. We report them as skipped, not passed.”

### Team attribution

The original plan suggests the following division. Replace or confirm it against actual work:

| Person | Planned area | Bring actual evidence |
|---|---|---|
| Ashwanth | Cryptography and TCP control | Relevant commits, tests, explanation of handshake |
| Cain | UDP transport and packet handling | Relevant commits, packet format and size-limit test |
| Sreenandu | Audio and evaluation | Relevant commits, experiment commands and result interpretation |
| All | Integration and presentation | Demo rehearsal and documented contributions |

Do not invent commit authorship or claim planned ownership proves completed work.

---

## Slide 6 — Evaluation method
**Time: 60 seconds**

### On the slide

| Experiment | Conditions | Measures |
|---|---|---|
| Transport sweep | Linux loopback, MTU 1500; 0–30% loss; 50 ms configured delay; 10 ms configured jitter; 5-run medians | Captured fragments, received-frame loss, first-frame timing, modeled MOS |
| Payload survival | 10,842-byte synthetic payload; 100 attempts per transport/loss setting | Delivery success and successful-transfer duration |
| FEC experiment | 3-second generated signal; identical seeded loss decisions | Reconstruction SNR and recovery attempts |
| ns-3 | Separate transport model | Secondary comparison only |

### Speaker notes

“The transport experiments use real sockets, encryption and Opus encoding, with Linux tc netem providing impairment. However, the main runner completes authentication before impairment and before capture. Its timing therefore excludes that handshake.”

“The transport runner encodes silence and sends frames without 20 ms real-time pacing. Its timing and jitter values are limited benchmark measurements, not mouth-to-ear call-quality measurements.”

“The payload-survival test isolates delivery of large messages. It does not execute the cryptographic handshake in each attempt.”

### Method details for backup

- Fragment filter: `ip.flags.mf==1 || ip.frag_offset>0`.
- Naive transport sweep inserts a 6445-byte random UDP payload.
- Survival experiment uses a different payload size: 10,842 bytes.
- Naive survival: one UDP send, one-second timeout.
- TCP survival: reliable stream transfer, 20-second socket/thread wait settings; not a single rigorously enforced common end-to-end deadline.
- These are intentionally different transport behaviors, not equal-time retry policies.
- Survival experiment does not apply the transport sweep's 50 ms delay and 10 ms jitter settings.
- Saved aggregate CSVs do not contain every individual run. Preserve raw runs for future repeatability and uncertainty analysis.

---

## Slide 7 — Result: fragmentation and payload delivery
**Time: 90 seconds**

### On the slide

**Captured fragmentation in the transport sweep**

| Configured loss | Hybrid observed fragments | Naive observed fragments |
|---|---:|---:|
| 0% | 0 | 5 |
| 5% | 0 | 5 |
| 10% | 0 | 5 |
| 20% | 0 | 4 |
| 30% | 0 | 4 |

**Separate 10,842-byte payload-delivery experiment**

| Configured loss | Naive UDP deliveries / 100 | TCP deliveries / 100 |
|---|---:|---:|
| 0% | 100 | 100 |
| 5% | 75 | 100 |
| 10% | 39 | 100 |
| 20% | 19 | 99 |
| 30% | 2 | 83 |

**Takeaway:** avoiding a single oversized UDP payload and allowing reliable delivery improves completion in this experiment, at the cost of waiting for retransmissions.

### Speaker notes

“At 20% configured loss, the synthetic payload arrived in 99 of 100 TCP attempts and 19 of 100 naive UDP attempts. These are payload deliveries under the stated timeout policies, not authenticated call connection rates.”

“The two experiments have different payload sizes, so their fragment counts should not be treated as the same measurement.”

“Zero captured fragments applies to the captured benchmark traffic. Since the actual handshake occurs before that capture, this graph alone does not verify fragmentation behavior for the entire call lifecycle.”

### Visual assets

- [Fragment counts](../results/loss_vs_fragments.png)
- [Payload survival](../results/handshake_survival.png)

Recreate the survival graph with the title **“Handshake-sized payload delivery under packet loss”**. Add a footnote giving payload size, sample size and timeout policy. Existing graph wording may overstate the experiment.

### Backup interpretation

For a single 10,842-byte UDP payload at MTU 1500 with basic IPv4 headers:

```text
Fragment count = ceil((10842 + 8) / 1480) = 8
Idealized delivery probability = (1 - p)^8
At p = 0.20: 0.8^8 ≈ 16.8%
Observed naive delivery: 19/100
```

This assumes independent fragment loss. It is not a model of burst loss or of the real bidirectional protocol.

At 20% loss, approximate 95% Wilson intervals are 13–28% for naive and 95–100% for TCP.

TCP's saved median successful-transfer duration rises from about 0.1 ms at zero loss to 420.8 ms at 20% and 1019.6 ms at 30%. These are local synthetic-transfer timings, conditional on success; failures are excluded from the median.

---

## Slide 8 — Results: voice-loss estimate and FEC
**Time: 60 seconds**

### On the slide

**Modeled MOS from transport sweep**

| Configured loss | Hybrid | Naive UDP | TCP voice |
|---|---:|---:|---:|
| 0% | 4.35 | 4.35 | 4.35 |
| 10% | 3.17 | 3.06 | 4.35 |
| 20% | 2.18 | 2.08 | 4.35 |
| 30% | 1.90 | 1.90 | 4.35 |

- Hybrid and naive UDP estimates are similar.
- Small voice packets do not share the oversized handshake payload's fragmentation problem.
- TCP's flat modeled MOS does not establish superior interactive voice quality.
- Separate FEC experiment shows improved reconstruction against a silence-fill baseline.

### Speaker notes

“MOS here is an illustrative estimate from a simplified E-model with fixed codec assumptions. The runner uses configured delay and jitter, plus observed frame loss. It does not feed measured per-frame playback latency into the model.”

“That explains why TCP's score stays high when it delivers all frames, even though the metric can miss the cost of waiting for late data.”

“FEC recovery is implemented in the dedicated experiment. The current live receiver decodes received packets normally, so we must not describe the loss-slider demo as proof of integrated FEC recovery.”

### FEC backup table

| Configured loss | FEC enabled SNR | FEC disabled SNR | Lost frames | Next-packet recovery attempts |
|---|---:|---:|---:|---:|
| 0% | 99.0 dB | 99.0 dB | 0 | 0 |
| 5% | 63.6 dB | 37.3 dB | 8 | 7 |
| 10% | 60.1 dB | 30.6 dB | 11 | 10 |
| 20% | 47.4 dB | 23.9 dB | 26 | 20 |
| 30% | 33.7 dB | 10.0 dB | 39 | 28 |

Qualifications:

- SNR reference is each encoder variant's undamaged decode, not original speech.
- Input is a generated signal, not a recorded speech corpus.
- Unrecoverable/missing frames use zeros. This is not a comparison against optimized Opus packet-loss concealment.
- The counter increments when next-packet FEC decoding is attempted; it does not independently verify that the packet carried redundancy.
- 99 dB is a numerical cap for zero measured error, not a physical audio-quality measurement.
- Do not generalize these SNR differences to perceived speech improvement.

Assets: [MOS figure](../results/loss_vs_mos.png), [FEC figure](../results/fec_recovery.png). Keep the FEC table as backup if time is short.

---

## Slide 9 — Limitations and remaining work
**Time: 50 seconds**

### On the slide

- Validate the exact two-device configuration used for the review.
- Measure complete authenticated setup under impairment and a common deadline.
- Add real-time pacing and per-frame delivery/playback deadlines to evaluation.
- Integrate and validate FEC recovery in the live receive path.
- Expand beyond local emulation to controlled two-device tests.
- Finish report sections and align claims with measured evidence.

### Speaker notes

“The core modules and evaluation infrastructure exist. Our next work improves the validity of the measurements and validates the integrated application under realistic conditions.”

“Stretch work includes adaptive jitter buffering, path-MTU discovery and more detailed perceptual evaluation. These are future items, not completed features.”

Keep ns-3 as supporting evidence. Its QUIC path is a simplified model, not a real QUIC implementation. Its TCP latency accounting has documented limitations.

---

## Slide 10 — Conclusion
**Time: 30 seconds**

### On the slide

- Implemented post-quantum setup and encrypted voice over separate transports.
- Added executable tests, a GUI/CLI demo and reproducible experiment scripts.
- Observed zero fragments in the captured hybrid benchmark traffic.
- Measured stronger delivery of handshake-sized payloads over TCP under loss.
- Next: complete-call validation and more realistic audio measurement.

### Closing script

“Our interim contribution is a working transport prototype and a set of controlled experiments. The strongest evidence currently concerns packet sizing and large-payload delivery. We have identified the additional measurements needed to support broader claims about call setup and real-time voice quality.”

---

# Appendix A — Demo runbook

## Before review day

- [ ] Confirm time limit, required slide format and approved scope.
- [ ] Use the same tested build on both computers.
- [ ] Choose a LAN that allows devices to communicate; guest/campus Wi-Fi may isolate clients.
- [ ] Use headphones on both devices.
- [ ] Test microphone and local-network permissions.
- [ ] Allow the application through the firewall on the trusted private network.
- [ ] Generate one demo identity file and privately copy it to the other device.
- [ ] Keep the identity file out of slides, screenshots, public repositories and release packages.
- [ ] Test each computer as listener.
- [ ] Complete a one-minute bidirectional call.
- [ ] Change voice loss, return to zero, hang up and reconnect.
- [ ] Record a backup video of the successful run.
- [ ] Save build version, OS versions, date and observations.
- [ ] Have a copy of the figures and video available offline.

The demo identity file contains private keys for both roles. Shared distribution is a demo convenience, not a production identity-management design.

## Live sequence

| Time | Action | Narration |
|---|---|---|
| 0:00–0:20 | Listener starts on port 9000; caller uses its LAN IPv4 address | “Control establishes the session, then voice uses UDP.” |
| 0:20–0:50 | Speak in both directions | “The receiving peer decrypts and decodes the audio.” |
| 0:50–1:15 | Set one peer's outgoing loss to 20% | “This drops outgoing voice packets in this application.” |
| 1:15–1:35 | Point to counters and describe observed audio | Describe only what happens in the demonstration |
| 1:35–1:50 | Return to 0% | “We have removed injected voice loss.” |
| 1:50–2:00 | Hang up | Transition to the controlled experiment results |

If connection fails, check listener state, IP, identity, permissions and firewall briefly. Then use the recorded successful run and state that it is a recording. Do not spend the presentation troubleshooting.

See [desktop setup](DESKTOP-DEMO.md) and [demo details](DEMO.md).

---

# Appendix B — Likely panel questions and answers

### 1. What is new in your project?

“The contribution is the integration and evaluation of a TCP control channel with a size-limited encrypted UDP voice path for a post-quantum voice prototype. The cryptographic algorithms and Opus codec are existing components. We are not claiming the general use of separate control and media channels is new.”

### 2. Why use TCP for the handshake?

“It carries large application records using a reliable stream, with retransmission when data is lost. Our synthetic payload experiment demonstrates the benefit compared with a one-shot oversized UDP datagram.”

### 3. Why not use TCP for everything?

“Voice has playback deadlines. Reliable ordered delivery can make later data wait behind missing data. Our current MOS results do not quantify that playback penalty adequately; per-frame timing is planned.”

### 4. Why not QUIC?

“A real QUIC baseline would be useful but is not implemented here. The ns-3 comparison is a simplified proxy. We cannot claim to outperform real QUIC from that model.”

### 5. Why 1200 bytes?

“It is a conservative application packet bound that leaves header margin on the tested 1500-byte link. It is not a proof for every path, tunnel or MTU. Path-MTU validation is future work.”

### 6. Does TCP eliminate fragmentation?

“TCP ordinarily segments data according to its transport/path constraints. Our captured hybrid benchmark showed zero fragments. We do not claim TCP alone guarantees no IP fragmentation on every network.”

### 7. What does hybrid mean?

“TCP and UDP used together. This project does not combine classical and post-quantum key exchange.”

### 8. How are peers authenticated?

“Signatures are verified using pinned peer public keys. The custom handshake uses ML-DSA-65 signatures and ML-KEM-768 key establishment. It is not TLS or a CA-based identity system.”

### 9. Does encryption prevent replay?

“Authenticated encryption detects tampering; replay requires separate sequence/state handling. The receive buffer rejects late or duplicate sequences within its state. We do not claim a formal replay-security proof.”

### 10. Is key rotation the same as a fresh post-quantum handshake?

“No. The helper derives new material from the current key. It does not introduce a fresh independent secret. We also distinguish the tested helper from automatic rekeying in the live application.”

### 11. Why are hybrid and naive MOS close?

“Their voice frames are small and use UDP. The fragmentation comparison targets the oversized setup payload. The measured MOS difference is not evidence of a strong hybrid voice-quality advantage.”

### 12. Why is TCP MOS high?

“The simplified model uses configured delay/jitter and observed loss. It does not fully account for late playback and retransmission waiting. Delivering every frame is not the same as delivering it before its deadline.”

### 13. Did 99% of authenticated calls connect at 20% loss?

“No. Ninety-nine of 100 synthetic payload transfers succeeded over TCP under the experiment's settings. Complete authenticated call-setup success is a separate measurement we still need.”

### 14. Is the UDP comparison fair?

“It is a deliberately naive, one-shot oversized-datagram baseline that illustrates fragmentation and delivery behavior. Its timeout differs from TCP's. It does not represent every possible reliable UDP protocol.”

### 15. Why five fragments in one graph and eight in another?

“The transport sweep injects a 6445-byte payload; the survival experiment uses 10,842 bytes. At a 1500-byte IPv4 MTU, they produce different fragment counts. Captured counts under loss may be lower than the original count.”

### 16. Are your latency numbers full call-setup time?

“No. The main runner starts timing after the cryptographic handshake. Those numbers cover sending-start to first received voice frame.”

### 17. Does the live demo recover lost packets using FEC?

“The live receive path currently decodes packets normally. The separate experiment exercises next-packet FEC decoding. Integrating and validating that recovery path is remaining work.”

### 18. Are the results statistically conclusive?

“They are interim results. The transport tables contain five-run medians, and payload survival uses 100 attempts per condition. We should retain raw trials, report uncertainty, and repeat across environments before making broad claims.”

### 19. What did each team member do?

Each member should answer with actual files, commits, tests and decisions. Prepare a 30-second explanation and one piece of executable evidence per person.

### 20. What remains for the final review?

“Complete-call measurement under impairment, realistic audio timing, integrated FEC recovery, two-device validation, and completed documentation. Further security validation is needed before any production claim.”

---

# Appendix C — Evidence inventory and verification

| Claim or topic | Source |
|---|---|
| Working scope and planned ownership | [PLAN.md](../PLAN.md) |
| Handshake and algorithm choices | [handshake.py](../src/hpqv/handshake.py) |
| TCP records | [control.py](../src/hpqv/control.py) |
| Key derivation for rotation | [session.py](../src/hpqv/session.py) |
| Packet size, header and encryption | [packet.py](../src/hpqv/packet.py) |
| Conditional DF request | [udp.py](../src/hpqv/udp.py) |
| Audio settings | [audio.py](../src/hpqv/audio.py) |
| Buffer behavior | [jitter.py](../src/hpqv/jitter.py) |
| Live receive behavior | [demo.py](../src/hpqv/demo.py) |
| Main benchmark boundaries | [runner.py](../src/hpqv/eval/runner.py) |
| Payload-survival implementation | [handshake_survival.py](../src/hpqv/eval/handshake_survival.py) |
| Survival timeout settings | [run_handshake_survival.py](../scripts/run_handshake_survival.py) |
| Simplified MOS assumptions | [mos.py](../src/hpqv/eval/mos.py) |
| FEC experiment assumptions | [fec_recovery.py](../src/hpqv/eval/fec_recovery.py) |
| Saved transport results | [eval.csv](../results/eval.csv) |
| Saved delivery results | [handshake_survival.csv](../results/handshake_survival.csv) |
| Saved FEC results | [fec_recovery.csv](../results/fec_recovery.csv) |
| ns-3 limitations | [ns3 README](../ns3/README.md) |
| Report draft | [REPORT.md](REPORT.md) |

Some older report/README prose overstates the evidence. Use the qualifications in this guide when preparing slides; this file does not modify those older documents.

## Verification commands

From the repository root on the existing Apple Silicon development setup:

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q
```

The observed run returned 40 passed, 15 skipped. It also warned that local liboqs 0.15.0 differs from liboqs-python 0.16.0. Align the versions and rerun before freezing your review environment. Do not reuse the older “53 tests” count without checking.

Evaluation reproduction instructions are in [EVALUATION-REPORT.md](EVALUATION-REPORT.md). Run privileged network experiments only in the intended evaluation container. Sweep scripts overwrite the committed CSV/figure paths; use a separate checkout or preserve the previous evidence before regenerating.

## Evidence record to complete

| Field | Fill before presenting |
|---|---|
| Approved scope reference | [Document/date] |
| Demonstrated source commit or build | [Commit/build] |
| Two-device pairing | [OS and hardware] |
| Demo validation date | [Date] |
| Test command/result | [Actual latest output] |
| Historical result provenance | [Known run/build/environment; mark unknown fields] |
| Team contribution evidence | [Commits/tests/notes] |
| Backup recording | [Local file] |

---

# Appendix D — Final preparation checklist

## Must finish

- [ ] Map implemented modules to the guide-approved scope.
- [ ] Replace planned team ownership with verified contributions.
- [ ] Confirm all slide numbers match the committed CSVs.
- [ ] Relabel synthetic payload survival and post-handshake timing correctly.
- [ ] Keep MOS and FEC limitations in speaker notes.
- [ ] Rehearse the two-device call and save the backup video.
- [ ] Rerun tests in the chosen demo environment.
- [ ] Complete report abstract, introduction, related work, discussion and conclusion as needed for this review.
- [ ] Practice within the allotted time.
- [ ] Ensure every member can explain the whole architecture.

## Slide design

- Use one main point per slide.
- Limit most slides to four or five short bullets.
- Use large graph labels and consistent colors for hybrid, naive and TCP.
- Put experiment conditions directly under the chart.
- Label modeled values and synthetic experiments visibly.
- Put detailed parameters and extra tables in backup slides.
- Avoid claiming “100% secure,” “zero latency,” “always fragmentation-free,” or “better voice quality” without supporting measurements.

## Suggested rehearsal allocation

Use the following only if it matches actual contributions:

- Ashwanth: objective, architecture and cryptography.
- Cain: transport implementation and live demonstration.
- Sreenandu: methodology, results and limitations.
- All: questions, remaining work and contribution evidence.

The goal is that each claim on a slide has a source, each demonstrated feature works on the actual review devices, and each team member can explain what the evidence establishes.
