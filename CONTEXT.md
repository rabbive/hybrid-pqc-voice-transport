# Context / Glossary

Project: A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat.
Team: Cain Manoj, Sreenandu G, Ashwanth Kumaravel. Guide: Dr. Renuka Devi S.

Glossary of the domain language. No implementation details — see docs/adr/ for decisions.

## Hybrid Transport
The core design: two parallel channels between two peers — a reliable **Control Channel**
for setup, and an unreliable **Data Channel** for voice. "Hybrid" = TCP + UDP together,
not one or the other.

## Control Channel
The **TCP** path. Carries the PQC handshake and ongoing control signaling (key rotation,
call setup/teardown). Reliable and stream-oriented, so oversized PQC blobs never fragment
at the datagram level. Contrast: **Data Channel**.

## Data Channel
The **UDP** path. Carries encrypted real-time voice frames. Unreliable by choice — a lost
voice frame is skipped, never retransmitted (retransmit = too slow for real-time).
Contrast: **Control Channel**.

## Handshake
The one-time PQC key exchange over the Control Channel. Peer sends a Dilithium-signed
Kyber public key; other peer encapsulates; both derive a shared secret. Custom minimal
protocol, not TLS. Output feeds the symmetric key for the Data Channel.

## Fragmentation-Free
The thesis. A packet is fragmentation-free when it crosses the network without IP-layer
fragmentation. Achieved by keeping every datagram under a conservative MTU cap with the
Don't-Fragment bit set. The project's central claim: our design fragments zero packets
where the naive baseline fragments and drops.

## Framing
App-layer segmentation: the application chops payloads to fit under the MTU cap *itself*,
so the IP layer never has to. Distinct from IP fragmentation, which we forbid.

## MTU Cap
The conservative maximum datagram size (~1200 bytes) every Data Channel packet stays under.
Below the 1500-byte Internet MTU with margin for headers.

## FEC (Forward Error Correction)
Redundant data added to the voice stream so the receiver reconstructs a lost frame without
retransmission. Primary mechanism: Opus in-band FEC.

## Baseline
A comparison system the hybrid is measured against. The villain baseline is
**naive-PQC-over-UDP** (whole handshake dumped in UDP datagrams → fragments → drops).
Others: plaintext UDP, TCP/TLS-PQC.

## MOS (Mean Opinion Score)
Perceived voice quality, 1–5. Computed from delay/jitter/loss via the ITU-T E-model
(G.107), not from live listeners.

## TTFB (Time To First Byte)
Latency of the handshake — time from connection start to first usable voice.
