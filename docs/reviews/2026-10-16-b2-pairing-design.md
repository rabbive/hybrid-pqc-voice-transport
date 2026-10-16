# Phase B2: No-files pairing, concise design (revision 3)

Date: 2026-10-16. Scope: a **course-project proof of concept**. It must be
demonstrable, documented and credible, not production-hardened. The B1 security
foundation (`932fb1f`) is kept unchanged. The detailed revision 2, which covers
signed approvals, automatic recovery and the full Windows inventory, is kept in
[2026-10-16-b2-pairing-design-rev2-detailed.md](2026-10-16-b2-pairing-design-rev2-detailed.md)
as reference only.

**Status: design proposal.** Not formally verified, not externally reviewed,
not production-secure.

## 1. The user flow

1. Each app creates its own ML-DSA-65 identity on first launch, in the per-OS
   app directory. The private key never leaves the device.
2. On the listening device, the user clicks **"Allow new device"**. This opens a
   2-minute pairing window.
3. On the other device, the user sees the listener in a **nearby devices** list
   (from a UDP broadcast on the LAN) and clicks **Pair**. Typing `HOST:PORT`
   works as a fallback.
4. Both screens show the same **6-digit code**. The users read it aloud, and
   **both** press **"Codes match"**.
5. Each device saves the other's public key as a trusted friend. Later calls
   connect only to pinned friends, using the unchanged B1 call path.
6. If anything fails, nothing is trusted and the user simply **pairs again**.
   There is no automatic recovery.

## 2. Protocol (the simplest defensible version)

The building blocks all already exist in B1:
- TLS 1.3 with X25519MLKEM768 and mutual ML-DSA-65 authentication;
- the `SecureChannel` framing;
- the RFC 9266 TLS channel binding.

Pairing adds one ALPN value and five small messages.

| Step | Message | Content |
|---|---|---|
| 0 | TLS 1.3 handshake, ALPN **`hpqv-pair/1`** | Same B1 restrictions: group 0x11EC only, ML-DSA-65 only, TLS 1.3, no resumption. Any ML-DSA-65 peer key is accepted **except our own**. The listener accepts this ALPN only while its pairing window is open, and handles one pairing at a time |
| 1 | R→I `READY` | existing message |
| 2 | R→I `COMMIT` | `C = SHA-256("hpqv-pair/1 commit" ‖ CB ‖ fpR ‖ fpI ‖ nR)` |
| 3 | I→R `NONCE` | `nI`, 32 random bytes, drawn **after** C was received |
| 4 | R→I `REVEAL` | `nR`, 32 random bytes. I checks it against `C`; a mismatch aborts |
| — | both | `code = SHA-256("hpqv-pair/1 code" ‖ CB ‖ fpI ‖ fpR ‖ nI ‖ nR)[0:4]` as a big-endian integer `mod 10⁶`, shown as `ddd ddd` |
| 5 | both `CONFIRM` | one byte: 1 = user pressed "Codes match", 0 = "Don't match" or cancel |

Definitions:
- `CB = TLS-Exporter("EXPORTER-Channel-Binding", "", 32)` (RFC 9266), computed
  locally on each side.
- `fpX = SHA-256(raw ML-DSA-65 public key)`, taken from the certificates the TLS
  session actually authenticated.

All messages travel inside TLS, so they are encrypted, authenticated, ordered
and replay-protected. Display names and discovery data never enter a hash.

**Saving the friend.** A device saves the peer only when its own user said yes
and it received the peer's `CONFIRM = 1`. Then it closes the session.

**Interrupted pairing.** If the connection drops after one side saved and before
the other did, the next call fails cleanly ("this device isn't paired on the
other side; pair again"). Pairing again simply overwrites the record for the
same fingerprint.

## 3. Why the commitment step: the one non-obvious design choice

**The problem.** A code computed only from TLS session data can be targeted by
an active man-in-the-middle. The attacker controls one of the two TLS sessions,
so it can keep re-randomising it until both codes match. ZRTP documents exactly
this problem (RFC 6189 §4.4.1.1).

**The fix.** The responder commits to its nonce before it sees the initiator's.
Bluetooth Numeric Comparison uses the same ordering (Core Specification 6.2,
Vol 3, Part H, §2.3.5.6.2). In both sessions, the attacker must then fix its own
values before it learns the honest device's second nonce, so it can't steer the
codes. Each attempt gives it about **1 chance in 10⁶**.

**What is ours.** This is our own application of that published pattern to the
inputs above, using SHA-256. Neither Bluetooth's nor ZRTP's analysis covers it.
It is **not formally verified**.

## 4. Simple abuse limits
- Pairing is possible only inside a window the user opened, for at most 2 minutes.
- One pairing at a time.
- At most 3 attempts per window.
- **A "Don't match" or a failed commitment check closes the window,** discards
  that attempt's nonces and code, and shows a warning.
- The listener keeps accepting connections after stray or failed ones. This
  fixes T6 finding 6, and it bounds how many handshakes can run at once.

## 5. Stated limitations
1. **Careless users defeat it.** If both users press "Codes match" without
   comparing, an active attacker can win.
2. **About 10⁻⁶ per attempt.** That's the attacker's chance of matching codes,
   and the window and attempt limits bound the number of attempts.
3. **No defence against a compromised device or a stolen private key.** Friends
   must remove that device by hand.
4. **No automatic recovery.** An interrupted pairing must be repeated, and may
   briefly leave one side paired and the other not.
5. **Discovery is unauthenticated.** Names are labels; the fingerprint suffix
   shown in the list is the real identity.
6. **Some networks block it.** Client-isolated Wi-Fi (campus or guest networks)
   blocks discovery and calls; use a hotspot or a home router.
7. **The trust file isn't protected against another program running as the same
   user.**
8. **Not formally verified or externally reviewed.** The ML-DSA codepoint
   (draft-ietf-tls-mldsa-06) is still a draft, not an RFC.

## 6. What changes in the code
- **`secure.py`:**
  - a trusted-key list for the listener;
  - a pairing mode (ALPN `hpqv-pair/1`, unknown ML-DSA keys allowed, own key
    refused);
  - `channel_binding()`.

  The call path stays exactly as in B1.
- **New:**
  - `trust.py`: a JSON store keyed by fingerprint, with atomic writes and
    owner-only permissions;
  - `pairing.py`: the five messages and the code;
  - `discovery.py`: the UDP broadcast;
  - a small shared listener with an accept loop that keeps running.
- **CLI:** `pair`, `peers`, `forget`. The existing `--key/--cert/--peer-cert`
  flags keep working.
- **GUI:** a friend list instead of the three file pickers; an "Allow new device"
  button; a code dialog.
- **Unchanged:** the voice packets, the keyring and UDP. No new dependencies.

## 7. Tests (automated)

| Test | Checks |
|---|---|
| Successful pairing, then a pinned call | Both devices save each other, and a call to the friend connects |
| Commitment order and attacker in the middle | The initiator draws its nonce only after the commitment arrives. An in-process attacker running two TLS sessions gets different codes (over many seeded trials). A commitment taken from another session fails the check |
| Rejection paths | A "Don't match", a cancel, a timeout or a dropped connection saves nothing. A mismatch closes the window |
| Window and limits | Pairing is refused when the window is closed. A second pairing at the same time is refused. A fourth attempt in one window is refused |
| Identity checks | Pairing with yourself is refused. Changing an already-paired friend's key needs a full re-pair, and the old friend record is kept until then |
| Robustness | Malformed or oversized messages are rejected. The listener survives 100 stray connections. Discovery tables stay bounded and can't create trust |

Where each runs:
- **Linux, macOS and Windows CI.**
- **Real-hardware checks are recorded separately.** Two-device pairing on real
  Wi-Fi is one; Windows 10/11 x64 desktop testing needs physical machines,
  because GitHub's x64 Windows runners are Windows **Server**.

## 8. After B2: phases and checkpoints

| Phase | Deliverable | Checkpoint evidence |
|---|---|---|
| **B2** | The pairing above | Full suite results on macOS and Linux (and Windows CI if pushed with permission); a two-process demo transcript |
| **C1, calls and shutdown** | The listener fix; Windows `SO_EXCLUSIVEADDRUSE`; dead-peer detection (PING every 1 s, call ends after 10 s of silence); clean Hang up and window-close with every thread joined | Tests that kill or freeze the peer; a shutdown test that finishes in under 3 s |
| **C2, continuous playback** | A 20 ms playout clock; a bounded jitter buffer; FEC from the next packet, otherwise PLC; simple adaptive depth | Existing live-scenario tests plus outage and reordering tests |
| **D1, Diagnostics tab** | Live security values (protocol, group, fingerprints, code, key generation), network values (loss, jitter, round-trip time, rejected packets), audio values (buffer, FEC/PLC, underruns), each marked measured or estimate; reset; CSV/JSON export | Pure-logic tests; a screenshot from a real run |
| **D2, impairment demos and benchmarks** | Named seeded profiles; FEC off/on comparison; hybrid-vs-classical handshake benchmark (classical isolated to the benchmark) | Fresh CSV/JSON in `results/v2/` produced by committed scripts; the v1 `results/` stay untouched |
| **E, setup and demo** | A setup guide, a 5-minute demo script, README updates | A clean-environment run on macOS. Windows reported as CI-only unless tested on hardware |
