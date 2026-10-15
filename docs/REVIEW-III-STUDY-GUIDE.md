# Review III: study guide in simple English

**For the review on 17 September 2026**  
Prepared on 16 September 2026  
Project: A Hybrid TCP-UDP Transport Protocol for Fragmentation-Free Post-Quantum Voice Chat

This is your personal preparation guide. Read it to understand the project and explain it in your own words. The separate [presentation guide](REVIEW-III-PRESENTATION-GUIDE.md) contains slide content and detailed evidence.

## How to study tonight

If you have about two hours:

| Time | What to do |
|---|---|
| 20 minutes | Read sections 1–4. Explain the problem without looking at the page. |
| 25 minutes | Read sections 5–8. Draw the architecture and explain the handshake. |
| 25 minutes | Read sections 9–11. Practice explaining each main graph. |
| 20 minutes | Read the panel questions and answer them aloud. |
| 20 minutes | Rehearse your actual slides and demo with your team. |
| 10 minutes | Read the final revision sheet and check your files. |

If you have only 30 minutes, read sections 1, 5, 10, 12 and 15.

You do not need to memorize every number. You need to know the problem, the design, what works, what each experiment measures, and what remains.

---

## 1. Your project in one minute

### The shortest explanation

> We use TCP to carry large post-quantum setup messages and UDP to carry small encrypted voice packets.

### The problem

Before two users can talk securely, their computers need to establish encryption keys and check identities. This setup is called a handshake.

Our post-quantum handshake contains large public keys and signatures. If we put a large message into one UDP datagram, it can be split into IP fragments. If a fragment is missing, the complete datagram cannot be reconstructed.

### Our approach

- Put the handshake on a reliable TCP connection.
- Send voice using small UDP datagrams.
- Encrypt the voice.
- Limit every application voice datagram to 1200 bytes.
- Measure behavior under controlled packet loss.

### What you can say to the panel

> “Our project separates connection setup from voice transmission. TCP carries the large setup messages and handles retransmission. UDP carries small encrypted voice packets. We have implemented the main modules, a calling interface and evaluation scripts. Our strongest current results concern captured fragmentation and delivery of large synthetic payloads under packet loss.”

### What is our contribution?

We did not invent TCP, UDP, Opus or the cryptographic algorithms.

Our contribution is the prototype that combines them for this problem, plus the experiments that examine the design. Do not claim that separating control and media channels is a completely new idea.

---

## 2. Networking from the beginning

### What happens when you speak into a computer?

A microphone turns sound into an electrical signal. The computer represents that sound as numbers. It processes those numbers and sends bytes across a network.

The receiver turns the received data back into sound.

### Bit and byte

- A bit is a 0 or a 1.
- A byte contains 8 bits.
- Packet sizes in our project are usually measured in bytes.
- Network rates are often given in bits per second.

Do not confuse 1200 bytes with 1200 bits.

### Packet

A packet is a unit of data sent through a network. It contains useful data and headers that help deliver and interpret it.

### Payload and header

The payload is the data being carried. A header contains information about that data.

Think of a letter: the message is the payload; addressing and delivery information are like headers. This is only an analogy, not the exact protocol format.

### IP address and port

An IP address identifies a network interface. A port helps the operating system deliver traffic to the correct application.

For our demo, the listener uses TCP port 9000 and UDP port 9000. TCP and UDP have separate port spaces, so using the same number is allowed.

### Socket

A socket is the program's interface for sending and receiving network data.

Our Python code uses real sockets. The main prototype is not only a simulation.

### LAN and loopback

- A LAN is a local network, such as devices connected to the same suitable Wi-Fi/router.
- Loopback sends traffic within one computer, commonly using 127.0.0.1.

Most committed network measurements use Linux loopback with artificial network impairment. That is different from testing two laptops across the Internet.

### TCP and UDP

| Question | TCP | UDP |
|---|---|---|
| What does the application get? | An ordered stream of bytes | Separate datagrams |
| Does the transport retransmit lost data? | Yes, while the connection remains viable | No automatic retransmission |
| Does it preserve application message boundaries? | No; we add framing | Yes |
| Can data delivery involve waiting for missing earlier data? | Yes | UDP itself does not require ordered delivery |
| Does it encrypt by itself? | No | No |

TCP is not “always slow.” UDP is not “always fast.” The useful difference is the service each provides.

For reliable setup, waiting for missing data can be useful. For live voice, data arriving after its playback time can be less useful.

Reference: [IETF transport services overview](https://www.rfc-editor.org/info/rfc8095/).

### Why not send voice using TCP?

If a piece of data is missing, TCP's ordered delivery can hold back later bytes until the gap is repaired. This is called head-of-line blocking.

For example, if a missing audio frame belongs to a moment that has already passed, waiting for it can increase conversational delay.

Our current results do not fully measure that playback penalty. Explain it as a design reason, not as a result conclusively proved by our MOS graph.

---

## 3. MTU, fragmentation and why size matters

### MTU

MTU means Maximum Transmission Unit. It describes the largest IP packet a link can carry without fragmentation at that link.

Our experiments set the MTU to 1500 bytes. This is common for Ethernet, but not universal.

### IP fragmentation

With IPv4, an oversized datagram may be divided into smaller IP fragments when fragmentation is allowed.

The receiver must collect the pieces to rebuild the datagram. If a required piece does not arrive before reassembly times out, the datagram cannot be delivered.

The IETF recommends avoiding IP fragmentation because it reduces reliability and efficiency. [UDP usage guidelines](https://www.rfc-editor.org/info/rfc8085/)

### Simple example

Suppose one datagram becomes eight fragments.

If seven arrive and one is lost, the receiver does not get seven-eighths of the application message. It fails to obtain the complete datagram.

This is why multiple fragments create more opportunities for one message to fail.

### The formula you may be asked

If each fragment is lost independently with probability p:

```text
Probability one fragment arrives = 1 - p
Probability all n fragments arrive = (1 - p)^n

For 20% loss and eight fragments:
p = 0.20
(1 - 0.20)^8 = 0.8^8 ≈ 0.168
Expected delivery probability ≈ 16.8%
```

This is an idealized calculation. It assumes independent losses. Burst loss, where several adjacent packets disappear together, behaves differently.

### Fragmentation versus TCP segmentation

They are different.

- IP fragmentation divides an IP datagram.
- TCP segmentation carries parts of a byte stream in separate TCP segments.

The application reads an ordered byte stream from TCP. It does not need to reassemble IP fragments itself.

Do not say TCP guarantees no fragmentation on every network. The path and transport settings still matter.

### Why our 1200-byte cap?

It is a conservative application limit that leaves header space on the tested 1500-byte path.

Our 1200 bytes include the application header, encrypted audio and authentication tag. They exclude UDP and IP headers.

With basic IPv4 and UDP headers, 1200 bytes become 1228 bytes. Extra headers or tunnels can change the total.

### Don't Fragment

The DF flag requests that an IPv4 packet not be fragmented. If a packet cannot fit, failure or path feedback can occur instead.

Our helper requests this behavior only where the necessary socket option is exposed. Do not claim it is verified on every operating system.

---

## 4. Cryptography from the beginning

### Encryption

Encryption changes readable data into ciphertext using a key. A party with the appropriate key can recover the original data.

Encryption protects confidentiality: keeping the content private.

### Authentication and integrity

Authentication checks who or what produced a message.

Integrity checks whether protected data has changed.

Encryption and identity verification are different jobs. A connection can carry encrypted data without adequately proving who is at the other end.

### Symmetric key

A shared secret key used for symmetric encryption and decryption.

Our voice packets use ChaCha20-Poly1305 with a 32-byte key.

### Public key and private key

A public key can be shared. Its corresponding private key must stay secret.

Different algorithms use these keys for different purposes. Signing keys and key-establishment keys are not interchangeable.

### Digital signature

A private signing key produces a signature. The corresponding public key verifies it.

A valid signature links the signed bytes to the signing key. You still need a reason to trust that public key as belonging to the intended peer.

### Key pinning

Our peers already know which public identity keys they trust. They reject a different identity key.

That prior trust is called pinning. The demo sets up trust outside the network handshake.

### Post-quantum cryptography

PQC means cryptographic methods designed to resist attacks from powerful quantum computers as well as ordinary computers.

They run on ordinary computers. We do not use quantum hardware or a quantum communication link.

NIST standardized ML-KEM for key establishment and ML-DSA for signatures. They are derived from the Kyber and Dilithium algorithm families. [NIST overview](https://www.nist.gov/news-events/news/2024/08/nist-releases-first-3-finalized-post-quantum-encryption-standards)

Do not say “all encryption is broken by quantum computers” or “our software is guaranteed quantum-proof.”

### Our algorithms in plain English

| Name | Its job in this project | Simple explanation |
|---|---|---|
| ML-KEM-768 | Establish shared secret | Lets the peers obtain matching secret material |
| ML-DSA-65 | Sign handshake material | Lets peers verify signatures against trusted identity keys |
| HKDF-SHA256 | Derive keys and nonce prefixes | Converts secret material into the values our packet encryption needs |
| ChaCha20-Poly1305 | Protect voice packets | Encrypts audio and checks authenticity of protected content/header |
| Opus | Compress/decompress audio | It is an audio codec, not an encryption algorithm |

“768” and “65” name parameter sets. They are not the number of bytes in our voice encryption key.

At a deeper level, ML-KEM and ML-DSA use mathematical constructions based on module lattices. For this review, explain their roles accurately before trying to explain the mathematics.

Primary standards: [ML-KEM](https://csrc.nist.gov/pubs/fips/203/final), [ML-DSA](https://csrc.nist.gov/pubs/fips/204/final).

### KEM: three operations to remember

KEM means Key Encapsulation Mechanism.

1. Generate a public/private key pair.
2. Encapsulate using the public key: produce a ciphertext and a shared secret.
3. Decapsulate using the private key and ciphertext: recover the matching shared secret.

The ciphertext crosses the network. The shared secret is not directly sent as plaintext.

### Is our protocol TLS?

No. It is a custom academic handshake using library implementations of the cryptographic algorithms.

Standardized building blocks do not automatically make the entire custom protocol secure. We have tests, but no formal protocol proof or production security audit.

---

## 5. Understand the full call

### Architecture

```text
                         TCP control connection
Peer A        <-------------------------------------->        Peer B
                    Handshake and control records

Microphone -> Opus -> Encrypt ---- UDP ----> Decrypt -> Buffer -> Decode -> Speaker

Speaker <- Decode <- Buffer <- Decrypt <---- UDP ---- Encrypt <- Opus <- Microphone
```

### Step-by-step handshake

1. Both peers load identity material and know the trusted peer public keys.
2. The initiator generates an ML-KEM key pair.
3. It signs its KEM public key together with its identity public key.
4. It sends a HELLO record over TCP.
5. The responder checks the pinned identity and verifies the signature.
6. The responder encapsulates to the initiator's KEM public key.
7. It obtains a shared secret and a KEM ciphertext.
8. It signs the initiator's KEM public key together with that ciphertext.
9. It sends the ciphertext and signature in an ACCEPT record.
10. The initiator verifies the responder signature using its pinned key.
11. The initiator decapsulates the ciphertext.
12. Both sides derive the voice key and direction-specific nonce prefixes with HKDF.

This describes the implemented exchange. It is not a claim of a full formal authentication proof.

### Why length-prefixed records?

TCP is a stream. One send does not necessarily equal one receive.

Our code places a four-byte length before a record. The receiver reads the length, then reads that many payload bytes.

Think: “First tell me how many bytes belong to this message.”

### After setup

- Capture audio.
- Compress it with Opus.
- Add a sequence number and other header fields.
- Encrypt and authenticate it.
- Check the complete application datagram is no larger than 1200 bytes.
- Send it through UDP.
- Receive, authenticate, decrypt, buffer and decode it.

### What does “hybrid” mean here?

TCP plus UDP.

It does not mean classical plus post-quantum key exchange. This is one of the most important distinctions to remember.

---

## 6. Audio concepts

### Sample rate

48 kHz means 48,000 audio samples per second.

Our audio is mono: one channel.

### Frame

We process audio in 20 ms chunks.

```text
20 ms = 0.020 seconds
48,000 samples/second × 0.020 seconds = 960 samples
960 samples × 2 bytes/sample = 1920 bytes of raw 16-bit mono audio
```

This is before Opus compression. Encoded packet sizes can vary.

### Codec

A codec encodes and decodes audio.

Opus compresses the raw audio so less data needs to be transmitted.

### Packet loss

Some packets do not arrive or are unusable.

Configured loss is the target loss rate in the experiment. Observed loss is what happened in the sample. They do not have to match exactly.

### Latency

How long something takes from a defined start to a defined finish.

Always specify those points. Sending a frame to receiving it, starting a connection to completing setup, and speaking to hearing playback are different measurements.

### Jitter

Variation in packet arrival timing.

If packets are sent regularly but arrive unevenly, the receiver may need a buffer to smooth playback.

### Jitter buffer

A receiver holds some packets so it can reorder them before decoding.

Our implementation uses a fixed packet-depth buffer. The default depth is three packets. Do not describe it as a guaranteed 60 ms scheduled playback buffer.

More buffering can help with reordering but can also add waiting time.

### FEC

FEC means Forward Error Correction. Extra information can help reconstruct missing content without requesting a retransmission.

Opus can place information useful for recovering a previous frame into a later packet. Recovery depends on the available redundancy and the next packet arriving.

Important project detail: our separate experiment calls the FEC decoding operation. Our current live receiver uses normal decoding. Enabling FEC in the encoder alone does not prove the live call recovers lost frames.

---

## 7. Packet protection

### Our packet layout

| Part | Size |
|---|---:|
| Version | 1 byte |
| Message type | 1 byte |
| Sequence number | 8 bytes |
| Flags | 1 byte |
| Encrypted audio | Variable |
| Authentication tag | 16 bytes |
| Total allowed | At most 1200 bytes |

Header size: 11 bytes.  
Largest encoded audio payload under this format: 1200 - 11 - 16 = 1173 bytes.

### Sequence number

A counter identifies packet order. It supports reordering and duplicate/late-packet handling.

### Nonce

A nonce is an input to the encryption operation. It must not repeat with the same key.

Our nonce combines a four-byte direction prefix and an eight-byte sequence number.

The two directions derive separate prefixes. This is intended to separate their nonce use. Avoid saying we have formally proved uniqueness in all possible sessions.

### Authentication tag

The receiver checks the tag to detect invalid protected packets.

The application header is authenticated as associated data. It can remain visible while still being protected against undetected modification.

### Replay

Replay means sending a previously valid packet again.

Authenticated encryption alone does not solve replay. Sequence tracking and receiver state are also needed. Our buffer drops late/duplicate sequence numbers within its state; this is not a complete formal replay-security claim.

### Key rotation

The helper derives new key material from the existing key.

It is not a new independent post-quantum handshake. Do not claim automatic live rekeying unless you can demonstrate that integration.

---

## 8. What we implemented and how to prove it

| Area | Files to recognize | What you should explain |
|---|---|---|
| Handshake | handshake.py | Signatures, encapsulation, decapsulation, derivation |
| TCP control | control.py | Length-prefixed records and handshake driver |
| Packet protection | packet.py | Header, encryption, size guard |
| UDP socket | udp.py | Sending/receiving and conditional DF request |
| Audio | audio.py | Opus and frame settings |
| Reordering | jitter.py | Packet-depth buffer and duplicate/late drops |
| Call integration | call.py, demo.py | Joining audio, keys and sockets |
| Interface | gui.py | Starting calls and changing outgoing voice loss |
| Evaluation | src/hpqv/eval/ | Measurement and comparison code |
| Results | results/ | Saved CSVs and figures |

### Unit test and integration test

A unit test checks a small part. Example: reject a packet that exceeds the size cap.

An integration test checks connected parts. Example: establish keys and send protected audio between endpoints.

### Test result you can quote

On **15 September 2026**, the local run returned:

- 40 passed.
- 15 skipped.
- The run took 25.15 seconds.

Skipped tests needed environment-specific evaluation tools. Skipped does not mean passed.

The run also reported a liboqs 0.15.0 / liboqs-python 0.16.0 version mismatch. Align and verify the environment before using it as a frozen demo setup.

These tests were not rerun when writing this study guide.

### Team contribution

The plan suggests:

- Ashwanth: crypto/control.
- Cain: transport/data.
- Sreenandu: audio/evaluation.

Confirm actual contributions. Each person should name a real file, test, decision and contribution. Do not read the planned division as if it proves authorship.

For your own answer, complete:

> “I worked on ____. My main implementation was ____. I verified it with ____. One issue I understood or resolved was ____.”

---

## 9. Understanding our experiments

### Measurement versus simulation

A real-prototype measurement runs our code and sockets.

A simulation models system behavior using assumptions.

Both can help, but they are different types of evidence.

### Tools

| Tool | Plain meaning |
|---|---|
| tc netem | Linux tool for adding controlled delay/loss/jitter |
| Docker container | A repeatable environment containing evaluation software |
| tshark | Reads/captures network packets |
| pcap | A saved packet-capture file |
| CSV | A text table of values |
| ns-3 | Network simulation software |
| pytest | Runs automated Python tests |

### Main transport sweep

- Linux loopback.
- MTU fixed at 1500 bytes.
- Loss settings: 0%, 5%, 10%, 20%, 30%.
- Configured delay: 50 ms.
- Configured jitter: 10 ms.
- Five runs per condition, reported as medians.
- Driver uses 100 frames per normal run.

The runner completes the real handshake before capture and impairment. It sends encoded silence without real-time 20 ms pacing.

Therefore it is a limited transport benchmark. Do not describe all its outputs as realistic conversational audio measurements.

### Payload-delivery experiment

- Random payload of 10,842 bytes.
- 100 attempts for each transport/loss setting.
- Naive UDP sends the payload once.
- TCP transfers it reliably using a stream.
- UDP timeout is one second.
- TCP uses 20-second socket/thread wait settings.

The payload has the size of the handshake's cryptographic material. It is not an actual authenticated handshake.

### FEC experiment

- Three-second generated signal.
- Repeatable seeded packet-loss choices.
- Compare reconstruction with FEC-enabled encoding/next-packet recovery against an FEC-disabled silence-fill baseline.
- Measure error relative to the corresponding undamaged decode.

It does not measure human opinions or prove live-call FEC integration.

### Median

Sort the measured values. The middle value is the median.

Five values of 10, 11, 12, 13 and 100 have a median of 12. The median is less affected by a single very large value than the average.

### Confidence interval

A range describing statistical uncertainty under the method's assumptions.

A result from 100 attempts is a sample. It does not establish the exact success probability for every future call.

---

## 10. Explain our main results

### Result A: captured fragments

[Open fragmentation graph](../presentation/canva-review-iii/charts/loss_vs_fragments.png)

| Loss setting | Hybrid captured fragments | Naive captured fragments |
|---|---:|---:|
| 0% | 0 | 5 |
| 5% | 0 | 5 |
| 10% | 0 | 5 |
| 20% | 0 | 4 |
| 30% | 0 | 4 |

**Say:**

> “Our captured hybrid benchmark traffic produced zero observed IP fragments. The naive scenario inserted an oversized UDP payload and produced fragments.”

**Why do naive counts fall to four?**

The values count observed fragments in the capture. They are not a promise that every original datagram was divided into exactly four fragments at high loss.

**Limit:**

The real handshake happened before capture. This graph alone does not verify the complete handshake traffic.

### Result B: delivery of handshake-sized payloads

[Open delivery graph](../presentation/canva-review-iii/charts/handshake_survival.png)

| Loss setting | Naive successes out of 100 | TCP successes out of 100 |
|---|---:|---:|
| 0% | 100 | 100 |
| 5% | 75 | 100 |
| 10% | 39 | 100 |
| 20% | 19 | 99 |
| 30% | 2 | 83 |

**Remember: 20% loss, 19 versus 99.**

**Say:**

> “At 20% configured loss, 19 of 100 one-shot UDP payloads arrived, compared with 99 of 100 TCP payload transfers under the experiment's timeout settings. This demonstrates delivery behavior for a synthetic large payload.”

**Why is TCP better here?**

It retransmits lost data and has more time to finish.

**What is the cost?**

Waiting. At 20% loss, the saved median successful TCP transfer time was about 421 ms. At 30%, about 1020 ms. Failed attempts are excluded from these medians.

**Do not say:**

“99% of fully authenticated voice calls connected at 20% loss.”

That experiment has not established that claim.

### Why eight fragments here but five in the other graph?

They use different payloads:

- Main transport sweep: 6445-byte inserted UDP payload.
- Survival experiment: 10,842-byte UDP payload.

Do not combine their counts into one result.

### Result C: modeled MOS

[Open MOS graph](../presentation/canva-review-iii/charts/loss_vs_mos.png)

MOS stands for Mean Opinion Score. In a listening study it can represent people's quality ratings. Our program estimates a score mathematically instead.

| Loss setting | Hybrid estimate | Naive estimate | TCP estimate |
|---|---:|---:|---:|
| 0% | 4.35 | 4.35 | 4.35 |
| 10% | 3.17 | 3.06 | 4.35 |
| 20% | 2.18 | 2.08 | 4.35 |
| 30% | 1.90 | 1.90 | 4.35 |

**Say:**

> “The simplified model predicts lower quality as UDP frame loss increases. Hybrid and naive UDP are similar because both carry small voice packets. The setup payload's fragmentation does not imply worse steady-state voice quality.”

**Why is TCP flat?**

The model uses configured delay/jitter and observed loss. It does not fully represent per-frame playback waiting. When TCP delivers all frames, the model can give a high score while missing an important delay cost.

### Result D: FEC reconstruction

[Open FEC graph](../presentation/canva-review-iii/charts/fec_recovery.png)

At 20% configured loss:

- FEC-enabled reconstruction: about 47.4 dB segmental SNR.
- FEC-disabled silence-fill baseline: about 23.9 dB.
- 26 frames were lost.
- 20 next-packet recovery attempts were counted.

SNR means signal-to-noise ratio. Here a higher value means less measured reconstruction error relative to the selected reference.

The recovery counter records attempts, not independent verification of redundancy in each packet. The 99 dB zero-loss value is a numerical cap.

**Say:**

> “The separate generated-signal experiment shows lower reconstruction error with the FEC approach under its test conditions. It is not a listening study or evidence of FEC recovery in our current live receiver.”

### Latency chart

[Open first-frame chart](../presentation/canva-review-iii/charts/latency_vs_ttfb.png)

TTFB means Time To First Byte, but the exact timer matters more than the name.

Our main benchmark starts timing after the crypto handshake. Explain its values as sending-start to first received voice frame, not full call setup.

### ns-3 charts

Use them as backup.

They are model outputs. QUIC is a simplified proxy, not a real full QUIC implementation. TCP delay accounting also has limitations.

Do not claim that we beat actual QUIC.

---

## 11. What is complete and what is still limited?

### Evidence we have

- Crypto/control and packet modules.
- Audio encoding/decoding.
- Integrated call code and GUI.
- Automated tests.
- Saved controlled-experiment results.
- Reproduction scripts.
- A separate FEC recovery experiment.

### Remaining validation

- The actual two-device demo configuration.
- Full authenticated setup under impairment.
- Comparison under a clearly defined common completion deadline.
- Real-time paced audio measurements.
- Per-frame delivery and playback deadlines.
- Live FEC recovery integration and testing.
- More representative networks and audio inputs.
- Completed report sections and corrected claims.

A limitation does not mean the project has no value. It tells the panel exactly what your evidence supports and what you will do next.

Never call this production-ready, fully security-audited or formally proven.

---

## 12. Likely panel questions: short answers to practice

### What problem are you solving?

“Large post-quantum setup messages can cause fragmentation when placed in oversized UDP datagrams. We separate reliable setup from small encrypted voice traffic.”

### Why both TCP and UDP?

“TCP provides ordered reliable setup delivery. UDP lets the voice path avoid transport-level retransmission and ordered waiting.”

### Why not use ordinary UDP with application-level reliability?

“That is another possible design. Our baseline is deliberately naive and does not represent every reliable UDP protocol. A stronger baseline is useful future work.”

### Are TCP and UDP secure?

“They do not provide encryption by themselves. We add our handshake and authenticated voice encryption.”

### Why use post-quantum algorithms?

“To study a transport design that accommodates post-quantum key establishment and authentication, including their larger messages.”

### Do you need a quantum computer?

“No. The algorithms run on ordinary computers.”

### What is the difference between ML-KEM and ML-DSA?

“ML-KEM establishes secret material. ML-DSA signs and verifies messages.”

### Why HKDF?

“To derive the key and direction-specific nonce prefixes from the shared secret.”

### Why not encrypt every voice frame with ML-KEM?

“ML-KEM establishes a secret. We then use symmetric authenticated encryption for the ongoing voice packets.”

### Why 1200 bytes?

“It leaves header margin on our tested 1500-byte path. It is a conservative limit, not a universal guarantee.”

### What happens when a packet exceeds 1200 bytes?

“The packet-building function rejects the datagram by raising an error.”

### Is a digital signature the same as encryption?

“No. A signature supports authenticity of the signed data. Encryption protects confidentiality.”

### What prevents impersonation?

“The handshake checks signatures using pinned identity public keys. The full custom protocol still needs broader security analysis.”

### Does the shared identity file contain secrets?

“Yes. The demo file contains private material for both roles. It must be copied privately and must not be shown or published.”

### Why not certificates and a certificate authority?

“The demo uses out-of-band pinning to keep trust setup simple. A production identity system would need a different provisioning design.”

### Did the GUI slider test handshake loss?

“No. It drops outgoing voice packets after setup.”

### Did you prove better voice quality?

“No general improvement over naive UDP is established. Their modeled MOS values are similar. Our strongest evidence is about payload sizing and delivery.”

### How can TCP have high MOS under loss?

“Our simplified score does not fully include late-playback costs. Reliable arrival and timely arrival are different.”

### Does your live call recover loss through FEC?

“Not through the explicit recovery path used in the separate experiment. Integrating that path is remaining work.”

### Are the tests all passing?

“The last inspected local run had 40 passed and 15 skipped on 15 September. I would quote the latest verified output if we rerun it.”

### What does a skipped test mean?

“The test did not execute, often because required tooling is unavailable. We do not count it as passed.”

### Is everything finished?

“The core prototype and experiment infrastructure exist. Several integration and measurement improvements remain. We will show completion against our approved scope.”

### What is the biggest lesson so far?

“Measurement boundaries matter. A graph can be correct while its title or interpretation claims more than it measured.”

### What will you do next?

“Validate the real two-device call, measure complete setup under loss, add realistic audio timing, and integrate the FEC recovery path.”

### What if you do not know an answer?

Say:

> “I have not measured that yet. Our current evidence shows ____. To answer your question, we would test ____.”

Or:

> “I know the role of that component, but I would need to check the exact implementation detail.”

Do not invent a confident answer.

---

## 13. Demo preparation

### Before leaving

- Charge both laptops and bring chargers.
- Bring headphones for both users.
- Test the exact app build and network.
- Keep the same identity file on both machines, copied privately.
- Check microphone and network permissions.
- Save the slide deck and backup demo video offline.
- Keep the chart PNGs available.
- Do not expose private keys in a screen share.

### Demo sequence

1. Start the listener.
2. Call the listener's actual LAN IP and port.
3. Speak in both directions.
4. Show receive/send counters.
5. Change outgoing voice loss to 20%.
6. Describe only what is actually audible/visible.
7. Return loss to zero.
8. Hang up.

### If it fails

Check:

- Did the listener start?
- Is the IP address correct?
- Do both peers use the same demo identity?
- Are microphone permissions enabled?
- Is UDP blocked?
- Does the Wi-Fi isolate clients?

Do a brief check, then use the backup recording if necessary. Say clearly that it is a recording of an earlier successful run.

Detailed instructions: [desktop demo](DESKTOP-DEMO.md).

---

## 14. Opening, results explanation and closing

### Opening: about 40 seconds

“Good morning. Our project is a hybrid TCP-UDP transport for post-quantum voice communication. Post-quantum setup messages contain relatively large public keys and signatures. Sending those messages as oversized UDP datagrams can lead to IP fragmentation. Our design puts setup on TCP and small encrypted voice packets on UDP. Today we will show the implemented modules, demonstrate the application and explain our interim results.”

### Main result: about 30 seconds

“In the separate payload-delivery experiment, at 20% configured packet loss, TCP delivered 99 of 100 synthetic handshake-sized payloads, while one-shot UDP delivered 19. TCP's advantage comes with retransmission waiting and a longer timeout policy. This supports our transport motivation, but it is not yet a measurement of complete authenticated call success.”

### Closing: about 25 seconds

“We have implemented the main transport, cryptographic and audio components and collected controlled results. Our next steps are complete-call validation, realistic audio timing and integration of the FEC recovery path. We are keeping our conclusions limited to what the current experiments actually measure.”

---

## 15. Tomorrow morning: one-page revision sheet

### Remember these ten points

1. Problem: large setup messages can fragment if sent as oversized UDP datagrams.
2. Design: TCP control, UDP voice.
3. Hybrid means two transports, not two kinds of key exchange.
4. ML-KEM establishes secret material; ML-DSA signs.
5. HKDF derives values; ChaCha20-Poly1305 protects voice.
6. Opus compresses audio: 48 kHz mono, 20 ms, 960 samples.
7. Voice application datagram cap: 1200 bytes.
8. At 20% loss: 19/100 naive versus 99/100 TCP synthetic payload deliveries.
9. MOS is modeled; the latency timer excludes the crypto handshake.
10. Live FEC recovery, complete-call measurement and device validation remain.

### Numbers worth knowing

| Number | Meaning |
|---|---|
| 1500 B | Experiment MTU |
| 1200 B | Maximum application voice datagram |
| 11 B | Voice header |
| 16 B | Authentication tag |
| 32 B | Voice encryption key |
| 10,842 B | Total handshake cryptographic material; synthetic survival payload size |
| 10,852 B | Two framed handshake records including versions and record lengths, before network headers |
| 100 | Attempts per survival condition |
| 5 | Runs per main transport-sweep condition |
| 40 passed / 15 skipped | Verified local test outcome on 15 September |

### Do not mix these up

| Say this | Avoid this |
|---|---|
| Synthetic payload delivery | Authenticated call success |
| Zero observed fragments in captured traffic | No fragmentation anywhere |
| Modeled MOS | Human listening score |
| Post-handshake first-frame time | Complete call setup time |
| Separate FEC experiment | Live-call FEC recovery |
| Tested academic prototype | Production-ready secure system |
| Real socket experiments on impaired loopback | Open-Internet validation |
| Standard library algorithms integrated by the team | Algorithms invented by the team |

### How to explain any graph

1. Name the experiment.
2. Explain the horizontal axis.
3. Explain the vertical axis and unit.
4. Compare one concrete point.
5. Explain the mechanism.
6. State the main limitation.

### The sentence to remember

> “Our design separates large reliable setup traffic from small time-sensitive voice traffic, and our current results support that design under the specific conditions we tested.”

---

## 16. Final self-test

Close this file and answer these without reading:

- Can I draw the architecture?
- Can I explain TCP versus UDP without saying only “slow versus fast”?
- Can I explain why one missing fragment loses the whole datagram?
- Can I distinguish a signature, a KEM and voice encryption?
- Can I describe the handshake in order?
- Can I explain the 19 versus 99 result without calling it real-call success?
- Can I explain why TCP's MOS stays high?
- Can I explain what the GUI loss slider actually does?
- Can I name my own verified contribution?
- Can I name two concrete next steps?

If you can answer these clearly, focus the remaining time on rehearsing delivery and checking the demo rather than memorizing more terminology.
