# Original requirements (verbatim, supplied by the user on 2026-10-16)

Task: Implement Core Security and Voice Resilience Features in a Hybrid PQC Voice Transmission Application

You are a senior cybersecurity engineer and Python networking developer. I am building a real-time, end-to-end encrypted voice transmission application using Python, post-quantum cryptography (PQC), TCP, UDP, and Opus.

Your task is to inspect my existing repository and implement the following five features into the existing application. Do not blindly rewrite the project. Understand the current architecture, reuse working code, and integrate changes cleanly.

Existing architecture

- Language: Python
- TCP control channel: Peer identity authentication, certificate handling, session negotiation and control messages.
- UDP media channel: Real-time voice transmission using the Opus codec.
- Cryptography: Hybrid classical and post-quantum key establishment.
- Audio resilience: Opus in-band FEC and packet-loss concealment where supported.
- Objective: Secure, low-latency voice communication that remains usable under unreliable network conditions.

First inspect the repository, identify its entry points, modules, dependencies, cryptographic implementation, audio pipeline, packet formats and existing tests. Summarize the current architecture and propose an implementation plan before making changes.

Do not replace existing working components without a technical reason. Do not invent files, dependencies, APIs or completed functionality that do not exist.

Feature 1: Hybrid PQC Key Establishment and Session Security

Implement a secure hybrid key-establishment protocol using:

- X25519 for classical elliptic-curve Diffie-Hellman.
- ML-KEM-768, standardized in NIST FIPS 203, for post-quantum key establishment.
- HKDF-SHA-256 or another appropriately selected standardized KDF for deriving session traffic keys.
- A vetted cryptographic library for all cryptographic primitives.

Requirements:

1. Combine the classical and post-quantum shared secrets using a reviewed hybrid construction. Do not invent an ad hoc concatenation or key-combination protocol. Use a documented, standards-aligned construction where available.
2. Authenticate the handshake and bind peer identities, negotiated algorithms, session identifiers, protocol version and relevant handshake messages into the authenticated transcript.
3. Use TLS 1.3 with appropriate certificate validation for the TCP control channel, or integrate an equivalent reviewed authenticated handshake design if the existing architecture requires a custom protocol.
4. Ensure the identity authentication mechanism actually authenticates the peer, not merely encrypts the connection.
5. Derive independent directional traffic keys and separate keys for distinct cryptographic purposes.
6. Protect against downgrade attacks, replayed handshakes, mismatched session parameters and unauthenticated peer impersonation.
7. Use cryptographically secure randomness.
8. Handle failed handshakes safely. Never fall back silently to classical-only encryption when PQC negotiation fails.
9. Do not log private keys, shared secrets, traffic keys, authentication tokens or sensitive handshake material.
10. Document the chosen cryptographic construction and its limitations.

Expose clear interfaces for establishing a session, obtaining directional traffic keys and closing a session. Adapt these interfaces to the existing codebase instead of creating unnecessary duplicate abstractions.

Feature 2: Authenticated and Encrypted UDP Voice Transport

Secure the existing UDP voice pipeline.

Requirements:

1. Encrypt outgoing Opus payloads using a vetted AEAD construction, such as ChaCha20-Poly1305 or AES-GCM.
2. Authenticate packets before passing their payloads to the Opus decoder.
3. Use a unique nonce for every encryption operation under a given key. Never reuse a nonce with the same key.
4. Use a per-session packet sequence number and session identifier. Authenticate relevant header fields as associated data.
5. Maintain separate sending and receiving keys.
6. Reject duplicate, replayed, stale or invalidly authenticated packets using a properly designed replay-protection window.
7. Handle packet reordering and sequence-number boundaries safely.
8. Reject malformed, oversized and truncated packets before expensive processing.
9. Prevent unauthenticated packets from triggering state changes or consuming excessive CPU and memory.
10. Ensure packets from previous sessions cannot be accepted in a newly established session.
11. Keep the media path independent of TCP retransmission delays. TCP should handle control messages; UDP should carry time-sensitive audio.
12. Implement clean session termination and safe handling of unexpected peer disconnections.

Do not assume that TLS on the TCP connection automatically protects UDP traffic. Explicitly bind the UDP media session to the authenticated session and its derived keys.

Design the packet format with clear versioning, bounded field lengths and documented byte ordering. Minimize header overhead.

Feature 3: Adaptive Opus FEC, Packet-Loss Concealment and Jitter Buffer

Improve voice quality under network impairment without sacrificing low latency.

Requirements:

1. Inspect the existing Opus implementation and preserve its working encoding and decoding pipeline.
2. Enable and configure Opus in-band FEC where supported by the selected binding.
3. Configure packet-loss expectations using measured network conditions and supported Opus controls.
4. Use packet-loss concealment when recovery data is unavailable.
5. Implement a bounded jitter buffer that handles variable packet arrival times, packet reordering and late packets.
6. Monitor packet loss, burst loss, jitter, packet arrival intervals, late arrivals and buffer occupancy.
7. Adapt Opus bitrate and supported FEC settings conservatively using smoothed measurements and hysteresis to prevent rapid oscillations.
8. Keep target latency bounded and expose configurable latency limits.
9. Avoid unnecessary buffering or blocking operations in the audio callback and time-sensitive media path.
10. Ensure that missing packets are handled correctly according to Opus FEC semantics. Recovery information for a missing frame may be carried in a subsequent packet, so decoding and buffering must account for that.
11. Make FEC, concealment and adaptation independently configurable for testing.
12. If the installed Opus binding lacks a required control, document the limitation and use a compatible implementation rather than silently pretending the feature works.

Do not assume that enabling FEC always improves quality. Account for its bandwidth cost, recovery delay and the possibility of consecutive packet losses.

Feature 4: Live Diagnostics and Monitoring Dashboard

Integrate a diagnostics interface that fits the existing application. If there is already a GUI, extend it rather than introducing an unrelated frontend.

Display the following metrics where measurable:

- Current connection and authentication status.
- Negotiated cryptographic suite and whether hybrid PQC was successfully established.
- Handshake duration.
- Encrypted packet transmission and reception rates.
- Packet loss and burst-loss estimates.
- RTT, where measurable.
- One-way audio latency only when clocks and measurement methodology support it.
- Jitter and jitter-buffer occupancy.
- Estimated FEC recovery events and packet-loss concealment events.
- Current Opus bitrate and FEC configuration.
- Audio underruns, late packets and dropped packets.
- CPU usage and memory consumption.
- Session rekeying status, if rekeying is implemented.

Requirements:

1. Collect metrics using lightweight counters and rolling statistics.
2. Do not block audio capture, encoding, packet transmission or decoding to update the dashboard.
3. Separate cryptographic overhead, network behavior and audio-processing metrics.
4. Distinguish directly measured values from estimates.
5. Avoid logging sensitive audio, cryptographic secrets or private identity material.
6. Include a resettable session statistics view and a way to export benchmark data to CSV or JSON.
7. If a GUI does not exist, propose the smallest suitable implementation consistent with the current project architecture.

Feature 5: Network Impairment Simulator and Automated Testing

Create a reproducible test system for evaluating security and voice quality.

The simulator should support configurable:

- Random packet loss.
- Burst packet loss.
- Artificial delay.
- Jitter.
- Packet reordering.
- Packet duplication.
- Bandwidth constraints where practical.
- Temporary network outages.

Support repeatable profiles such as:

- Clean network.
- 1% packet loss.
- 5% packet loss.
- 10% packet loss.
- 20% packet loss.
- Burst-loss scenarios.
- High latency and high jitter.
- Temporary disconnection and reconnection.

Requirements:

1. Prefer an isolated test harness or a dedicated test proxy so simulated impairment does not disrupt unrelated network traffic.
2. Use deterministic random seeds where possible.
3. Run automated tests against both the sending and receiving pipelines.
4. Verify that modified ciphertext, invalid authentication tags, replayed packets, malformed headers and packets from previous sessions are rejected.
5. Test out-of-order packet delivery and ensure the replay window does not incorrectly reject legitimate reordered packets.
6. Verify that network impairment does not cause unbounded memory growth, unbounded jitter-buffer delay or application deadlocks.
7. Compare FEC-disabled, FEC-enabled and adaptive-FEC modes under identical conditions.
8. Compare classical-only and hybrid-PQC handshakes in an isolated benchmark mode, but never silently weaken production security to obtain a baseline.
9. Record handshake duration, handshake bytes, CPU usage, packet delivery statistics, latency, bitrate and audio-quality measurements where available.
10. Generate machine-readable CSV or JSON results suitable for plotting.
11. Include repeatable commands for running tests and benchmarks.
12. Use synthetic audio or an explicitly supplied test recording. Do not record real conversations without consent.

Where possible, evaluate decoded audio using an appropriate objective speech-quality metric, such as POLQA where licensed and available, or another suitable metric. Clearly distinguish objective measurements from subjective listening tests.

Engineering requirements

- Follow the existing project structure and naming conventions.
- Use type hints, clear interfaces, structured logging and meaningful error handling.
- Keep dependencies minimal and document every new dependency.
- Avoid unnecessary abstractions, global mutable state and duplicated implementations.
- Use bounded queues and buffers throughout the real-time audio and networking pipelines.
- Keep expensive cryptographic handshakes and benchmark operations off the audio callback.
- Ensure that blocking socket operations cannot freeze the UI or audio processing.
- Implement clean shutdown, socket cleanup and thread or task cancellation.
- Never implement cryptographic primitives yourself.
- Never claim a feature is secure merely because a test passes.
- Do not expose cryptographic secrets in logs, metrics, crash reports or exported benchmark files.
- Preserve compatibility with existing functionality unless a change is necessary and documented.

Required testing

Add or update tests covering:

1. Successful authenticated hybrid session establishment.
2. Certificate validation failures and invalid peer identities.
3. Handshake tampering and negotiation failures.
4. Correct directional key separation.
5. UDP encryption and decryption round trips.
6. Invalid ciphertext, nonce misuse protections and replay rejection.
7. Legitimate packet reordering.
8. Malformed and oversized packet rejection.
9. Opus FEC recovery and packet-loss concealment.
10. Jitter-buffer behavior and bounded latency.
11. Adaptive bitrate and FEC behavior under changing network conditions.
12. Dashboard metric accuracy.
13. Reproducibility of impairment simulations.
14. Graceful handling of disconnections and shutdown.
15. Regression tests for existing application functionality.

Use mocks for cryptographic or network failure scenarios when appropriate, but include integration tests that exercise the actual selected cryptographic libraries and real local sockets.

Implementation workflow

Proceed in the following order:

Phase A: Repository inspection

- Identify the application entry points, architecture, existing tests and dependencies.
- Identify the existing PQC, certificate, TCP, UDP, Opus and FEC implementations.
- Report security issues and missing components.
- Propose the smallest safe implementation plan.

Phase B: Security foundation

- Implement or correct authenticated hybrid key establishment.
- Implement secure key derivation and session binding.
- Integrate authenticated UDP media encryption and replay protection.
- Add security tests before proceeding.

Phase C: Audio resilience

- Implement or improve the jitter buffer, Opus FEC, packet-loss concealment and adaptive controls.
- Add audio-pipeline tests.

Phase D: Observability and simulation

- Add diagnostics and exportable metrics.
- Implement the network impairment simulator and reproducible benchmarks.

Phase E: Validation and documentation

- Run all relevant tests.
- Fix regressions introduced by the changes.
- Document architecture, threat model, protocol assumptions, setup instructions and benchmark commands.
- Provide a concise implementation report.

After each phase, summarize files changed, tests run, results, known limitations and remaining work. Do not report a test as passed unless it was actually executed and passed.

Begin with Phase A. Do not start by replacing the entire application or making speculative changes before understanding the repository.