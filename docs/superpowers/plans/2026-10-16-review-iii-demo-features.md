# Review III demo features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Review III panel visible proof that the v2 call is encrypted and authenticated, plus three features that did not exist at Review II: an "on the wire" view, an incoming-call prompt that shows the caller's fingerprint and SAS, and a related-work comparison.

**Architecture:** Everything sits on top of the merged v2 stack (`secure.py`, `keyring.py`, `packet.py`, T4 integration in `demo.py`/`gui.py`). No protocol change: the accept prompt is listener-side only, and the caller infers "answered" from the first authentic datagram. Wireshark gets a Lua dissector for the v2 voice datagram so the panel sees labelled headers and an opaque payload.

**Tech Stack:** Python 3.11, tkinter, pyOpenSSL/cryptography (already pinned), Wireshark 4.x Lua API. No new Python dependencies.

**Spec:** Agreed in chat on 2026-10-16 (no separate design doc). Summary:
1. Idea 1, "what an eavesdropper sees": an in-app panel showing the last datagram this peer sent, as an observer on the wire would see it, plus a Wireshark dissector and a capture guide.
2. Idea 2, "fingerprint before you answer": when a call comes in, the listener sees the caller's certificate fingerprint and the 4-byte SAS and must press Accept. Decline, or no answer within 30 s, ends the call.
3. Idea 3, comparison: a related-work table of hpqv v2 vs AEGIS-VoIP, Quantum-Safe-Video-Conference-App-V3 and thilakragav's FYP, sourced from their READMEs, for one slide.
No code from those repos is copied: neither has a license.
Out of scope: any tool that sends crafted, replayed or modified packets.

**Deadline:** guide review in 4 days (2026-10-20); panel in about 7 (2026-10-23). Tasks 1–3 are the must-haves; 4–6 can slip one day.

## Global Constraints

- Run tests with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q <tests>`; set `UV_CACHE_DIR=$TMPDIR/uvcache` if uv cannot write its cache.
- Never log, export or display key material, exporter output, PEM private keys or audio (plaintext Opus/PCM). Ciphertext and headers may be shown on screen; they are what the network already carries.
- Telemetry/export must not gain any new field from this work (`telemetry.COLUMNS` and `_summary` are unchanged).
- Bounded everything: no loop or buffer whose size is driven by peer input.
- Never implement a cryptographic primitive.
- No new dependencies in `pyproject.toml`/`uv.lock`.
- Do not touch `results/` or claim numbers you did not measure.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (other models use their own name). Do not push.
- Branch: work on `feat/review-iii-demo` cut from `feat/secure-session-v2` at or after `52a4557`.

## Review Focus

1. **The listener hangs up or closes the window while the incoming-call dialog is open.** The dialog disappears, the worker exits, the caller's call ends. Test in Task 3 (`test_wait_for_answer_returns_false_when_stopped`).
2. **Accept pressed after the 30 s timeout already declined, or in the next call.** It's ignored and doesn't leak into the next call (answers queue is per call). Test in Task 3 (`test_stale_answer_does_not_leak_into_next_call`).
3. **Another LAN host floods the listener's UDP port while it's ringing.** `drain_udp` stops after a bounded number of datagrams and does not hang the worker. Test in Task 3 (`test_drain_udp_is_bounded`).
4. **The wire panel before any datagram was sent, or after the call ends.** It shows `—`, not an exception or stale bytes from a previous call. Test in Task 1 (`test_wire_rows_before_any_datagram`).
5. **Caller hangs up while ringing.** The listener, if it then accepts, sees the call end within about 1 s with the existing "the other side hung up" text. Covered by the existing callctl `peer_ended` path. Manual check in Task 6's rehearsal script.

---

### Task 1: Last-datagram capture and wire rows

**Files:**
- Modify: `src/hpqv/demo.py` (`Stats.__init__`, `_sender_loop.deliver`)
- Modify: `src/hpqv/gui_logic.py` (add `wire_rows`)
- Test: `tests/test_demo_loop.py`, `tests/test_gui_logic.py`, `tests/test_telemetry.py`

**Interfaces:**
- Consumes: `packet.parse_header(dg) -> (session_id, seq, flags)`, `packet.GEN_MASK`, `packet.FLAG_FEC`, `packet.HEADER_LEN`, `packet.TAG_LEN`.
- Produces:
  - `Stats.last_datagram: bytes`, default `b""`, set under `stats.lock` by `deliver()` to the exact bytes passed to `sendto`.
  - `gui_logic.wire_rows(dg: bytes) -> list[tuple[str, str]]` with labels, in order: `"Size on the wire"`, `"Version/type"`, `"Session id"`, `"Sequence"`, `"Key generation (mod 4)"`, `"FEC hint"`, `"Encrypted payload + tag"`, `"Payload bytes (first 32)"`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_logic.py
def test_wire_rows_before_any_datagram():
    assert all(v == NONE for _, v in wire_rows(b""))

def test_wire_rows_show_header_fields_and_only_ciphertext():
    keys = MediaKeys(send_secret=b"\x01" * 32, recv_secret=b"\x02" * 32, session_id=b"\xab\xcd\x00\x01")
    ring = Keyring(keys)
    ring.rotate_next()                                # generation 1
    dg = ring.seal(7, b"\x55" * 60, extra_flags=packet.FLAG_FEC)
    rows = dict(wire_rows(dg))
    assert rows["Size on the wire"] == f"{len(dg)} B (cap 1200 B)"
    assert rows["Version/type"] == "0x21 (v2 voice)"
    assert rows["Session id"] == "abcd0001"
    assert rows["Sequence"] == "7"
    assert rows["Key generation (mod 4)"] == "1"
    assert rows["FEC hint"] == "on"
    assert rows["Encrypted payload + tag"] == f"{len(dg) - 12} B"
    assert "55 55 55" not in rows["Payload bytes (first 32)"]     # plaintext never appears
    assert rows["Payload bytes (first 32)"].replace(" ", "").replace("\n", "") == dg[12:44].hex()

def test_wire_rows_for_a_malformed_datagram():
    assert dict(wire_rows(b"\x00" * 40))["Version/type"] == "not a v2 datagram"
```

```python
# tests/test_demo_loop.py
def test_sender_records_the_exact_last_datagram_sent():
    # fake udp: records sendto; source: stub yielding 3 FRAME_BYTES silent frames;
    # real Keyring over fixed MediaKeys; CallControls() defaults; threading.Event()
    ...
    demo._sender_loop(udp, ("127.0.0.1", 9), ring, source, CallControls(), stats, threading.Event())
    assert stats.last_datagram == udp.sent[-1]
```

```python
# tests/test_telemetry.py, extend test_export_never_writes_the_sas_the_fingerprint_or_key_material
    stats.last_datagram = b"\x21" + b"\xee" * 60
    # ...existing export...
    assert "last_datagram" not in COLUMNS
    # and no exported file contains "eeeeee"
```

- [ ] **Step 2: Run them, expect FAIL** (`wire_rows` undefined, `last_datagram` missing)

Run: `... pytest -q tests/test_gui_logic.py tests/test_demo_loop.py tests/test_telemetry.py`

- [ ] **Step 3: Implement**
  - `Stats.last_datagram = b""` next to `max_datagram`. Comment: on screen only, ciphertext, never exported.
  - In `deliver(dg)`, inside the existing `with stats.lock:`, `stats.last_datagram = dg`.
  - `wire_rows`: return all-`NONE` rows for `b""`. Catch `packet.Reject` from `parse_header` and return `"not a v2 datagram"` for Version/type with `NONE` for the others. Format the payload hex as 16 bytes per line in byte pairs separated by spaces.

- [ ] **Step 4: Run tests, expect PASS**, plus the full suite: `... pytest -q --ignore=tests/test_gui_window.py`

- [ ] **Step 5: Commit** `feat(demo): record the last sent datagram and format it as wire rows`

---

### Task 2: "On the wire" panel in the Security tab

**Files:**
- Modify: `src/hpqv/gui_panels.py` (add `WirePanel`)
- Modify: `src/hpqv/gui.py` (`_build`, `_refresh`)
- Test: `tests/test_gui_window.py` (follow its existing `HPQV_GUI_TESTS` pattern)

**Interfaces:**
- Consumes: `gui_logic.wire_rows`, `Stats.last_datagram` (Task 1), `_Grid` in `gui_panels.py`.
- Produces: `WirePanel(parent)` with `update(dg: bytes) -> None`; `DemoWindow.wire` attribute.

- [ ] **Step 1: Write failing window test** `test_security_tab_has_a_wire_panel_that_starts_empty`: build `DemoWindow`, assert every value label in `win.wire.values` reads `NONE`. Then set `win.stats.last_datagram` to a sealed datagram, call `win._refresh()`, and assert `"Sequence"` shows its seq.

- [ ] **Step 2: Run with `HPQV_GUI_TESTS=1`, expect FAIL**

- [ ] **Step 3: Implement**
  - `WirePanel(_Grid)` in a `LabelFrame` titled `"What an eavesdropper sees (your last packet)"`. Use `Mono.TLabel` for the payload row. Under the grid, a wrapped caption: `"This is everything the network carries. The voice is inside the encrypted payload; without the session keys it cannot be decoded. Wireshark shows the same bytes."`
  - Build it in the Security tab after `SecurityPanel`. In `_refresh`, call `self.wire.update(stats["last_datagram"])`. `stats` is already `self.stats.as_dict()`, and a new call's fresh `Stats()` resets it to `b""`.

- [ ] **Step 4: Run window tests with `HPQV_GUI_TESTS=1`, and the full suite, expect PASS.** Then launch `uv run python -m hpqv.gui` for two local peers on 127.0.0.1 and check by eye that the panel updates about every 200 ms and the Security tab still fits at the 1100×640 minimum size.

- [ ] **Step 5: Commit** `feat(gui): show the last datagram as an observer on the wire sees it`

---

### Task 3: Incoming-call prompt with fingerprint and SAS

**Files:**
- Modify: `src/hpqv/demo.py` (add `drain_udp`, `ANSWER_TIMEOUT`)
- Modify: `src/hpqv/gui_logic.py` (add `incoming_call_text`, `wait_for_answer`)
- Modify: `src/hpqv/gui_panels.py` (add `IncomingCallDialog`)
- Modify: `src/hpqv/gui.py` (`_start`, `_run`, `_drain`, `_refresh`, `_stop`, `_on_close`)
- Test: `tests/test_gui_logic.py`, `tests/test_demo_loop.py`, `tests/test_gui_window.py`

**Interfaces:**
- Consumes: `Stats.peer_fingerprint`, `Stats.sas` (set by `establish_timed`), `gui_logic.wrap_fingerprint`, `gui_logic.format_sas`, `control.MSG_CALL_END`, `SecureChannel.send`.
- Produces:
  - `demo.ANSWER_TIMEOUT = 30.0`
  - `demo.drain_udp(sock: socket.socket, limit: int = 4096) -> int`: discards datagrams already queued, without blocking, and stops at `limit`. Returns how many it discarded and restores the socket's previous timeout.
  - `gui_logic.incoming_call_text(fingerprint: str, sas: str) -> str`
  - `gui_logic.wait_for_answer(answers: "queue.Queue[bool]", stop: threading.Event, timeout: float, tick: float = 0.2) -> bool`: True only if `True` was put before `timeout` and `stop` was not set.
  - `IncomingCallDialog(root, text: str, sas: str, on_answer: Callable[[bool], None])`, a non-modal `Toplevel` with `close()`.
  - GUI event kinds: `("incoming", {"fingerprint": str, "sas": str})` and `("ringing", None)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gui_logic.py
def test_incoming_call_text_shows_wrapped_fingerprint_and_sas():
    fp = " ".join(["abcd"] * 16)
    text = incoming_call_text(fp, "c0ffee42")
    assert wrap_fingerprint(fp) in text and "c0ff ee42" in text

def test_wait_for_answer_accept_decline_timeout():
    q = queue.Queue(); q.put(True)
    assert wait_for_answer(q, threading.Event(), 1.0) is True
    q.put(False)
    assert wait_for_answer(q, threading.Event(), 1.0) is False
    assert wait_for_answer(queue.Queue(), threading.Event(), 0.3) is False

def test_wait_for_answer_returns_false_when_stopped():
    stop = threading.Event(); stop.set()
    t0 = time.monotonic()
    assert wait_for_answer(queue.Queue(), stop, 30.0) is False
    assert time.monotonic() - t0 < 0.5
```

```python
# tests/test_demo_loop.py
def test_drain_udp_discards_queued_datagrams_and_keeps_timeout():
    # two loopback UDP sockets; send 3 datagrams; rx.settimeout(0.5)
    assert demo.drain_udp(rx) == 3
    assert rx.gettimeout() == 0.5
    # nothing left: rx.setblocking(False); rx.recv(1) raises BlockingIOError

def test_drain_udp_is_bounded():
    # send 50 datagrams, drain with limit=10
    assert demo.drain_udp(rx, limit=10) == 10
```

```python
# tests/test_gui_window.py
def test_stale_answer_does_not_leak_into_next_call():
    # _start creates cfg["answers"]; put True into call 1's queue after it ended;
    # call 2's cfg["answers"] is a different, empty Queue
```

- [ ] **Step 2: Run them, expect FAIL**

- [ ] **Step 3: Implement the pure parts**: `drain_udp`, `ANSWER_TIMEOUT`, `incoming_call_text`, `wait_for_answer`. `wait_for_answer` polls `answers.get(timeout=tick)` until the deadline or `stop`.

- [ ] **Step 4: Run the Step 1 non-window tests, expect PASS**

- [ ] **Step 5: Wire it into the GUI**
  - `_start`: `cfg["answers"] = queue.Queue()`; keep `self._answers = cfg["answers"]`.
  - `_run`, listen branch, after `establish_timed` and `_open_bound_udp`:
    `put(("incoming", {"fingerprint": stats.peer_fingerprint, "sas": stats.sas}))`, then
    `if not gui_logic.wait_for_answer(cfg["answers"], stop, demo.ANSWER_TIMEOUT):` send `MSG_CALL_END` (ignore `OSError`), put state `"declined"` (or `"hung up"` if `stop` is set), and return. On accept, call `demo.wait_call_start(...)` as today (CALL_START is already buffered in TLS), then `demo.drain_udp(udp)` before `_run_call` so seconds of stale ringing audio aren't played.
  - `_run`, call branch: after sending CALL_START, `put(("ringing", None))` instead of the "connected — talk" state.
  - `_drain`: on `"incoming"`, open `IncomingCallDialog`. Its title is `"Incoming call"`, the body is `incoming_call_text(...)`, and the SAS is shown again in `State.TLabel` at about 28 pt. Buttons `Accept` and `Decline` call `self._answers.put(bool)` and close the dialog; the window-close X counts as Decline. On `"ringing"`, set `self._ringing = True` and state `"ringing — waiting for them to answer"`. On `"done"`, close any open dialog.
  - `_refresh`: if `self._ringing` and `stats["received"] > 0`, clear it and set state `"connected — talk"`.
  - `_stop` and `_on_close`: close any open dialog. `stop` being set already ends `wait_for_answer`.
  - CLI `listen` keeps auto-accepting. Add one line to `--help`/docs saying the prompt is GUI-only.

- [ ] **Step 6: Run the full suite with `HPQV_GUI_TESTS=1`, expect PASS.** Then rehearse on two local GUIs:
  - Accept: audio flows, and the dialog SAS matches the caller's Security tab.
  - Decline: the caller shows "the other side hung up".
  - Wait 30 s: auto-decline.
  - Close the listener window while ringing: the caller's call ends.

- [ ] **Step 7: Commit** `feat(gui): ask before answering, showing the caller's fingerprint and SAS`

---

### Task 4: Wireshark dissector and capture guide

**Files:**
- Create: `tools/wireshark/hpqv.lua`
- Create: `docs/DEMO-WIRESHARK.md`
- Test: manual, with `tshark` (no pytest)

**Interfaces:**
- Consumes: the v2 datagram layout in `docs/wire-format.md` (v2 section). It must match `packet.py` exactly: `ver_type` 0x21, flags bits 0–1 generation, bit 2 FEC, bits 3–7 reserved, session_id[4], seq uint48 BE, then ciphertext+tag.
- Produces: protocol `hpqv` with fields `hpqv.ver_type`, `hpqv.gen`, `hpqv.fec`, `hpqv.session_id`, `hpqv.seq`, `hpqv.ciphertext`.

- [ ] **Step 1: Write the dissector** as a heuristic UDP dissector (ports are user-chosen and the caller's is ephemeral). Accept a packet only if byte 0 is `0x21`, the reserved flag bits are 0 and the length is 29–1200. Otherwise return false so other dissectors run. The info column reads `hpqv v2 voice seq=<n> gen=<g> len=<L>`. Label the payload subtree `"Encrypted Opus frame + Poly1305 tag (ChaCha20-Poly1305)"`.

- [ ] **Step 2: Verify on a real capture.** `brew install --cask wireshark`. Run two local CLI peers on 127.0.0.1 with `--input file --loop` and capture on `lo0`:
  - `tshark -i lo0 -a duration:10 -w $TMPDIR/hpqv.pcapng`
  - `tshark -X lua_script:tools/wireshark/hpqv.lua -r $TMPDIR/hpqv.pcapng -Y hpqv | head`

  Expected: one line per voice datagram, increasing seq, every length ≤ 1200.
  - `tshark -r $TMPDIR/hpqv.pcapng -Y 'tls.handshake.type == 1' -V | grep -i -A3 supported_groups`

  Expected: group `0x11ec`, named X25519MLKEM768 on recent Wireshark or shown as Unknown (0x11ec) on older builds. Note which one you got.

- [ ] **Step 3: Write `docs/DEMO-WIRESHARK.md`.** Cover:
  - Install, and load the plugin by copying it to `~/.local/lib/wireshark/plugins/` or via `-X lua_script:`.
  - Which interface to capture on (LAN interface for two laptops, `lo0` to rehearse on one).
  - What to point at, with the display filter for each:
    1. The TLS 1.3 ClientHello on TCP: group 0x11ec, signature scheme 0x0905. Its size is far over one MTU, yet `ip.flags.mf == 1` finds nothing.
    2. `hpqv` packets: headers readable, payload opaque.
    3. `udp.length > 1208` finds nothing.
    4. Telephony → RTP Streams finds no stream, because there's no plaintext RTP to pick up.
    5. Press Rotate keys and watch `hpqv.gen` change.
  - The honest limits: Wireshark proves the payload is opaque and the handshake is PQ hybrid. It does not by itself prove there's no man in the middle; the SAS check does that.

- [ ] **Step 4: Commit** `docs: Wireshark dissector and capture guide for the v2 voice datagram`

---

### Task 5: Related-work comparison

**Files:**
- Create: `docs/RELATED-WORK.md`
- Modify: `docs/REVIEW-2-TO-REVIEW-3-CHANGES.md` (append a section)
- Test: `tests/test_docs_current.py` (run it; extend only if it already checks doc links)

**Interfaces:** none (docs).

- [ ] **Step 1: Write `docs/RELATED-WORK.md`.** Header line: `Compared from each project's public README on 2026-10-16; not independently built or tested. No code from these projects is used (none has a license).` Then one table with columns hpqv v2 | [AEGIS-VoIP](https://github.com/MuhammadMusaab-UlHaq/AEGIS-VoIP-Post-Quantum-Secure-Voice-Video-Communication-System) | [Quantum-Safe-Video-Conference-App-V3](https://github.com/Shubhamg-1811/Quantum-Safe-Video-Conference-App-V3) | [thilakragav FYP](https://github.com/thilakragav/FYP_Post-Quantum-Encrypted-and-Obfuscated-Voice-Obfuscation-System), and these rows (README facts only; write "not stated" where a README is silent):

| Row | hpqv v2 | AEGIS-VoIP | QS Video Conf V3 | thilakragav FYP |
|---|---|---|---|---|
| Key exchange | X25519MLKEM768 in TLS 1.3 | X25519 + Kyber-1024 | Kyber-768 | Kyber-512 |
| Peer authentication | mutual, pinned ML-DSA-65 certificates | SAS (ZRTP-inspired) | Dilithium3 signature; key distribution assumed secure | not stated (registry has no auth, per README) |
| Media encryption | ChaCha20-Poly1305, per-direction keys | DTLS-SRTP + AES-256-GCM (insertable streams) | SRTP AES-256-ICM + HMAC-SHA1-80 | AES-256-GCM |
| Human MITM check | SAS + fingerprint shown before answering | SAS | recommends TOFU/QR | not stated |
| Replay protection on media | 256-packet window | not stated | not stated | not stated |
| Audio | full duplex | full duplex (WebRTC) | full duplex | one-way |
| Handshake vs MTU | handshake on TCP, datagrams ≤ 1200 B | not discussed | not discussed | not discussed |
| Measured evaluation | netem loss sweeps, fragment counts, E-model MOS, FEC SNR | not stated | performance claims, no method given | live telemetry display |

  Below the table, add a three-line "What we took from them" section: the SAS idea is also in AEGIS, the "verify before trust" prompt follows the TOFU/QR advice in QS Video Conf's README, and the eavesdropper view is a reframing of thilakragav's "hear the scrambled audio" feature. Then add one line for the slide: `Only hpqv measures how the post-quantum handshake behaves under packet loss.`

- [ ] **Step 2: Append to `docs/REVIEW-2-TO-REVIEW-3-CHANGES.md`** a section `## 7. After this document: protocol v2 and Review III demo features`. It gets four bullets, each linking its doc:
  - TLS 1.3 with X25519MLKEM768. This corrects this file's "hybrid means TCP+UDP only" line: v2 is also a classical+PQ hybrid key exchange.
  - Authenticated control channel.
  - Per-direction media keys with a replay window.
  - The three demo features.

  Do not rewrite sections 1–6; they describe v1.

- [ ] **Step 3: Run** `... pytest -q tests/test_docs_current.py`, expect PASS

- [ ] **Step 4: Commit** `docs: related-work comparison and v2 addendum for Review III`

---

### Task 6: Review III demo script

**Files:**
- Modify: `docs/DEMO-RUNBOOK.md` (add a `## Review III script (v2)` section at the top)
- Test: one full rehearsal on two machines (manual)

- [ ] **Step 1: Write the script** as numbered steps with what to say at each. Target is 6 minutes.
  1. Each laptop creates its identity, and only the `.crt` files are swapped. Say: "Unlike Review II, neither side holds the other's secret key."
  2. Start the Wireshark capture with the plugin.
  3. Call. The listener's prompt shows the fingerprint and code; both read the code aloud and Accept.
  4. Wireshark: ClientHello group 0x11ec, `hpqv` packets with an opaque payload, no fragments, no RTP stream.
  5. Security tab: the wire panel matches Wireshark byte for byte.
  6. Rotate keys: the generation changes in the panel and in `hpqv.gen`, and the audio never drops.
  7. Loss slider and FEC (the Review II demo, now on v2).
  8. Related-work slide.

  Add a "if something fails" line for each step, taken from `demo.FAILURE_TEXT`.

- [ ] **Step 2: Rehearse once end to end on two laptops on one LAN** and record any step that took over a minute or failed. This includes Review Focus item 5 (caller hangs up while ringing). Fix the doc or file a bug.

- [ ] **Step 3: Commit** `docs: Review III demo script`
