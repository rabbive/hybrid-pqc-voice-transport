# Related work: other post-quantum voice projects on GitHub

Compared from each project's public README on 2026-10-16; not independently built
or tested. No code from these projects is used (none has a license, so their code
is all rights reserved).

| | **hpqv v2 (this project)** | [AEGIS-VoIP](https://github.com/MuhammadMusaab-UlHaq/AEGIS-VoIP-Post-Quantum-Secure-Voice-Video-Communication-System) | [Quantum-Safe-Video-Conference-App-V3](https://github.com/Shubhamg-1811/Quantum-Safe-Video-Conference-App-V3) | [thilakragav FYP](https://github.com/thilakragav/FYP_Post-Quantum-Encrypted-and-Obfuscated-Voice-Obfuscation-System) |
|---|---|---|---|---|
| Key exchange | X25519MLKEM768 in TLS 1.3 (classical + post-quantum) | X25519 + Kyber-1024 | Kyber-768 | Kyber-512 |
| Peer authentication | mutual, pinned ML-DSA-65 certificates | SAS (ZRTP-inspired) | Dilithium3 signature; README assumes the Dilithium key was distributed securely | not stated (README: the key registry has no authentication) |
| Media encryption | ChaCha20-Poly1305, independent per-direction keys | DTLS-SRTP + AES-256-GCM (insertable streams) | SRTP, AES-256-ICM + HMAC-SHA1-80 | AES-256-GCM |
| Human check against a man in the middle | SAS + fingerprint shown before answering | SAS | recommends pre-shared keys, TOFU or QR | not stated |
| Replay protection on media | 256-packet sliding window | not stated | not stated | not stated |
| Audio | full duplex | full duplex (WebRTC) | full duplex | one-way |
| Large handshake vs MTU | handshake on TCP; every voice datagram ≤ 1200 B | not discussed | not discussed | not discussed |
| Measured evaluation | `tc netem` loss sweeps, IP fragment counts, handshake completion under loss, E-model MOS, FEC SNR, all measured on the **v1** protocol ([report](EVALUATION-REPORT.md)); v2 not yet re-measured | not stated | performance claims (<1 ms crypto, <100 ms LAN) with no method given | live latency/jitter/loss display |

## What we took from them

- **Read-aloud code (SAS):** AEGIS-VoIP also uses one. Ours comes from the TLS
  exporter and appears in the incoming-call prompt and the Security tab.
- **Verify before you trust:** Quantum-Safe-Video-Conference's README advises
  checking the peer's key out of band (TOFU or QR). Our incoming-call prompt shows
  the caller's certificate fingerprint before the call is answered.
- **The eavesdropper's view:** thilakragav's project lets you hear what an
  unauthorised listener would hear. We show the same idea as data: the
  *What an eavesdropper sees* panel and the [Wireshark guide](DEMO-WIRESHARK.md).

## One line for the slide

Of the four projects compared, only hpqv reports measurements of how a
post-quantum handshake behaves under packet loss (measured on our v1 protocol;
v2 not yet re-measured).
