# Live demo runbook — two laptops, GUI, 15–20 minutes

A minute-by-minute script for the final review. It assumes two laptops running the GUI
(`python -m hpqv.gui` or the packaged HPQV app) on the same LAN. For install and
two-device setup details see [DESKTOP-DEMO.md](DESKTOP-DEMO.md); for CLI use see
[DEMO.md](DEMO.md).

**Status of this script.** The behaviours below follow from the code, the unit tests and the
committed experiments. No two-laptop run is recorded in the repo, so **rehearse it once end to
end** and correct anything your machines do differently. For citable numbers behind what the
audience sees (FEC vs silence vs PLC, burst loss, jitter depth), point to
[EXPERIMENT-live-scenarios.md](EXPERIMENT-live-scenarios.md) and `docs/EVALUATION-REPORT.md` §11.

---

## Review III script (v2): encrypted, authenticated, and what changed since Review II

Six minutes, before the long script below. Laptop A listens, laptop B calls, and the
narrator drives Wireshark on A (or on a third laptop on the same LAN). Both laptops run
the `feat/secure-session-v2` build or later; v2 does not talk to the v1 release.

**Setup (replaces section 1's "one identity file" step, which is v1).**
- [ ] On both laptops, Call tab: leave *Your key*, *Your certificate* and *Peer's
  certificate* **empty**. The app then uses this laptop's own identity, created on
  first use in the app folder; its private key never leaves the laptop.
- [ ] Pair the two laptops once, in the *Pair a device (no files)* box (or live as
  step 1). Neither laptop may be in a call while pairing.
  A: *Allow new device (2 min)*. B: type `<A's IP>:9000` and press *Pair with…*.
  Both screens ask *Do the codes match?* with the same six-digit code. Read it aloud
  and press **Yes** on both. Each status line then says *Paired with NAME.* The
  pairing is remembered, so later calls between these two laptops connect directly.
  No-files pairing is experimental ([design](reviews/2026-10-16-b2-pairing-design.md)).
- [ ] *Advanced, file-based alternative* (still supported, e.g. if the Wi-Fi blocks
  the pairing): Call tab → *Create identity* on each laptop. Each writes `NAME.key`
  (stays on that laptop) and `NAME.crt`. Swap **only the `.crt` files** and choose the
  other laptop's file as *Peer's certificate*.
- [ ] Wireshark with the hpqv plugin installed, *Decode As* TCP 9000 → TLS set, capturing
  on the LAN interface with filter `host <B's IP>` ([guide](DEMO-WIRESHARK.md)).
- [ ] Rehearse once on two laptops. The automated tests cover pairing, an unpaired
  caller being refused without a prompt, and the prompt's Decline, Hang up and Accept
  paths. Audio after Accept and the matching codes on both screens have only been
  checked by a person at rehearsal.

| Step | Do | Say | If it fails |
|---|---|---|---|
| 1 | Pair the laptops (setup above), or show the status line *Paired with NAME.* if already done. | "At Review II both laptops shared one file holding both secret keys. Now each laptop makes its own key, which never leaves it. The first time two laptops meet, both people confirm a code before either trusts the other." | B says *Pairing refused*: A's 2-minute window closed or A is in a call; press *Allow new device* on A again. |
| 2 | Start the Wireshark capture. A: *Start* (listen). B: *Start* (call). | "The handshake is standard TLS 1.3 with a hybrid classical plus post-quantum key exchange." | B shows *handshake failed: …*: read the plain-words reason; usually the laptops are not paired (file-based: the wrong peer certificate was chosen). An unpaired laptop is refused before any prompt. |
| 3 | A's **Incoming call from NAME (paired)** prompt opens. Both read the call's code aloud: A from the prompt, B from its Security tab. A presses **Accept**. B's state changes from *ringing* to *connected — talk*. | "Pairing happened once. Every call still gets a fresh code: if anyone were in the middle, these two codes would differ. You compare them out loud before you answer." | No prompt: A is not listening on 9000, or B's firewall blocked it. No answer in 30 s declines the call; just call again. |
| 4 | Wireshark: `tls.handshake.type == 2` → group X25519MLKEM768; `tls.app_data` → certificates encrypted; `ip.flags.mf == 1` → nothing; `hpqv` → voice packets. | "About 21 kB of handshake, never fragmented. Every voice packet is under 1200 bytes, and its payload is ciphertext." | No `HPQV` rows: plugin not loaded (*Help → About → Plugins*). |
| 5 | A: Security tab, *What an eavesdropper sees*. In Wireshark filter `hpqv && ip.src == <A's IP>` and compare the session id, key generation and packet size with the panel (packets go out 50 a second, so the sequence number moves too fast to match live). | "This is everything on the wire. Without the session keys, the voice inside cannot be decoded." | — |
| 6 | Press **Rotate keys**. | "New keys mid-call: the generation in the header goes from 0 to 1, and the audio does not stop." | Button says *old key retiring…*: wait 4 s and press again. |
| 7 | Network tab: Bad Wi-Fi preset, then repair modes (section 2's 3:00–10:00). | "The Review II loss demo, now on the v2 protocol." | — |
| 8 | Slide from [RELATED-WORK.md](RELATED-WORK.md). | "Of the four projects we compared, only ours reports measurements of how a post-quantum handshake behaves under loss. Those numbers are from our v1 protocol; v2 has not been re-measured yet." | — |

Hang up on either laptop ends both. The handshake-failure wording is `demo.FAILURE_TEXT`.

---

## 0. Who does what, and the one idea to get right

Team of three works best: **driver A** (network laptop), **driver B** (listening laptop on the
projector), **narrator**.

> **Which laptop shows what.** The *outgoing* knobs on a laptop (loss, loss model, burst
> length, delay, jitter, Encoder FEC, bitrate) change what the **peer** hears. (The *Encoder FEC*
> checkbox is labelled *in audio the peer receives* and sits with the outgoing-loss controls on
> the Network tab.) The tiles, graph,
> MOS and export on a laptop describe the audio **you** receive, measured by your own receiver.
> Three knobs act on your own receiver: *Incoming loss*, *Repair mode* and *Jitter buffer*.

So the plan is:

| | Laptop A — "network" | Laptop B — "listening" (projected) |
|---|---|---|
| Role | Sends speech; its outgoing impairments are what B hears | Hears A; its tiles and graph move; exports the session |
| Knobs used | outgoing loss, burst preset, delay, jitter, Encoder FEC, bitrate, Rotate keys | Repair mode, Incoming loss, Jitter buffer, Rotate keys |
| Listen to | — | the speakers / headphones on B |

Why B's *Incoming loss* for the repair-mode A/B (segment 4): it is a B-local knob, so B's export
labels each change as an event and splits the summary into segments. Changes made on A are
**not visible in B's export** (the summary says so); write down the call time of each.

---

## 1. Setup checklist (do it the day before and again 15 min before)

- [ ] **Same build on both laptops** (same app version or same commit).
- [ ] **One identity file.** On A: Call tab → *Create identity*, save `identity.json`; copy it
  privately to B and select it with *…*. Two independently generated files do not authenticate.
- [ ] **Same LAN that lets clients talk to each other.** Not a guest, hotel or campus network
  that isolates devices. Note A's IPv4 address (Wi-Fi settings or `ipconfig` / `ifconfig`).
- [ ] **Firewall and permission prompts accepted in advance** on both laptops: listener on A
  needs **TCP and UDP 9000**; B needs inbound UDP for the app. Accept microphone and
  local-network prompts now, not on stage.
- [ ] **Audio source decision.**
  - *Recommended for a room:* **WAV file on both laptops.** Two open microphones next to two
    speakers can howl, and a noisy room ruins repeatable A/B listening. A plays a speech WAV;
    B plays any quiet WAV.
  - WAV must be **48 kHz, mono, 16-bit PCM**. The GUI **loops** a WAV input (it restarts at the
    end, still paced in real time), so any length works and the call runs until **Hang up**. A
    short speech clip is fine. Check the format with
    `python -c "import wave;w=wave.open('speech.wav');print(w.getframerate(),w.getnchannels(),w.getsampwidth())"`
    → expect `48000 1 2`. (To convert, e.g. `ffmpeg -i in.m4a -ar 48000 -ac 1 -sample_fmt s16 speech.wav`.)
    On the CLI, add `--loop` for the same behaviour; without it the file plays once and the call
    ends. Loop restarts are audible as a repeat, so pick a clip that cuts cleanly.
  - **Use real speech, not tones.** FEC recovery only works when the packet carries Opus LBRR
    data, which the encoder produces for speech-like (SILK) content and never for CELT-only
    packets ([wire-format.md](wire-format.md), flags bit 2).
  - *Microphone:* fine for a short clean-call opening if you wear headphones on B.
- [ ] **Record + export enabled.** Before pressing Start on **B**: Call tab → tick *Record
  received audio* and choose a path. Pick a writable export folder in advance. The recording is
  exactly what B played, so you can replay segment 4 afterwards when the room is noisy.
- [ ] **Screen:** B on the projector at 1366×768 or larger; Network, Call and Security tabs
  reachable; graph visible. A can be on a second display or the narrator's view.
- [ ] **Dry run** of the 30-second sequence: A *Listen* → B *Call* → both say *connected* →
  press *Hang up* on A → B says *call ended by peer*.
- [ ] **Backup:** the committed results are citable without the live demo — keep
  [EVALUATION-REPORT.md](EVALUATION-REPORT.md) §7 open on a tab.

If the call connects but B's *Received* counter stays at 0, it is almost always blocked UDP —
see DESKTOP-DEMO.md → *If the call does not work*.

---

## 2. Script

Times are targets from pressing *Start*. Between segments press **Clean** on **A** and
**Clean** on **B** (presets reset network knobs, including Incoming loss and jitter depth, but
**not** repair mode, Encoder FEC or bitrate — set those explicitly).

### 0:00–1:30 · Handshake panel

**Click.** A: *Listen on port* 9000 → *Start*. B: *Call* `A-ip:9000` → *Start*. Open the
**Call** tab on B.
**Say.** "Before any voice flows, the two laptops run a post-quantum, mutually authenticated
handshake. Here is what it cost."
**Panel should see.** Key exchange *ML-KEM-768*; signature *ML-DSA-65*; HELLO / ACCEPT / total
**6446 / 4398 / 10844 B**; transport **TCP**; *UDP fragments* 0; *Handshake time* in
milliseconds; *Largest datagram* with the 1200 B cap beside it.
**What it proves.** The handshake is about seven times a 1500 B MTU, yet it never touches UDP, so it
cannot fragment. Voice datagrams are far under the cap.
**Honest caveats.**
- *UDP fragments: 0 (by design: handshake is on TCP)* in the panel is a constant, true by construction (the handshake is TCP; the
  voice sealer refuses datagrams over 1200 B). The **measured** evidence is the packet-capture
  result in `results/eval.csv`: zero fragments from the hybrid's voice datagrams at every loss
  level. The handshake ran over TCP before that capture started, so it is not in it.
- 10,844 B on the wire = the 10,842 B of key material quoted in the docs + a 1-byte version in
  each message.
- *Handshake time* here is one LAN handshake with no loss. For handshakes **under loss**, point to
  [EXPERIMENT-full-handshake.md](EXPERIMENT-full-handshake.md): 84 % (TCP) vs 18 % (naive UDP)
  complete in 3 s at 20 % loss — and UDP with HELLO retry does about as well as TCP.

### 1:30–3:00 · Clean call

**Click.** Nothing. Talk or let the WAV play; watch *Sent* and *Received* climb on both laptops.
**Say.** "A clean call: every frame arrives, loss is zero, the MOS estimate sits near the
model's ceiling."
**Panel should see.** *Loss you hear* 0 %, *Residual loss* 0 %, *Dropped*, *Late*, *Recovered*,
*Concealed* all flat, MOS steady. Graph fills left to right.
**Caveat.** MOS is an **ITU-T G.107 E-model estimate** from loss and delay — not a listening
test, and it cannot see codec artefacts.

### 3:00–4:30 · Bad Wi-Fi preset

**Click.** **A** → Network tab → *Bad Wi-Fi*. (A, not B: the preset impairs the *sender*.)
**Say.** "This is a mildly bad Wi-Fi link: a few percent loss with some delay and jitter, applied
to what A sends."
**Panel should see (on B).** *Loss you hear* rises to a few percent; *Recovered (FEC)* ticks up;
*Residual loss* stays below *Loss you hear*; MOS dips slightly; sound mostly intact.
**Caveat.** Loss is independent random (i.i.d.), the easy case. On A only *Dropped (out)* climbs;
A's *Loss you hear* and MOS do **not** change, because the impairment is on what A *sends*.

### 4:30–10:00 · A/B of repair modes: Silence → Concealment → FEC (the centrepiece)

**Setup.** A: *Clean*, **Encoder FEC ticked**. B: *Clean*. On B set **Incoming loss to 10 %**
(slider, Network tab). Everything below is on B.

Run each mode for about 60 s at 10 %, then repeat at 20 %. Switch in this order and keep talking
the same speech:

1. **Repair = Silence.** *Say:* "Lost frames are replaced by silence — what a naive receiver does."
   Listen for choppy dropouts. *Tiles:* *Concealed* climbs, *Recovered* stays 0, *Residual
   loss* ≈ *Loss you hear*, MOS low for this loss.
2. **Repair = Concealment only.** *Say:* "Opus guesses the missing frame from the previous one."
   Listen for smoother but still garbled gaps. *Tiles:* *Concealed* climbs, *Recovered* stays 0.
3. **Repair = FEC + concealment.** *Say:* "Each packet also carries a low-bitrate copy of the
   previous frame; when one is lost the next packet rebuilds it." Listen for noticeably fewer
   glitches. *Tiles:* **Recovered (FEC) climbs**, *Residual loss* drops well below *Loss you
   hear*, MOS recovers toward the clean value.

Then raise Incoming loss to **20 %** and show FEC still helps but recovers a smaller share.
Optional extra: on **A** untick *Encoder FEC* while B stays on *FEC + concealment* — *Recovered*
stops climbing and *Concealed* resumes, because without LBRR in the packet there is nothing to
rebuild from.

**Panel should see.** Only the FEC mode moves *Recovered* and lowers *Residual loss*.
**Honest caveats.**
- **MOS and *Residual loss* cannot tell Silence from Concealment**: neither recovers real audio,
  so both read the same. The difference between those two is audible, not on the tiles. Say so
  before the panel notices.
- *Recovered* counts only frames rebuilt from LBRR the next packet actually carried; frames
  filled by concealment (or silence) count as *Concealed*, not recovered.
- FEC looks one frame ahead: it needs the *next* packet to arrive. At 20 % random loss more
  losses have a lost successor, so the recovered share falls.
- Offline evidence for FEC is a 3 s synthetic chirp: 24–30 dB segmental SNR better than
  silence-filling lost frames ([EXPERIMENT-fec-recovery.md](EXPERIMENT-fec-recovery.md)); not a
  listening test. The chirp encodes in hybrid mode with LBRR, so that gain is real LBRR recovery,
  but the baseline is silence, not Opus concealment. Tell them live recovery is shown by the
  counter and by ear.
- Incoming loss is applied after decryption on B and is random only; it models loss on B's side of
  the link. It is not a substitute for loss on the network path.
- If the room is too noisy to hear the difference, replay B's recording (Call tab recording path)
  afterwards.

### 10:00–12:00 · Burst loss defeats FEC

**Click.** **B**: Incoming loss back to 0 %, repair = *FEC + concealment*. **A**: *Burst loss*
preset (10 % average loss, mean burst 4 packets, simple Gilbert two-state model).
**Say.** "Same average loss as before, but it arrives in runs. FEC only looks one frame ahead, so
it cannot rebuild a run."
**Panel should see (B).** *Loss you hear* near 10 %, but *Recovered* is a small fraction of the
lost frames compared with the 10 % random case; *Concealed* climbs; *Residual loss* stays close
to *Loss you hear*; MOS lower than random 10 % with FEC. In a burst of *B* lost packets only the last
frame is recoverable.
**Caveat.** This is a limitation, not a bug, and it is why block FEC (e.g. Reed–Solomon) is in
future work. Real loss is usually burstier than the i.i.d. loss used in the handshake experiments.

### 12:00–14:30 · Delay and jitter versus jitter buffer depth

**Click.** **A**: *Clean*, then set **Delay 100 ms** and **Jitter 60 ms** on the sliders (loss
0 %). **B**: Jitter buffer slider to **1 frame**, then raise it step by step.
**Say.** "Jitter reorders packets. A shallow buffer plays a frame before a late one arrives, so
the late one is useless. A deeper buffer waits longer, but adds delay."
**Panel should see (B).** At depth 1–2, **Late** climbs and *Residual loss* rises (a late packet
counts as lost to the listener). Raise the depth until **Late stops climbing**. MOS responds to two opposing effects — fewer late
packets help, the added buffer delay hurts (the estimate counts 20 ms per frame of depth plus the
measured added delay) — so read which one wins rather than promising a direction. The slider shows depth in frames and ms.
**Caveats.**
- A *constant* delay does not cause Late; variation in delay does.
- *Added delay* is relative to the lowest delay seen this call (the laptops' clocks are not synced),
  so a base delay present from the first packet is not included. The MOS trade-off shows the buffer's own cost only.
- The buffer is fixed-depth, not adaptive (future work).
- A quiet LAN has almost no jitter of its own; the demo adds it on purpose.

### 14:30–15:30 · Bitrate versus largest datagram

**Click.** **A**: *Clean*; Opus bitrate slider from low up to 64 kbit/s. Watch *Largest datagram*
on **A's** Call tab (the handshake panel).
**Say.** "Raising the audio bitrate makes packets bigger, but they stay nowhere near the 1200-byte
cap that keeps us fragmentation-free."
**Panel should see.** *Largest datagram* may creep up and stays a small fraction of 1200 B. As
arithmetic, 64 kbit/s is about 160 B of Opus per 20 ms frame, plus an 11 B header and a 16 B tag,
roughly 190 B on average; the displayed value is the measured maximum.
**Caveat.** *Largest datagram* is a **high-water mark**: it rises but never falls when you lower
the bitrate again. Go low → high, not the reverse. The datagram guard rejects (rather than sends)
anything over 1200 B.

### 15:30–17:00 · Live key rotation

**Click.** Either laptop → Security tab → **Rotate keys**.
**Say.** "Both sides re-key in the middle of the call. Read the fingerprint aloud on both
screens — it changed, and the audio never stopped."
**Panel should see.** *Key generation* goes 0 → 1 on **both** laptops and the 8-digit
**fingerprint** changes to the same new value; audio continues. The button reads *Old key
retiring…* for about 4 s and then re-enables (one rotation at a time). Pressing again gives
generation 2.
**Caveats.**
- Rotation is a hash-chain step (HKDF of the current key), not a fresh key exchange: it limits how
  much traffic one key protects and later keys do not reveal earlier ones; it does not recover
  from compromise of the current key.
- The fingerprint is a human cross-check, not authentication. The in-call control channel is
  plain TCP after the handshake; an on-path attacker could desynchronise the keys (denial of
  service, no disclosure) — [ADR-0004](adr/0004-live-key-rotation.md).
- Datagrams sent by the new key before the peer processes the rotation message are lost to the
  receiver; expected to be small on a LAN (not measured), and repair conceals it.

### 17:00–18:00 · Hang up from one side ends both

**Click.** On **A**, press **Hang up**.
**Say.** "One side hangs up; the other learns over the control channel and ends too."
**Panel should see.** A: *hung up*. B: *call ended by peer*; the graph stops and keeps its data.
**Caveat.** Closing a window or a dropped TCP connection also ends the call for the other side.

### 18:00–20:00 · Export → open charts and summary

**Click.** On **B**: **Export session…** → pick the folder (works after hang-up). Open the folder.
**Say.** "Everything we just did is recorded and reproducible as files."
**Panel should see.** `timeline.csv` (one row per second), `summary.md`, `loss_vs_recovered.png`,
`mos.png`, `datagram_size.png`. In `summary.md`: totals, **one segment per B-local settings
change** (repair mode, Incoming loss, jitter depth) with recovered/lost and mean/min MOS, and an
events list including the key rotation. The charts mark each local change with a dotted line.
**Caveats.**
- All figures describe what **B received**. Changes made on A (Bad Wi-Fi, Burst loss, delay/jitter,
  bitrate) are not labelled in B's export; refer to your own timestamps.
- MOS is an E-model estimate; delay is relative; late packets count as loss.
- This is one rehearsal-grade session on one LAN, **not** a result for the evaluation report. The
  report's figures come from the committed experiments.

**Time check.** If you are running 15 minutes, drop *Clean call* to 30 s and cut the *Bitrate*
segment; if 20, keep everything and take questions from the last minute.

---

## 3. Likely panel questions

**Why not just send the handshake over UDP and retry?**
That works about as well. With one 3 s deadline, UDP with a HELLO resend every 250 ms completes
86 % of the time at 20 % loss against 84 % for TCP (52 % vs 56 % at 30 %); naive one-shot UDP gets
18 % and 6 %. The gain comes from retransmission, not TCP. Our remaining argument is structural:
the hybrid sends zero IP fragments, so it does not depend on fragments crossing a path, and TCP
also carries rotation and call-end. We have not measured paths that drop fragments
([EXPERIMENT-full-handshake.md](EXPERIMENT-full-handshake.md)).

**If fragmentation does not change voice MOS, why does it matter?**
Because it changes whether the call connects. Voice frames never fragment in either design;
the 10.8 KB handshake does in the naive design. Naive UDP completion is consistent with `(1−p)^8`
([EVALUATION-REPORT.md](EVALUATION-REPORT.md) §6, §7).

**Is the MOS a real listener score?**
No. It is an ITU-T G.107 E-model estimate from residual loss and delay (§10). The offline FEC
experiment uses segmental SNR, also not perceptual. We have not run listening tests.

**Does FEC actually work live?**
The *Recovered* counter counts only frames rebuilt from LBRR data the next packet carried, and
untick *Encoder FEC* on A shows it stop. Unit tests pin the call order and show a SILK stream
recovers better than concealment. Bursts defeat it (single-frame lookahead). On Apple Silicon
FEC was silently off until we fixed the variadic-ABI Opus CTL call
([EVALUATION-REPORT.md](EVALUATION-REPORT.md) §8).

**Why a fixed 1200-byte cap?**
Conservative and simple; dynamic PMTU discovery is future work. Voice packets here are a small
fraction of the cap.

**What does "UDP fragments: 0" prove?**
By construction, nothing on UDP exceeds 1200 B and the handshake is on TCP. The measurement is the
tshark capture in `results/eval.csv`, not the panel text: zero fragments from the hybrid's voice
datagrams at every loss level. The handshake runs before that capture, so it is not captured.

**Why 10,844 B on screen but 10,842 B in the slides?**
10,842 B is key and signature material; each of the two messages adds a 1-byte version.

**How secure is it?**
Mutually authenticated ML-KEM-768 + ML-DSA-65 with pinned identities (ADR-0002), per-direction
nonce prefixes, ChaCha20-Poly1305. Not security-audited; no CA. The in-call control channel is
unauthenticated, so an on-path attacker can disrupt rotation (DoS) but not read audio
([ADR-0004](adr/0004-live-key-rotation.md)).

**What does key rotation protect against?**
It bounds how much traffic one key covers and keeps earlier keys safe if a later one leaks. It is a
hash-chain step, not a fresh key exchange, so it does not heal a compromised current key.

**Did you test on real networks or two physical devices?**
Evaluation is on loopback with `tc netem` (independent loss) and the NS-3 model. A two-device
run and real-network/middlebox validation are open items ([EVALUATION-REPORT.md](EVALUATION-REPORT.md) §12).

**How does this compare with QUIC or TLS?**
We do not claim to beat them. NS-3's QUIC is a proxy (a one-way UDP burst with no reply, which
flatters it against a real 1-RTT handshake), and no real QUIC/TLS stack is
benchmarked ([ns3/README.md](../ns3/README.md)).

**Why is the loss slider on A, but the tiles on B move?**
Outgoing controls change what the **peer** hears. Each laptop's tiles, graph, MOS and export show
the audio it **receives**. Incoming loss, repair mode and jitter depth act on your own receiver.

**Why does Bad Wi-Fi on B not change B's tiles?**
It impairs what B *sends*. Apply it on A to see it on B.

**What if the call fails to connect at 30 % loss?**
That is the measured behaviour: 56 % of full handshakes complete within 3 s at 30 % loss over TCP
(same for UDP retry within noise), so some attempts fail inside the deadline. A longer deadline was
not measured.
