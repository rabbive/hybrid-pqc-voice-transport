# Review III speaking guide

Main slides: 8 minutes. Separate live demo: 2 minutes. Backup slides are for questions.

Memory route: Problem → Solution → Call → Tests → Fragments → Delivery → Voice → Conclusion.

## 1. A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

Target: 30 seconds.

CUE: SETUP + VOICE. Our project is a voice-chat prototype that uses different delivery methods for connection setup and conversation. Post-quantum means the cryptographic algorithms are designed to resist attacks from future quantum computers. We use existing cryptographic libraries. Our contribution is connecting them with the voice transport and evaluating the result. First, let us explain the delivery problem.

## 2. Why large messages cause trouble

Target: 55 seconds.

CUE: BIG MESSAGE → PIECES → MISSING PIECE. Before a secure call begins, the devices exchange security information. In our system this information is much larger than a normal network packet. MTU means the largest IP packet a link can carry. Our test uses 1,500 bytes. If we put too much data into one UDP message, it may be split into fragments. The receiver needs all those fragments to rebuild the original message. A missing piece is therefore enough to lose the whole message. This motivates using a different delivery method for setup.

## 3. Review II to Review III: what changed

Target: 60 seconds.

CUE: PROPOSED → BUILT → TESTED → REMAINING. In Review II, we proposed TCP for reliable connection setup and UDP for small voice packets. In Review III, that design is a working prototype. We implemented post-quantum key exchange, identity checks and encrypted Opus audio. We also added a desktop application, a working demonstration and automated tests. Evaluation now includes real sockets under controlled network loss, with ns-3 simulation as supporting evidence. These are saved experiments, not new runs. We simplified packet sizing to a fixed 1,200-byte UDP limit instead of automatically discovering the network's packet-size limit. Live recovery of missing audio and automatic adaptation are still pending. The main change is that we now have an implementation and evidence behind the proposal. Next, let us walk through how a call works.

This is the revised script for slide 3. Canva's embedded notes still contain the previous script; use this rehearsal guide for the updated explanation.

## 4. How a call works

Target: 60 seconds.

CUE: CONNECT → KEY → VOICE → PLAY. First, each device checks signatures against the peer identity key it already trusts. ML-DSA provides those signatures. ML-KEM establishes a shared secret, and HKDF turns that secret into a voice-encryption key. Next, Opus compresses microphone audio, and ChaCha20-Poly1305 encrypts it and checks for tampering. Small voice packets travel over UDP. The receiving device decrypts them, uses a reordering buffer, decodes the audio and plays it. Voice travels both ways. You do not need to explain the mathematics during the main talk. This is a custom academic protocol, not a production-audited calling system.

## 5. How we tested the prototype

Target: 50 seconds.

CUE: DROP → DELAY → MEASURE. Packet loss means some packets do not arrive. Delay means they arrive later; jitter means that delay varies. We used controlled Linux network tests with real sockets, encryption and Opus encoding. The transport sweep covers zero to thirty percent configured loss, with fifty milliseconds of configured delay and ten milliseconds of jitter. It reports medians from five runs. A separate experiment sends a large synthetic message one hundred times per condition. These are saved historical results. The main voice benchmark authenticates before measurement and does not send audio at real-time pace, so it is not a complete measurement of a live call.

## 6. Small packets avoided captured fragments

Target: 55 seconds.

CUE: READ AXES → ZERO → LIMIT. Along the bottom is the packet-loss setting. Up the side is the number of captured IP fragments. Our hybrid line stays at zero. The naive, or oversized UDP, baseline shows five fragments at the lower loss settings and four at higher settings. The lower count does not mean the message was delivered better; fragments may be missing from the capture. The important result is that the measured hybrid traffic did not produce captured fragments. The real cryptographic handshake happened before capture, so this graph alone does not prove that the entire call lifecycle is fragmentation-free.

## 7. TCP delivered the large test message more often

Target: 70 seconds.

CUE: 20% LOSS → 99 VERSUS 19 → TEST MESSAGE. The horizontal axis shows configured packet loss. The vertical axis shows the percentage of large test messages delivered. At twenty percent loss, TCP delivered ninety-nine out of one hundred, while naive UDP delivered nineteen. TCP can resend missing data. UDP sent its large message once. This is not a count of successfully authenticated calls. The original chart uses handshake wording, but the experiment only sends a synthetic message containing the same number of bytes as the cryptographic material. Also, the waiting policies differ: UDP has a one-second timeout, while TCP uses twenty-second wait settings. The result shows delivery under these policies, not an equal-time comparison. TCP gains reliability at the cost of possible waiting.

## 8. Voice estimates fall as packets go missing

Target: 65 seconds.

CUE: HIGHER SCORE → SIMILAR UDP LINES → MODEL LIMIT. MOS stands for Mean Opinion Score. Here it is calculated from a simplified model; people did not listen and rate the calls. As loss increases, the hybrid and naive UDP estimates both fall and remain close to each other. Small voice packets do not have the same oversized-message problem as setup traffic. The TCP line stays high because it delivers the frames, but the model does not fully capture the cost of waiting for late voice data. We therefore cannot claim that TCP gives a better live conversation, or that our hybrid system improves perceived voice quality. Our strongest current results concern packet sizing and delivery of large messages.

## 9. What we have shown, and what comes next

Target: 35 seconds.

CUE: BUILT → MEASURED → NEXT. We built the transport integration and encrypted voice prototype. Our controlled results show zero captured fragments in hybrid benchmark traffic and stronger delivery of a large test message using TCP. Next, we need complete authenticated-setup measurements with a common deadline, realistically paced audio tests and integration of the separate recovery method into the live receiver. We will now show our two-minute live demonstration. STOP THE MAIN DECK HERE. Slides 10 to 13 are for questions only.

## 10. Backup · Recovering missing audio

Backup only.

CUE: RECOVERY EXPERIMENT, NOT LIVE FEATURE. FEC means forward error correction. The tested decoder attempts to use information in the next packet to recover a missing frame. The graph shows signal-to-noise ratio, or SNR: higher means closer to the undamaged decoded reference. This used a three-second generated signal, not recorded speech. The comparison fills unrecovered gaps with zeros, not an optimized speech-concealment method. The 99 dB value is a numerical cap when no error is measured. The attempt counter does not independently verify that a packet contained redundancy. These findings do not prove perceived speech improvement, and this recovery behavior is not integrated into the current live receive path.

## 11. Backup · Time to the first received voice frame

Backup only.

CUE: FIRST RECEIVED FRAME, NOT FULL CALL DELAY. The horizontal axis is the configured network delay, and the vertical axis is time to the first received voice frame in milliseconds. TTFB is shorthand for time to first byte in the image. For this prototype benchmark, use the precise first-received-frame definition. The cryptographic handshake is completed before this timer begins. Frames are sent without real-time pacing. The figure does not measure microphone-to-speaker delay or complete call setup. Do not compare its numbers directly with the ns-3 timing graph, because the models use different timing definitions.

## 12. Backup · Simulated voice-quality estimates

Backup only.

CUE: SIMULATION ONLY. ns-3 is a network simulator. This is a separate modeled comparison, not another real-prototype measurement. Its MOS scores are estimated. The QUIC path is simplified and is not a real QUIC implementation. The TCP model does not fully represent playback delays, which limits what we can infer from its high score. Use this graph as supporting evidence only, rather than ranking actual calling applications.

## 13. Backup · Simulated first-byte timing

Backup only.

CUE: DIFFERENT MODEL, DIFFERENT TIMER. This graph reports first-byte timing in the ns-3 model. The simplified QUIC proxy has lower modeled timing here. That is a result of the modeled behavior and assumptions, not a measurement of a deployed QUIC application. TCP latency accounting has limitations. Do not compare the absolute values with the prototype first-frame chart: the two experiments time different portions of their processes. A fair end-to-end comparison remains future work.
