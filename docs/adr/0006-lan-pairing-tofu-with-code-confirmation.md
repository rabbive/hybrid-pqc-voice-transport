# 0006 - LAN pairing: pin on first use, after both users confirm a code

Status: Proposed (experimental; not formally verified, not externally reviewed)
Date: 2026-10-16

## Context

Protocol v2 (ADR-0005) authenticates each peer by pinning its exact ML-DSA-65
public key. In the first v2 build that meant exporting a certificate on each
device and copying it to the other one (`keygen`, then `--cert` / `--peer-cert`).
For two people on the same Wi-Fi that is the slowest part of the demo and the
step most likely to go wrong: a wrong file, a stale file, or a shared file
(v1 finding F1).

The goal is seamless first contact on one LAN without giving up the pin: the
user opens the app, pairs once, and later calls to that friend need no files and
no prompt.

Constraints from the project plan: no new dependency, no new cryptographic
primitive, nothing bound to a display name, and every wait for a human has a
timeout and never blocks the audio or the Tk thread.

## Decision

Pair on first use, and save the peer's key only after **both** users have
confirmed a 6-digit code. Calls then use the unchanged `hpqv/2` path, pinned to
the saved friends. The implementation is `hpqv.pairing`, `hpqv.trust`,
`hpqv.discovery`, `hpqv.pairflow` and the pairing mode in `hpqv.secure`;
the protocol text is `docs/PROTOCOL-v2.md` section 4a-4d.

- **Separate ALPN.** Pairing runs on `hpqv-pair/1` over the same TLS 1.3 stack
  as calls (X25519MLKEM768 only, ML-DSA-65 only, no resumption). It accepts any
  ML-DSA-65 key except our own. A pairing channel never gets voice keys
  (`media_keys()` raises) and a call needs a pinned key even on a listener that
  also accepts pairing.
- **A window the user opened.** The listener accepts pairing only inside a
  120 s window the user opened, one pairing at a time, at most 3 attempts per
  window. A "codes differ", a failed commitment check or a declined pairing
  closes the window.
- **Commit, then reveal.** The responder sends
  `C = SHA-256(label || CB || fpR || fpI || nR)` first; the initiator draws its
  nonce `nI` only after receiving `C`; the responder then reveals `nR`, which the
  initiator checks against `C`. Both compute
  `code = SHA-256(label || CB || fpI || fpR || nI || nR)[0:4] mod 10^6` and show
  it as `ddd ddd`. `CB` is the RFC 9266 channel binding of this TLS session and
  `fp` is the SHA-256 of the raw ML-DSA-65 key TLS authenticated.
- **Both users decide.** Each side sends `CONFIRM` (1 = "codes match", 0 = no,
  cancel or timeout, 60 s). A device saves the peer only after its own user said
  yes **and** the peer's `CONFIRM = 1` arrived. Nothing is saved otherwise.
- **Trust is keyed by fingerprint.** `trust.json` stores certificates by the
  SHA-256 of the key. A name is a label. A different key under a known name is
  stored as a separate entry, flagged "key changed" in the prompt, and never
  replaces the old pin silently.
- **Discovery is a phonebook.** A UDP broadcast on port 47123 carries name,
  TCP port, a 16-hex-digit fingerprint hint and a pairing-open flag. It is never
  used to decide trust; the pin in TLS is.

### Why the code is 6 digits, not 48 bits

The first version of the plan (Task 2) used a 48-bit exporter-derived code with
no commitment step, on the argument that an active man-in-the-middle holding one
TLS session each side can re-randomise its own session until both codes match; each attempt costs it an
ML-DSA signature, and 48 bits was meant to put that search out of reach inside
the 10 s handshake timeout. The B2
design (revision 3, `docs/reviews/2026-10-16-b2-pairing-design.md`) supersedes
that: the commitment ordering (the same idea as Bluetooth Numeric Comparison and
ZRTP, RFC 6189 section 4.4.1.1) forces the attacker to fix its values before it
learns the honest nonce, so grinding is not possible and a short code that a
person can actually read aloud is enough. The attacker's chance per attempt is
about 1 in 10^6, and a window allows at most 3 attempts. The code in
`pairing.comparison_code` is 6 decimal digits (about 19.9 bits). That figure is
the attacker's per-attempt success probability, not a strength of a secret key:
the code protects the pin decision, not the media.

## Consequences

- **One human step per new friend.** Both people compare one 6-digit code, once.
  After that, `call <friend>` or a call by `HOST:PORT` needs no prompt and no file.
- **A first call where the users skip the check is unauthenticated.** Pressing
  "Codes match" without comparing removes the only protection against an active
  attacker at pairing time. The software cannot tell.
- **The security argument for the commitment step is ours.** It applies a
  published pattern to our inputs with SHA-256; neither Bluetooth's nor ZRTP's
  analysis covers it, and there is no formal or symbolic model. It is labelled
  experimental in the code, the CLI, the GUI and `PROTOCOL-v2.md`.
- **Residual attack probability** is about 10^-6 per attempt; with at most 3
  attempts per window that is about 3 x 10^-6 per opened window, an arithmetic
  bound under the assumption that SHA-256 behaves as a random function and the
  users really compare. It is not a measured value.
- **Interrupted pairing can leave one side paired.** If the connection drops
  after one side saved and before the other did, the next call fails ("pair
  again"); pairing again overwrites the record for the same fingerprint. There
  is no automatic recovery.
- **Compromise is not handled.** A stolen private key or a compromised device
  stays trusted until each friend removes it by hand (`forget`).
- **Discovery is unauthenticated** and can be spoofed or blocked. Spoofing can
  show a wrong name in a list but cannot create trust. Client-isolated or
  broadcast-blocked Wi-Fi (campus, guest) leaves the list empty; pairing and
  calls by `HOST:PORT` still work if the network passes TCP between the devices.
- **Platform scope.** Tested on macOS. Linux and Windows behaviour is not
  tested here.
- The trust file is not protected against another program running as the same
  user.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Exchange certificate files (the first v2 build) | Safe if done right, but a manual, error-prone step for every pair of devices. Kept as the fallback: `--key/--cert/--peer-cert` still work and pin only |
| Paste or read out a full fingerprint | A full SHA-256 fingerprint is 16 groups of 4 hex digits; it is long to compare by eye or by voice. A 6-digit code is short enough to read aloud once |
| Trust on first use with no human check | Whoever connects first while the window is open becomes the friend. No protection against an active attacker |
| A 48-bit code with no commitment (original plan Task 2) | Needs the user to read 12 hex digits, and the argument rests on timing (grinding within the 10 s timeout). The commit-then-reveal ordering removes the grinding and permits a short code |
