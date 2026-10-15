# Review 2 to Review 3: what changed

Project: A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

Prepared for Review 3 on 16 September 2026.

This comparison uses the supplied Review 2 presentation, the current source code, and saved experiment results. Review 2 is the proposal baseline; this is not a commit-by-commit history. The experiments were not rerun for this document.

## What to say first

> In Review 2, we proposed separating the large post-quantum handshake from real-time voice traffic. For Review 3, we have implemented that architecture using real TCP and UDP sockets, post-quantum key establishment and authentication, and encrypted Opus audio. We added a desktop demonstration, automated tests, and controlled experiments. We also refined the scope: we use a fixed packet-size cap instead of dynamic path-MTU discovery, and NS-3 now supports the real prototype as a secondary model.

## 1. Direct comparison with Review 2

| Topic | What we claimed in Review 2 | What we have for Review 3 | Status |
|---|---|---|---|
| Dual-path architecture, slide 7 | TCP for PQC handshake/control and UDP for voice. | Python prototype with a real TCP handshake and encrypted UDP voice path. | Core design implemented. |
| PQC algorithms, slides 7 and 10 | Kyber for key establishment and Dilithium for authentication. | ML-KEM-768 and ML-DSA-65 through liboqs. These are the standardized algorithm names used in the implementation. | Concrete algorithm choices implemented. |
| Authentication, slide 10 | Authentication was named without a detailed exchange. | Both peers verify signatures against pinned public keys. The responder signs the initiator KEM public key and ciphertext together. | Mutual authentication made explicit. |
| Voice encryption, slide 7 | Symmetric keys from the handshake, described as having zero encryption latency. | HKDF-SHA256 derives a 256-bit key and direction-specific nonce prefixes; ChaCha20-Poly1305 encrypts and authenticates voice frames. | Implemented; zero encryption latency is not a valid claim. |
| Audio capture, slide 10 | Capture and compress audio using a codec such as Opus. | Microphone/speaker and WAV-file paths, using Opus at 48 kHz mono with 20 ms frames. | Implemented. |
| Application framing, slide 8 | Dynamically segment PQC payloads and voice packets at the application layer. | TCP messages have a four-byte length prefix. TCP handles stream segmentation. Each voice frame is sealed into one UDP datagram; oversized datagrams are rejected. | Simpler framing implemented; no general application fragmentation/reassembly engine. |
| MTU handling, slide 8 | Discover the path MTU and dynamically keep packets below it. | Fixed 1200-byte cap on UDP payloads, including the application header and authentication tag. DF is requested where the socket API exposes the required options. | Dynamic PMTU discovery remains unimplemented. |
| Middlebox compatibility, slide 8 | Ensure passage through legacy firewalls and routers. | Small UDP packets reduce the need for IP fragmentation. Existing measurements use a controlled local environment. | No general firewall, NAT, or middlebox traversal guarantee demonstrated. |
| FEC, slide 9 | Embed redundancy to reconstruct lost voice packets. | Opus in-band FEC is enabled in the encoder, and a separate experiment exercises next-packet FEC decoding. | Recovery experiment implemented; live receiver recovery remains incomplete. |
| Adaptive recovery, slide 9 | Adapt recovery to packet loss to preserve MOS. | Encoder loss expectation is fixed at 10%. The GUI can inject loss manually, but this is a demonstration control. | No automatic network-feedback adaptation. |
| Reliable control and key rotation, slide 9 | Selective retransmission for critical state changes and rotations. | TCP provides retransmission. Control message types and a key-rotation helper exist; call-start signaling is used in the demo. | Ongoing rotation and call-end signaling are not integrated into the live call loop. |
| Evaluation platform, slide 11 | NS-3 simulation/emulation. | Real prototype evaluated using Linux `tc netem` and packet capture; NS-3 added as a secondary size-and-delay model. | Evaluation expanded into two tracks. |
| Baselines, slide 11 | Compare with standard TCP/TLS and pure QUIC. | Real comparisons use hybrid, naive oversized UDP, and TCP voice. NS-3 includes a simplified UDP/1-RTT QUIC proxy. | No full TLS or production QUIC comparison. |
| Metrics, slide 11 | MOS, PQC handshake TTFB, and jitter drop rate. | Saved fragment counts, arrival/drop statistics, first-frame timing, E-model MOS estimates, payload-delivery survival, and separate FEC reconstruction results. | More evidence, with narrower metric definitions explained below. |
| Demonstration | Review 2 described a workflow. | GUI and CLI, listener/caller roles, audio meters, packet counters, a live loss slider, and Windows/macOS packaging workflow. | Demonstration code and packaging added. |

## 2. The main implementation changes

### A working voice pipeline

The implemented path is:

```text
TCP connection
  -> signed ML-KEM/ML-DSA handshake
  -> verify pinned peer identities
  -> derive symmetric key and directional nonce prefixes
  -> exchange caller UDP port

Microphone or WAV input
  -> 20 ms PCM frame
  -> Opus encoding
  -> ChaCha20-Poly1305 encryption
  -> enforce 1200-byte UDP payload cap
  -> UDP transmission
  -> authenticate and decrypt
  -> sequence-based jitter buffer
  -> normal Opus decoding
  -> speaker or WAV output
```

The jitter buffer provides basic ordering and duplicate/late-sequence rejection. It is not a full playback scheduler with measured per-frame deadlines.

Source: [handshake](../src/hpqv/handshake.py), [control](../src/hpqv/control.py), [packet format](../src/hpqv/packet.py), [audio](../src/hpqv/audio.py), [live demo](../src/hpqv/demo.py), and [jitter buffer](../src/hpqv/jitter.py).

### More explicit security design

The current exchange authenticates both sides, including the responder's ACCEPT message. The handshake contains 10,842 bytes of cryptographic material, excluding version bytes and TCP record length prefixes.

The two voice directions use separate nonce prefixes. Identity trust is configured out of band; the demo uses a shared identity file containing both demo identities. This remains an academic prototype, not an audited production protocol.

Here, “hybrid” means TCP plus UDP. The implementation does not combine a classical KEM with a post-quantum KEM.

Source: [authentication decision](adr/0002-add-responder-authentication.md) and [wire format](wire-format.md).

### A practical packet-size rule

The prototype replaces dynamic PMTU discovery with a conservative fixed limit. Its UDP payload contains an 11-byte application header, the encrypted Opus frame, and a 16-byte authentication tag. IP and UDP headers are additional.

This is straightforward to implement and test. It does not guarantee delivery on every possible path, particularly if the path MTU is smaller than the resulting IP packet or UDP is blocked.

Source: [packet size enforcement](../src/hpqv/packet.py) and [UDP socket setup](../src/hpqv/udp.py).

## 3. New evidence available for Review 3

### Fragmentation measurements

The saved main loss sweep records zero captured IP fragments for the hybrid scenario at 0%, 5%, 10%, 20%, and 30% configured loss. The naive scenario records four or five.

The main benchmark's naive payload is 6,445 bytes. The separate survival experiment uses 10,842 bytes, corresponding to eight theoretical IPv4 fragments at MTU 1500. These are different experiments, so do not describe the four-to-five and eight figures as the same measurement.

The main sweep establishes cryptographic sessions before impairment and packet capture. Its zero-fragment result should not be described as a capture of the complete authenticated setup under loss.

Source: [saved main results](../results/eval.csv) and [benchmark implementation](../src/hpqv/eval/runner.py).

### Delivery of a handshake-sized payload under loss

The separate experiment runs 100 attempts per transport per loss level.

| Configured packet loss | Naive UDP payload delivery | TCP payload delivery |
|---|---:|---:|
| 0% | 100% | 100% |
| 5% | 75% | 100% |
| 10% | 39% | 100% |
| 20% | 19% | 99% |
| 30% | 2% | 83% |

These are saved results for delivery of a random 10,842-byte payload sized to represent the cryptographic material. Each trial does not execute the full authenticated handshake. The naive UDP path has a one-second timeout and no retry; TCP has a twenty-second timeout and transport retransmissions.

Use this wording:

> Our controlled payload-delivery experiment shows that TCP delivers handshake-sized data more reliably than one oversized, unretried UDP datagram under the tested loss conditions.

Avoid turning the table into a claim that 99% of complete real calls connect at 20% loss under equal deadlines.

Source: [saved survival results](../results/handshake_survival.csv), [experiment code](../src/hpqv/eval/handshake_survival.py), and [sweep settings](../scripts/run_handshake_survival.py).

### Separate FEC experiment

At 20% configured loss, the saved experiment reports:

- 26 frames lost and 20 next-packet recovery operations.
- About 47.42 dB segmental SNR with FEC-enabled reconstruction.
- About 23.89 dB with FEC disabled and silence filling missing frames.

This uses a three-second synthetic signal and compares each reconstruction against its own undamaged decode. It is not a listening test. A recovery-operation count does not establish perfect reconstruction of every recovered frame.

The live receiver currently calls normal Opus decoding; it does not detect missing frames and invoke next-packet FEC recovery.

Source: [FEC experiment](EXPERIMENT-fec-recovery.md), [saved results](../results/fec_recovery.csv), and [live receiver](../src/hpqv/demo.py).

### More reproducible evaluation

The repository now contains automated tests, experiment scripts, saved CSVs, graphs, and a Linux evaluation container. These make the work inspectable and repeatable.

NS-3 remains useful for controlled transport modeling, but its QUIC path is a proxy and its TCP delay accounting understates head-of-line blocking.

Source: [evaluation report](EVALUATION-REPORT.md), [NS-3 model limitations](../ns3/README.md), and [evaluation decision](adr/0003-ns3-as-secondary-evaluation-model.md).

## 4. Claims to update before presenting

| Earlier or overly broad wording | Accurate Review 3 wording |
|---|---|
| Zero encryption latency. | Voice uses symmetric authenticated encryption after PQC setup; processing cost is not zero. |
| Dynamic PMTU-aware packaging. | Fixed 1200-byte UDP payload cap; dynamic PMTU discovery is future work. |
| All PQC keys individually exceed 1500 bytes. | Our combined authenticated handshake messages exceed a typical 1500-byte MTU; individual components differ in size. |
| Fragmentation-free on every network. | The tested hybrid scenario recorded zero fragments; packet sizing and platform/path conditions bound the claim. |
| We bypass firewalls and middleboxes. | We avoid oversized UDP handshake datagrams; real middlebox traversal has not been established. |
| Live adaptive FEC recovery is complete. | FEC encoding and a separate recovery experiment exist; live recovery and automatic adaptation remain. |
| TTFB measures full PQC setup. | The main prototype benchmark times send-start to first received voice frame after setup. |
| MOS proves listeners prefer our audio. | MOS is an E-model estimate. In the main runner it uses configured delay/jitter and measured delivery loss, not listener ratings or decoded-audio scoring. |
| We outperform actual QUIC/TLS. | We compared transport arrangements and a simplified QUIC proxy, not full production stacks. |
| Hybrid always gives better voice quality than naive UDP. | The saved MOS curves are similar; the clearest demonstrated benefit concerns avoiding oversized handshake datagrams. |

The main voice benchmark sends frames without real-time pacing and does not measure playback deadlines. Its metrics should not be presented as a complete assessment of conversational audio quality.

## 5. Remaining work to acknowledge

- Integrate missing-frame detection and next-packet FEC recovery into live playback.
- Add automatic loss feedback if adaptive recovery remains a project objective.
- Integrate live key rotation and complete control-message handling.
- Measure the complete authenticated setup under impairment with a common completion deadline.
- Evaluate paced speech, per-frame playback deadlines, and realistic burst loss.
- Validate the intended two-device demonstration and representative real network paths.
- Add dynamic PMTU handling if it remains required by the original proposal.
- Use full QUIC/TLS implementations before making claims about those protocols.

## 6. A short answer for the review panel

> The core architecture has stayed the same since Review 2: reliable TCP setup and small UDP voice packets. What changed is that we now have the actual cryptographic exchange, encrypted Opus audio, a GUI demonstration, and saved experiments. We simplified MTU handling to a fixed 1200-byte limit and expanded evaluation beyond NS-3 to real sockets with controlled impairment. Our strongest current evidence is the fragmentation measurement and the reliability of TCP delivery for handshake-sized payloads. Live FEC recovery, complete setup timing, and broader network validation are the next steps.

