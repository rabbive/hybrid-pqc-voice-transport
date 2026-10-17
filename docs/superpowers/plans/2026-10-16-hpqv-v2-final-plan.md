# hpqv v2 Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the v2 security foundation and add seamless same-WiFi pairing.
Then add the audio-resilience, diagnostics, impairment-simulation and benchmark
features from the original requirements.

**Architecture:**
- **TLS 1.3 control channel** over TCP: X25519MLKEM768 key exchange, ML-DSA-65
  mutual authentication (already merged).
- **Peer trust:**
  - Peers find each other with a stdlib UDP broadcast.
  - A peer's key is trusted once it is pinned in a local known-peers store,
    after both users confirmed a 48-bit code derived from the TLS exporter.
- **UDP media:**
  - Exporter-keyed v2 datagrams with replay protection (already merged).
  - A clock-driven playout thread pulls from a bounded jitter buffer.
  - A receiver-report loop drives an adaptive Opus controller.
- **Evaluation:** an isolated loopback impairment proxy feeds seeded benchmarks
  that write `results/v2/`.

**Tech Stack:** Python 3.11; pyOpenSSL 26.4 on `cryptography` 50.0.1 (bundled
OpenSSL 4.0.2); opuslib with the repo's ctypes CTL shim (`audio._encoder_set`);
Tk; pytest.

**Spec:** [docs/superpowers/specs/2026-10-16-hpqv-v2-design.md](../specs/2026-10-16-hpqv-v2-design.md).
It links the verbatim original requirements, `docs/PROTOCOL-v2.md` and the
Phase B contract `docs/superpowers/plans/2026-10-16-secure-session-v2.md`.

## Global Constraints

- **Python and dependencies:** Python `>=3.11,<3.12`. Only `cryptography` 50.x,
  `pyopenssl>=26.4,<27` and the existing deps. **No new dependency**; discovery
  is stdlib-only.
- **Datagram size:** every UDP datagram is ≤ `packet.MAX_DATAGRAM` = 1200 B.
- **No fallback:** never a production path to a classical-only group;
  `secure.GROUP == "X25519MLKEM768"` is enforced after every handshake.
- **No secrets:** never log or export private keys, PEM keys, exporter output,
  media secrets or audio. The code (SAS) and fingerprints go to the GUI and
  stdout only, never into exports.
- **Never block the user or the audio:** no blocking call on the Tk thread or in
  the playout tick. A human answer (pairing) is awaited on a worker thread with
  a timeout.
- **Bounded:** every queue, buffer, table and read is bounded. No allocation is
  sized by unauthenticated input.
- **Results:** v1 `results/*` are never modified. New results go only under
  `results/v2/`, produced by a command in this repo, and docs cite only those
  files.
- **Tests:**
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q -p no:cacheprovider --ignore=tests/test_gui_window.py`.
  The baseline on 52a4557 is `457 passed, 15 skipped`. Each task keeps the
  suite green.
- **Commits:** every commit ends with a `Co-Authored-By:` line for the
  implementing model. Do not push without the user's say-so.
- **Review gates:**
  - **GATE 1** after Task 7: stop for the user's review before any Phase C/D
    work.
  - **GATE 2** after Task 15.

## Review Focus

1. **Same display name on the WiFi.** Two devices both announcing "Bob" (one
   paired, one not): the friend list must show the unpaired one as *new*,
   because trust is keyed by fingerprint, never by name. Test in Task 4 and
   Task 6.
2. **Nobody answers the pairing prompt.** The user walks away, or closes the
   dialog: after the timeout the call is refused and nothing is saved. Test in
   Task 3.
3. **A paired friend reinstalls** and gets a new key under the same name: the
   app warns "key changed" and requires a fresh code check. The old pin is never
   silently replaced. Test in Task 3.
4. **Client-isolated or broadcast-blocked WiFi** (sendto raises `OSError`): the
   list just stays empty, and Call-by-IP still works with no crash or traceback.
   Test in Task 4.
5. **A long call through a 2 s UDP outage:** playout conceals and then returns to
   its target latency, memory stays flat, and hang-up exits every thread. Test in
   Task 8 and Task 13.

---

## Phase B: finish the security foundation, then seamless pairing

### Task 1: Fix the T6 security review findings

The report is saved at `docs/reviews/2026-10-16-t6-security-review.md`. T6 found
no break of authentication or key secrecy. It found six defects and some doc
mismatches.

**Files:**
- Modify: `src/hpqv/demo.py` (`_receiver_loop`, `_load_identity`, `listen`),
  `src/hpqv/call.py` (`recv_stream`), `src/hpqv/udp.py` (`open_udp`),
  `src/hpqv/secure.py` (`_context`, `media_keys`, error paths),
  `src/hpqv/identity.py` (`load`), `src/hpqv/gui.py` (`_accept`),
  `docs/PROTOCOL-v2.md`, `tests/test_demo_identity.py`.
- Test: `tests/test_t6_regressions.py` (new) holds one test per finding.

**Interfaces:**
- `SecureChannel.media_keys()` may be called **once**; a second call raises
  `RuntimeError("media keys already taken")`.
- `identity.load(...)` raises `PermissionError` on POSIX when the key file mode
  has any `0o077` bit set.
- `demo.listen` and the GUI listener keep accepting after a failed handshake,
  until a peer authenticates or the user cancels.

- [ ] **Step 1: Write the failing tests**, one per finding:
  - `test_t6_oversize_datagram_never_ends_call`: a Windows-like fake socket
    whose `recvfrom(n)` raises `OSError(10040)` when the datagram is longer than
    n, fed a 1,300 B datagram. Assert the receiver is still running,
    `stop_event` is not set, and `rejects["oversize"] == 1`. Also cover
    `ConnectionResetError` from `recvfrom`.
  - `test_t6_same_identity_both_sides_refused`: `_load_identity` with the peer
    cert equal to your own cert raises, with the message "this is your own
    certificate…". `establish` with `trusted` containing your own key raises
    `HandshakeError("bad_peer_cert")`.
  - `test_t6_clienthello_offers_only_pq_group_and_mldsa`: a TCP capture of the
    ClientHello shows `supported_groups == [0x11EC]`, one `key_share` entry of
    1,216 B, and `signature_algorithms == [0x0905]`. The post-handshake group
    check stays in place.
  - `test_t6_media_keys_single_use`.
  - `test_t6_world_readable_key_refused` (POSIX only).
  - `test_t6_listener_survives_stray_connection`: a raw TCP connect-and-close,
    then a real caller still connects.
  - `test_t6_ssl_error_in_establish_closes_socket_and_raises_handshakeerror`:
    monkeypatch `bio_write` to raise `SSL.Error`.
  - Fix the useless check in `test_demo_does_not_import_liboqs_or_the_v1_handshake`:
    assert `hpqv.eval.v1_handshake` is absent from `sys.modules`.
- [ ] **Step 2:** Run `pytest tests/test_t6_regressions.py -q`. Expected: every test FAILS.
- [ ] **Step 3:** Apply the fixes:
  - **Receive buffer:** `recvfrom(65535)` in `_receiver_loop` and
    `call.recv_stream`, so `parse_header` rejects oversize datagrams.
    `ConnectionResetError` → `continue`.
  - **Windows ICMP resets:** on Windows, `open_udp` calls
    `sock.ioctl(socket.SIO_UDP_CONNRESET, False)`.
  - **Own identity:** refuse it in `_load_identity` and in `establish`.
  - **Restrict the offer:** in `secure._context`, call
    `SSL._lib.SSL_CTX_set1_curves_list(ctx._context, b"X25519MLKEM768")` and
    `SSL._lib.SSL_CTX_set1_sigalgs_list(ctx._context, b"mldsa65")`. If either
    returns 1, raise. This is a private binding, so the post-handshake check
    stays and a `ponytail:` comment names the fragility.
  - **Single-use media keys.**
  - **Key permissions:** check them in `identity.load`.
  - **Listener:** loop in `listen` and `gui._accept`.
  - **Error paths:** wrap `SSL.Error` from `bio_write` and from the READY send
    as `HandshakeError("tls_error")`, and abort the socket.
- [ ] **Step 4:** Correct the `docs/PROTOCOL-v2.md` mismatches T6 listed:
  - §3: the real offer after the fix.
  - §3: a v2 caller dialling a v1 listener gets `timeout`.
  - §7.1: expired generations are purged before the AEAD, based on time only.
  - §9: the `HandshakeError` wording.
  - §12: the status and the filled rows.

  Also add T6's "for a human cryptographer" list to the doc's "Unverified" section.
- [ ] **Step 5:** Run the full suite. Expected: green, +8 tests.
- [ ] **Step 6:** Append a "Disposition" table to the review file
  (finding → commit), then commit: `fix(v2): address T6 security review findings`.

### Task 2: Trust policy in `secure.establish` and a 48-bit code

**Files:**
- Modify: `src/hpqv/secure.py` (`establish`, `_context`, `local_pair`, `_LABEL_SAS` length).
- Modify: `src/hpqv/demo.py:establish_timed`, and every caller passing `peer_cert_pem`.
- Test: `tests/test_secure.py`, `tests/test_demo_identity.py`.

**Interfaces:**
- Produces:

  ```python
  SecureChannel.establish(sock, role, ident, trusted: Sequence[bytes], *,
                          allow_unknown: bool = False, timeout=HANDSHAKE_TIMEOUT)
  ```

  `trusted` is a list of PEM certs. With `allow_unknown=False` the behaviour is
  exactly today's pinning. With `allow_unknown=True` any depth-0 ML-DSA-65 cert
  is accepted at the TLS layer.
- Produces:
  - `SecureChannel.peer_cert_pem: bytes`;
  - `SecureChannel.peer_known: bool` (the peer's raw key is in `trusted`);
  - `SecureChannel.sas() -> str`, now 12 hex chars grouped `"xxxx xxxx xxxx"`
    (6 exporter bytes).
- Produces: `demo.establish_timed(role, sock, ident, trusted, stats, allow_unknown=False)`.

Why 48 bits: without a commitment step, an active MITM can grind its own
ServerHello to steer one side's code. Each attempt costs it an ML-DSA signature,
and 48 bits puts that out of reach inside the 10 s handshake timeout. Record this
one line in `docs/PROTOCOL-v2.md`.

- [ ] **Step 1: Write the failing tests.**
  - `test_allow_unknown_accepts_unpinned_mldsa_peer`: `peer_known is False`, and
    `peer_cert_pem` equals the peer's cert.
  - `test_default_still_rejects_unpinned_peer`: raises
    `HandshakeError(reason="bad_peer_cert")`.
  - `test_trusted_list_matches_any_member`.
  - `test_allow_unknown_still_rejects_non_mldsa_cert` (Ed25519 → `bad_peer_cert`).
  - `test_allow_unknown_still_enforces_group_and_alpn`.
  - `test_sas_is_48_bits_and_equal_on_both_sides`: matches
    `re.fullmatch(r"[0-9a-f]{4} [0-9a-f]{4} [0-9a-f]{4}")`, and both sides are equal.
- [ ] **Step 2:** Run `pytest tests/test_secure.py -q`. Expected: the new tests FAIL (TypeError on `trusted`).
- [ ] **Step 3:** Implement the signature change. The verify callback accepts a
  depth-0 cert whose ML-DSA-65 raw key is in the trusted set, or any ML-DSA-65
  key when `allow_unknown`. The post-handshake re-check mirrors it. Update every
  caller to pass `[peer_cert_pem]`.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(v2): trust list + allow_unknown in establish; 48-bit SAS`.

### Task 3: `pairing.py`: home identity, known-peers store, confirmation exchange

**Files:**
- Create: `src/hpqv/pairing.py`.
- Modify: `src/hpqv/control.py` (add `MSG_TRUST = 5`, `MSG_PAIR_CONFIRM = 6`).
- Test: `tests/test_pairing.py`.

**Interfaces:**
- Consumes: `identity.new/save/load/fingerprint`; `SecureChannel.send/recv/sas/peer_cert_pem/peer_known` (Task 2).
- Produces:

  ```python
  HOME_ENV = "HPQV_HOME"                      # default ~/.hpqv, dir mode 0o700
  PAIR_TIMEOUT = 60.0                          # seconds a human has to answer

  def home() -> pathlib.Path
  def home_identity(name: str | None = None) -> identity.Identity
      # loads home()/identity.key+.crt; creates them on first use (name defaults to socket.gethostname()[:32])

  @dataclass(frozen=True)
  class Peer:
      name: str; fingerprint: str; cert_pem: bytes; paired_at: str   # ISO-8601 UTC

  class KnownPeersError(ValueError): ...      # unreadable/corrupt store: never overwritten

  class KnownPeers:
      def __init__(self, path: pathlib.Path)  # home()/known_peers.json, mode 0o600
      def certs(self) -> list[bytes]
      def get(self, fingerprint: str) -> Peer | None
      def by_name(self, name: str) -> list[Peer]
      def add(self, name: str, cert_pem: bytes) -> Peer   # atomic: temp file + os.replace
      def remove(self, fingerprint: str) -> None

  class PairingRefused(Exception):
      reason: str   # "declined_local" | "declined_peer" | "timeout" | "peer_closed"

  AskUser = Callable[[str, str, bool], bool]   # (sas, peer_name, key_changed) -> confirmed

  def negotiate(channel, known: KnownPeers, ask_user: AskUser,
                timeout: float = PAIR_TIMEOUT) -> Peer
  ```

- `negotiate` protocol over the TLS channel:
  1. Send `MSG_TRUST` with payload `b"\x01"` if `channel.peer_known`, else `b"\x00"`.
  2. Receive the peer's `MSG_TRUST`.
  3. If both are `0x01`, return the known `Peer`.
  4. Otherwise call `ask_user(channel.sas(), cn, key_changed)`. `cn` is the peer
     cert's CN, an untrusted label. `key_changed` is true when
     `known.by_name(cn)` is non-empty but the fingerprint is not in it.
  5. Send `MSG_PAIR_CONFIRM` with `b"\x01"` or `b"\x00"`, then receive the peer's.
  6. Only if both are `0x01`, call `known.add` and return the Peer. Otherwise
     raise `PairingRefused` and save nothing.
  7. Any other message type, or a bad payload length, is a `PairingRefused("peer_closed")`.

- [ ] **Step 1: Write the failing tests**, using `secure.local_pair`-style
  helpers with `allow_unknown=True` and `HPQV_HOME` set to `tmp_path`.
  - `test_home_identity_created_once_with_private_modes`: dir is `0o700`, key `0o600`; a second call returns the same fingerprint.
  - `test_first_call_both_confirm_saves_peer_on_both_sides`.
  - `test_local_decline_saves_nothing_and_raises_declined_local`.
  - `test_peer_decline_raises_declined_peer`.
  - `test_unanswered_prompt_times_out`: `ask_user` blocks past `timeout=0.5` → `PairingRefused("timeout")`, store unchanged *(Review Focus 2)*.
  - `test_second_call_skips_prompt`: `ask_user` must not be called.
  - `test_same_name_new_key_reports_key_changed_and_keeps_old_pin` *(Review Focus 3)*.
  - `test_one_side_forgot_peer_both_prompted`.
  - `test_corrupt_store_raises_and_is_not_overwritten`.
  - `test_store_never_contains_private_key`: no `"PRIVATE KEY"` in the file.
- [ ] **Step 2:** Run `pytest tests/test_pairing.py -q`. Expected: FAIL (ImportError).
- [ ] **Step 3:** Implement `pairing.py` to the interface. Run `ask_user` in a
  helper thread joined with `timeout`, so a hung prompt cannot block past it.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(v2): pairing — home identity, known peers, code confirmation`.

### Task 4: `discovery.py`: untrusted LAN announcements

**Files:**
- Create: `src/hpqv/discovery.py`.
- Test: `tests/test_discovery.py`.

**Interfaces:**
- Produces:

  ```python
  PORT = 47123; ANNOUNCE_EVERY = 2.0; EXPIRE_AFTER = 6.0; MAX_PEERS = 64; MAX_ANNOUNCE = 512

  @dataclass(frozen=True)
  class Seen:
      name: str; address: str; tcp_port: int; fp_hint: str; last_seen: float
      # fp_hint = first 16 hex chars of the fingerprint, unauthenticated

  def encode(name: str, tcp_port: int, fingerprint: str) -> bytes
      # JSON {"app":"hpqv","v":2,"name":..,"port":..,"fp":..} <= MAX_ANNOUNCE; name truncated to 32
  def decode(data: bytes, address: str, now: float) -> Seen | None
      # None for oversize, bad JSON, wrong app/v, bad port, non-hex fp

  class Discovery:
      def __init__(self, name, tcp_port, fingerprint, *, port=PORT,
                   send_to=("255.255.255.255", PORT), clock=time.monotonic)
      def start(self) -> None          # one daemon thread: announce + listen (select, 0.5 s)
      def stop(self) -> None           # joins within 2 s
      def peers(self) -> list[Seen]    # unexpired, newest first, excludes own fingerprint
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_roundtrip_encode_decode`.
  - `test_decode_rejects_oversize_badjson_wrongapp_badport_badfp`: each returns `None`.
  - `test_entries_expire_after_6s` (fake clock).
  - `test_table_capped_at_64_under_flood`.
  - `test_own_announcement_ignored`.
  - `test_two_instances_see_each_other_on_loopback`: `send_to=("127.0.0.1", p)` with distinct ports.
  - `test_broadcast_oserror_degrades_silently`: monkeypatched `sendto` raises `OSError` → `peers()==[]`, the thread keeps running, no exception escapes *(Review Focus 4)*.
  - `test_same_name_two_fingerprints_listed_separately` *(Review Focus 1)*.
  - `test_announcement_contains_no_key_material`.
- [ ] **Step 2:** Run `pytest tests/test_discovery.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement it. Use `SO_BROADCAST`, `SO_REUSEADDR`, and
  `SO_REUSEPORT` where it exists, and `recvfrom(MAX_ANNOUNCE + 1)`.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(v2): stdlib LAN discovery (untrusted phonebook)`.

### Task 5: CLI: no-files default, pairing, call by name

**Files:**
- Modify: `src/hpqv/demo.py` (`listen`, `call`, `main`, `_add_common_call_args`).
- Test: `tests/test_demo_identity.py`, `tests/test_demo_loop.py`.

**Interfaces:**
- Consumes: Task 2 `establish_timed(..., allow_unknown)`; Task 3
  `home_identity`, `KnownPeers`, `negotiate`; Task 4 `Discovery`.
- Produces the CLI:
  - **`listen [--port N]`** with no identity flags: uses `home_identity()`,
    `KnownPeers(home()/'known_peers.json')`, `allow_unknown=True`, announces via
    `Discovery`, and prompts `Code XXXX XXXX XXXX — does <name> see the same code? [y/N]`
    on stdin.
  - **`call <HOST:PORT | NAME>`**: a NAME is resolved through `Discovery.peers()`
    within 5 s. If more than one matches, it prints them and exits 2.
  - **`peers`**: prints the discovery list for 4 s, marked `paired` or `new`
    (paired means `fp_hint` is a prefix of a known fingerprint, shown as a hint
    only).
  - **`forget <NAME|FINGERPRINT-PREFIX>`**: removes a known peer.
  - **The file flags `--key/--cert/--peer-cert` keep today's behaviour.** They
    are pinned-only, with no pairing and no discovery.
- `listen` must not send `CALL_START` handling until `negotiate` returns.

- [ ] **Step 1: Write the failing tests.** They drive `main()` in threads with `HPQV_HOME` per peer and stdin monkeypatched.
  - `test_cli_first_call_pairs_then_second_call_needs_no_prompt`.
  - `test_cli_decline_ends_both_without_saving`.
  - `test_cli_call_by_name_via_loopback_discovery`.
  - `test_cli_file_flags_still_pinned_only`.
  - `test_cli_forget_removes_peer`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(v2): CLI pairing, call by name, peers/forget`.

### Task 6: GUI: friend list and pairing dialog

**Files:**
- Modify: `src/hpqv/gui_panels.py` (`ConnectionPanel`: replace the three file
  pickers with a friend list), `src/hpqv/gui.py` (`_run`, `_accept`, the pairing
  prompt), `src/hpqv/gui_logic.py`.
- Test: `tests/test_gui_logic.py`, `tests/test_gui_shell.py`.

**Interfaces:**
- Consumes Tasks 3 and 4.
- Produces pure functions in `gui_logic`:

  ```python
  def friend_rows(seen: list[discovery.Seen], known: list[pairing.Peer]) -> list[tuple[str, str, str]]
      # (label, status, call_target); status in {"paired", "new", "key changed?"}; keyed by fingerprint hint
  def pairing_prompt_text(sas: str, peer_name: str, key_changed: bool) -> tuple[str, str]   # (title, body)
  ```

- Produces `DemoWindow._ask_user(sas, name, key_changed) -> bool`. It is called
  on the worker thread, posts the dialog to the Tk thread via `root.after`,
  waits on a `threading.Event` with `pairing.PAIR_TIMEOUT`, and returns `False`
  on timeout or close.
- Only one pending pairing prompt at a time. A second unknown caller is refused
  with `PairingRefused("declined_local")`.
- A "Call by IP" entry stays. The friend list refreshes from `Discovery.peers()`
  in the existing `_poll` loop (no new timer).

- [ ] **Step 1: Write the failing tests.**
  - `test_friend_rows_marks_new_paired_and_same_name_conflict` *(Review Focus 1)*.
  - `test_pairing_prompt_text_includes_code_and_key_changed_warning`.
  - `test_ask_user_returns_false_on_timeout` (fake root with an `after` that never fires).
  - `test_second_pairing_while_one_pending_is_refused`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it. Handshake, pairing and discovery all stay off the Tk thread.
- [ ] **Step 4:** Run the full suite. Expected: green.
  Also: with `HPQV_GUI_TESTS=1`, run `pytest tests/test_gui_window.py -q` from a real terminal. Expected: pass.
- [ ] **Step 5:** Commit: `feat(v2): GUI friend list and code-confirmation dialog`.

### Task 7: Phase B docs, results placeholders, GATE 1

**Files:**
- Modify: `docs/PROTOCOL-v2.md` (fill every `TO BE FILLED AFTER WAVE 2` with
  test file + test count from an actual run; add sections on pairing, discovery
  and the 48-bit code), `docs/adr/0005-protocol-v2-tls13.md` (status stays
  Proposed), `README.md` (v2 quick start: open app → pick friend → read code
  once; v1 marked historical).
- Create: `docs/adr/0006-lan-pairing-tofu-with-code-confirmation.md`.
- Test: `tests/test_docs_current.py` stays green.

- [ ] **Step 1:** Run the full suite and save its tail into the PROTOCOL-v2 "Implemented and tested" table.
- [ ] **Step 2:** Write ADR-0006, covering:
  - context: same-WiFi seamlessness;
  - decision: pin on first use, only after both users confirm the 48-bit code;
  - consequences: one human step per new friend; a first call where the users skip the check is unauthenticated;
  - alternatives: file swap, fingerprint paste.
- [ ] **Step 3:** Commit: `docs(v2): Phase B results, pairing ADR, README quick start`.
- [ ] **Step 4: GATE 1.** Stop. Report to the user:
  - files changed;
  - suite result;
  - a live two-process pairing demo transcript;
  - open risks.

  Wait for approval before Task 8.

---

## Phase C: audio resilience

### Task 8: Bounded jitter buffer and clock-driven playout

**Files:**
- Modify: `src/hpqv/jitter.py`, `src/hpqv/playout.py`, `src/hpqv/controls.py`,
  `src/hpqv/demo.py` (`_receiver_loop` becomes receive-only; a new playout thread
  is started in `_run_call`; `FrameSink._over_backlog` is removed).
- Test: `tests/test_jitter.py`, `tests/test_playout.py`, `tests/test_demo_loop.py`.

**Interfaces:**
- Produces in `jitter.py`:

  ```python
  class JitterBuffer:                        # thread-safe (one lock), bounded
      def __init__(self, capacity: int = 64)
      def push(self, seq: int, item) -> str  # "ok" | "duplicate" | "late" | "overflow"(oldest evicted)
      def take(self, seq: int)               # item or None; frees older slots
      def peek(self, seq: int)               # item or None, no removal
      def occupancy(self) -> int
      def newest(self) -> int | None
  ```

- Produces in `playout.py`:

  ```python
  class PlayoutClock:
      def __init__(self, codec, buffer: JitterBuffer, write: Callable[[bytes], None],
                   controls, stats, clock=time.monotonic)
      def tick(self) -> str      # "wait" | "play" | "fec" | "plc" | "silence" | "underrun" | "skip"
      def run(self, stop_event) -> None   # deadline-scheduled every 20 ms; never busy-waits
      target_frames: int         # current adaptive target depth
  ```

- **Rules for `tick`:**
  - It returns `"wait"` until `occupancy >= target_frames`.
  - **Missing seq N, with N+1 buffered:** use `decode(N+1, decode_fec=True)` if
    `packet_has_lbrr`, else PLC; repair `silence` writes zeros.
  - **Empty buffer after start:** an underrun, played as PLC for up to
    `MAX_GAP`, then silence.
  - **Latency cap:** `occupancy > target_frames + 2` drops the oldest frame
    (`"skip"`).
  - **Target depth:**
    `target = clamp(ceil((2*rx_jitter_ms + 10)/20), ceil(min_latency_ms/20), floor(max_latency_ms/20))`.
    It moves at most ±1 frame per second, and only when the new value differs
    for 3 consecutive seconds.
  - The fixed `jitter_depth` is used when `adapt_depth` is False.
- **New `controls` defaults:** `"adapt_depth": True`, `"min_latency_ms": 20.0`
  (range 20–1000), `"max_latency_ms": 200.0` (range 20–2000), and
  `"adapt_encoder": False` (consumed by Task 9).
- **New `Stats` fields:** `underruns`, `jb_occupancy`, `jb_target`. The existing
  `recovered`, `concealed`, `late`, `skipped` and `repaired_sequences` keep their
  meaning.

- [ ] **Step 1: Write the failing tests**, with a fake codec that records calls and a fake clock.
  - `test_push_bounded_capacity_evicts_oldest`.
  - `test_duplicate_and_late_reported`.
  - `test_missing_frame_recovered_from_next_packet_lbrr`: `decode_fec=True` is called with packet N+1.
  - `test_missing_frame_without_lbrr_is_plc`.
  - `test_burst_of_three_two_plc_one_fec`.
  - `test_underrun_counts_and_plc_then_silence_after_max_gap`.
  - `test_latency_capped_at_target_plus_two`.
  - `test_target_adapts_with_hysteresis_and_respects_limits`.
  - `test_outage_2s_then_returns_to_target` *(Review Focus 5)*.
  - `test_tick_never_blocks`: `tick()` with an empty buffer returns in < 2 ms.
  - Plus the existing `test_demo_loop` WAV call: output length is within ±2 frames of the input.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it. `_receiver_loop` only authenticates, meters and
  pushes. The playout thread owns the decoder.
- [ ] **Step 4:** Run the full suite, including `tests/eval/test_live_scenarios.py`. Expected: green.
- [ ] **Step 5:** Commit: `feat(audio): bounded jitter buffer and clock-driven playout`.

### Task 9: Receiver reports and the adaptive Opus controller

**Files:**
- Create: `src/hpqv/adapt.py`.
- Modify: `src/hpqv/control.py` (`MSG_RECEIVER_REPORT = 9`), `src/hpqv/callctl.py`
  (send a report every 1 s; route received reports), `src/hpqv/demo.py`
  (`RxMeter` loss-run tracking; `_sender_loop` applies the controller's settings
  through `audio._encoder_set` with CTLs 4012 / 4014 / 4002).
- Test: `tests/test_adapt.py`, `tests/test_callctl.py`.

**Interfaces:**
- Produces:

  ```python
  REPORT = struct.Struct(">HHHI")   # loss_permille, burst_mean_x10, jitter_ms_x10, expected
  def pack_report(r: Report) -> bytes; def unpack_report(b: bytes) -> Report   # ValueError on bad length

  @dataclass(frozen=True)
  class Report: loss_pct: float; burst_mean: float; jitter_ms: float; expected: int
  @dataclass(frozen=True)
  class EncoderSettings: fec: bool; loss_perc: int; bitrate: int

  class AdaptiveController:
      LADDER = (12000, 16000, 24000, 32000)
      def __init__(self, alpha: float = 0.3, start: EncoderSettings = EncoderSettings(True, 10, 24000))
      def update(self, r: Report) -> EncoderSettings
  ```

- **Controller rules** (the plan's calls; record them in the docs):
  - Loss is smoothed: `L = EWMA(alpha)` of `loss_pct`. Reports with `expected == 0` are ignored.
  - **FEC switching:** on when `L >= 1.0`; off only after 5 consecutive reports with `L < 0.5`.
  - **`loss_perc`:** `clamp(ceil(L), 0, 30)`, changed only when it differs from
    the current value by ≥ 2. It is capped at 20 when `burst_mean >= 3`, because
    in-band FEC repairs only single losses.
  - **Bitrate:** one ladder rung down after 3 consecutive reports with `L >= 10`;
    one rung up after 10 consecutive reports with `L < 2`.
- **Wiring:**
  - `adapt_encoder=False` means reports are logged and counted but settings never change.
  - The manual knobs `fec` and `bitrate` apply only when adaptation is off.
- **New `Stats` fields:** `burst_mean`, `enc_fec`, `enc_loss_perc`, `enc_bitrate`.
- **Opus binding note:** opuslib has no setters for INBAND_FEC (4012),
  PACKET_LOSS_PERC (4014) or BITRATE (4002), so the existing ctypes shim
  `audio._encoder_set` is used. Each setting is read back with
  `audio._encoder_get` after it is applied; a mismatch raises. Record this
  limitation in `docs/PROTOCOL-v2.md` (requirements Feature 3, item 12).

- [ ] **Step 1: Write the failing tests.**
  - `test_settings_read_back_from_encoder` (real `OpusCodec`: 4013/4015/4003 GETs match what was set).
  - `test_report_roundtrip_and_bad_length`.
  - `test_fec_turns_on_at_1pct_and_off_only_after_5_low_reports`.
  - `test_no_oscillation_on_noisy_loss_around_threshold`: alternating 0.4 / 1.2 % for 50 reports gives ≤ 2 FEC flips.
  - `test_bitrate_steps_down_after_3_high_and_up_after_10_low`.
  - `test_loss_perc_capped_at_20_for_bursty_loss`.
  - `test_adapt_off_ignores_reports`.
  - `test_reports_flow_over_tls_and_change_sender_settings` (callctl integration with `local_pair`).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(audio): receiver reports and adaptive FEC/bitrate with hysteresis`.

---

## Phase D: observability and simulation

### Task 10: Metrics: RTT, CPU/memory, crypto cost, reset, JSON export

**Files:**
- Modify: `src/hpqv/control.py` (`MSG_PING = 7`, `MSG_PONG = 8`), `src/hpqv/callctl.py`
  (ping every 1 s with a u64 nonce; echo pongs), `src/hpqv/keyring.py`
  (`crypto_ns` accumulator around seal/open), `src/hpqv/demo.py` (`Stats` fields
  and `reset_counters`), `src/hpqv/telemetry.py` (rates, `reset(t)`, `summary.json`).
- Test: `tests/test_telemetry.py`, `tests/test_callctl.py`, `tests/test_keyring.py`.

**Interfaces:**
- **New `Stats` fields:**
  - `rtt_ms` (EWMA α=0.25 over pong round trips, local monotonic clock, measured);
  - `rtt_samples`;
  - `cpu_pct` (`time.process_time` delta ÷ wall delta × 100, whole process, measured);
  - `peak_rss_mb` (`resource.getrusage(RUSAGE_SELF).ru_maxrss`, peak not current,
    `None` on Windows);
  - `crypto_us_per_pkt` (from `Keyring.crypto_ns`).
- `Stats.reset_counters() -> None` zeroes the counters and keeps the session
  facts: group, cipher, fingerprints, handshake numbers, key generation.
- `Telemetry.reset(t: float) -> None`.
- `Telemetry.export(out_dir) -> list[str]` now also writes `summary.json`, a
  mapping `metric -> {"value": ..., "kind": "measured" | "estimate" | "not_measured"}`.
  One-way latency is always `"not_measured"`, with reason `"peers' clocks are not synchronised"`.
  The E-model MOS is `"estimate"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_rtt_measured_over_local_pair_is_positive_and_small`.
  - `test_reset_counters_keeps_session_facts`.
  - `test_summary_json_kinds_and_no_secrets`: no SAS, fingerprint, key, or 32/64-hex runs.
  - `test_one_way_latency_marked_not_measured`.
  - `test_rates_from_counter_deltas` (synthetic Stats).
  - `test_crypto_cost_accumulates`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(metrics): RTT, CPU/mem, crypto cost, reset, JSON summary`.

### Task 11: GUI Diagnostics tab

**Files:**
- Modify: `src/hpqv/gui.py` (a fourth notebook tab "Diagnostics", plus a Reset
  button wired to `Stats.reset_counters` + `Telemetry.reset`), `src/hpqv/gui_panels.py`
  (`DiagnosticsPanel`), `src/hpqv/gui_logic.py`.
- Test: `tests/test_gui_logic.py`.

**Interfaces:**
- Produces:

  ```python
  def diagnostic_rows(stats: dict, row: dict | None) -> list[tuple[str, str, str, str]]
      # (section, label, value_text, kind)
  ```

  - `section` is one of `"Security"`, `"Network"` or `"Audio"`; `kind` is one of
    `"measured"`, `"estimate"` or `"not measured"`.
  - The rows cover every Feature 4 metric the requirements list, including the
    rekey generation and "hybrid PQC established: yes/no", which comes from
    `group == "X25519MLKEM768"`.
- `DiagnosticsPanel.update(rows)` is called from the existing `_refresh`. No new timers.

- [ ] **Step 1: Write the failing tests.**
  - `test_diagnostic_rows_cover_every_required_metric` (assert the label set).
  - `test_kinds_labelled`.
  - `test_hybrid_flag_false_for_other_group`.
  - `test_no_secret_in_rows`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite. Expected: green. Also run `HPQV_GUI_TESTS=1 pytest tests/test_gui_window.py -q` from a terminal. Expected: pass.
- [ ] **Step 5:** Commit: `feat(gui): diagnostics tab with measured/estimate labels and reset`.

### Task 12: Seeded impairment proxy and profiles

**Files:**
- Create: `src/hpqv/eval/udp_proxy.py`, `src/hpqv/eval/profiles.py`.
- Modify: `src/hpqv/impair.py` (add `DupModel`, `TokenBucket`).
- Test: `tests/eval/test_udp_proxy.py`, `tests/test_impair.py`.

**Interfaces:**
- Produces:

  ```python
  @dataclass(frozen=True)
  class Profile:
      name: str; loss_pct: float = 0; loss_model: str = "random"; burst_len: float = 3
      delay_ms: float = 0; jitter_ms: float = 0; dup_pct: float = 0; reorder_pct: float = 0
      bandwidth_kbps: float = 0          # 0 = unlimited
      outages: tuple[tuple[float, float], ...] = ()   # (start_s, duration_s)

  PROFILES: dict[str, Profile]   # "clean","loss1","loss5","loss10","loss20","burst10"(GE 10 %, burst 3),
                                 # "high_latency_jitter"(150 ms ±40),"outage"(2 s at t=3 s)

  class ImpairmentProxy:         # binds 127.0.0.1 only; one thread per direction; bounded queues (256)
      def __init__(self, a_addr, b_addr, profile: Profile, seed: int)
      def start(self) -> tuple[tuple[str,int], tuple[str,int]]   # (addr A should send to, addr B should send to)
      def stop(self) -> None
      trace: list[tuple[int, str]]   # (datagram index, action) action in {"fwd","drop","dup","reorder","late"}
  ```

- [ ] **Step 1: Write the failing tests.**
  - `test_same_seed_same_trace`.
  - `test_different_seed_different_trace`.
  - `test_loss_rate_within_tolerance` (10,000 datagrams, ±1 pt).
  - `test_duplication_and_reordering_rates`.
  - `test_bandwidth_cap_bounds_throughput`.
  - `test_outage_drops_everything_in_window`.
  - `test_proxy_binds_loopback_only`.
  - `test_queues_bounded_under_flood`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it, reusing `impair.LossModel` and `impair.DelayLine`.
- [ ] **Step 4:** Run the full suite. Expected: green.
- [ ] **Step 5:** Commit: `feat(eval): seeded loopback impairment proxy and named profiles`.

### Task 13: Resilience benchmark and soak tests

**Files:**
- Create: `src/hpqv/eval/resilience.py`, `scripts/run_resilience.py`, `tests/eval/test_resilience.py`, `tests/eval/test_soak.py`.
- Modify: `src/hpqv/eval/live_scenarios.py` (`_run_loopback` accepts an optional `ImpairmentProxy`).

**Interfaces:**
- Produces:

  ```python
  MODES = {"fec_off": {"fec": False, "repair": "plc", "adapt_encoder": False},
           "fec_on":  {"fec": True,  "repair": "fec", "adapt_encoder": False},
           "adaptive":{"fec": True,  "repair": "fec", "adapt_encoder": True}}
  def run_case(profile: str, mode: str, seed: int, seconds: float) -> dict
      # keys: profile, mode, seed, seconds, sent, delivered, residual_loss_pct, recovered, concealed,
      #       late, underruns, mean_bitrate_bps, latency_estimate_ms, snr_db, repaired_snr_db, mos_estimate
  ```

- CLI:
  `scripts/run_resilience.py --seconds 10 --seeds 0 1 2 --profiles all --modes all --out results/v2`
  writes `resilience.csv` and `resilience.json`. It refuses any `--out` that
  resolves to `results/` itself, since v1 results stay untouched.
- The input is synthetic `live_scenarios.make_speech_wav` audio only. Never record a microphone.

- [ ] **Step 1: Write the failing tests.**
  - `test_run_case_keys_and_types` (`clean`, 2 s).
  - `test_same_seed_reproducible_counts`.
  - `test_modes_compared_under_identical_proxy_trace`.
  - `test_refuses_v1_results_dir`.
  - Soak test `test_outage_soak_memory_and_threads_bounded`: profile `outage`, 20 s.
    tracemalloc growth must be < 5 MB, `jb_occupancy <= 64`, and every thread
    joins within 5 s of hang-up *(Review Focus 5)*.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite. Then actually run `python scripts/run_resilience.py --seconds 10 --seeds 0 1 2 --out results/v2` and keep its output files.
- [ ] **Step 5:** Commit the code and `results/v2/resilience.*`: `feat(eval): FEC off/on/adaptive resilience benchmark + soak tests`.

### Task 14: Handshake benchmark, hybrid vs isolated classical baseline

**Files:**
- Create: `src/hpqv/eval/classical_baseline.py`, `scripts/run_handshake_bench.py`, `tests/eval/test_handshake_bench.py`.

**Interfaces:**
- Produces: `classical_baseline.run(n: int) -> list[dict]`.
  - It runs in a **child process** started with `OPENSSL_CONF` pointing to a
    temp config with `Groups = x25519`, and uses Ed25519 self-signed certs.
  - Each row is `{"mode": "classical-BENCHMARK-ONLY", "handshake_ms", "handshake_bytes", "cpu_ms", "group"}`.
- Produces: `scripts/run_handshake_bench.py --n 50 --out results/v2`, which
  writes `handshake_bench.csv` and `.json` with `mode` in
  {`hybrid`, `classical-BENCHMARK-ONLY`}. The hybrid rows come from `secure.local_pair`.

- [ ] **Step 1: Write the failing tests.**
  - `test_classical_rows_report_x25519_group`.
  - `test_production_establish_rejects_classical_peer_wrong_group` (production `secure` against the baseline server → `HandshakeError("wrong_group")`).
  - `test_no_production_module_imports_classical_baseline`.
  - `test_bench_writes_csv_and_json_with_both_modes` (n=3).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run the full suite, then `python scripts/run_handshake_bench.py --n 50 --out results/v2`.
- [ ] **Step 5:** Commit the code and `results/v2/handshake_bench.*`: `feat(eval): hybrid vs isolated classical handshake benchmark`.

---

## Phase E: validation and documentation

### Task 15: Packaging smoke test, docs, final validation, GATE 2

**Files:**
- Modify: `README.md`, `docs/DEMO-RUNBOOK.md`, `docs/DESKTOP-DEMO.md`,
  `docs/PROTOCOL-v2.md` (threat model plus a final implemented/assumed/unverified
  table), `docs/EVALUATION-REPORT.md` (a new "v2" section citing only
  `results/v2/*`).
- Create: `docs/V2-IMPLEMENTATION-REPORT.md`.

- [ ] **Step 1:** On macOS, run `python packaging/build.py`, then `python scripts/smoke_package.py`.
  Expected: the frozen self-check passes. If either can't run here, record the
  exact error; do not claim success.
- [ ] **Step 2:** Run the full suite and the `HPQV_GUI_TESTS=1` window tests. Record the exact tails.
- [ ] **Step 3:** Update the docs. Every number must carry a link to the
  `results/v2` file or test that produced it. Add the reproduction commands from
  Tasks 13 and 14.
- [ ] **Step 4:** Write `V2-IMPLEMENTATION-REPORT.md`, with:
  - per phase: files changed, tests run with results, known limitations, remaining work;
  - an explicit list of what has not been externally reviewed.
- [ ] **Step 5:** Commit: `docs(v2): final report, runbook, evaluation v2 section`.
- [ ] **Step 6: GATE 2.** Stop and present the report to the user. Pushing the branch, or opening a PR, is the user's call.
