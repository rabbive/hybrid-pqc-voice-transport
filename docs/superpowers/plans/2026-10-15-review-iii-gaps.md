# Plan — close the two Review III caveats that weaken headline claims

Spec: no separate spec. Authority is the "Remaining work" list in
`docs/REVIEW-2-TO-REVIEW-3-CHANGES.md` §5 and `docs/REVIEW-III-STUDY-GUIDE.md` §11.
Branch: `feat/review-iii-gaps` (cut from `feat/desktop-demo-packages`, PR #26).

## Global constraints
- Python 3.11, existing deps only. No new packages.
- Match surrounding code: short modules, terse docstrings that explain *why*.
- Tests: pytest. macOS run:
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src <repo>/.venv/bin/python -m pytest -q`
  (baseline: 40 passed, 15 skipped). Linux-only tests skip via
  `pytest.mark.skipif(shutil.which("tc") is None, ...)`, never fail.
- Do NOT edit the Review III snapshot docs (`docs/REVIEW-*`, `presentation/`).
- `tests/test_docs_current.py` guards README/report claims — keep it green.
- TDD: write the failing test, watch it fail, implement, watch it pass.

## Task 2 — Full authenticated handshake under loss, common deadline
Why: the 99% vs 19% result sends a *synthetic* 10,842-byte blob, with different
timeouts for UDP (1 s) and TCP (20 s). Panel caveat: "not a real call, unequal
deadlines". This runs the real ML-KEM/ML-DSA handshake under one deadline.

Produces `src/hpqv/eval/full_handshake.py`:
- `run_tcp(ids, deadline_s) -> (ok: bool, elapsed_ms: float)` — responder thread +
  initiator over loopback TCP using `control.run_initiator` / `control.run_responder`.
- `run_udp(ids, deadline_s, retry: bool, rto_s=0.25) -> (ok, elapsed_ms)` — HELLO as
  ONE UDP datagram, ACCEPT as ONE UDP datagram back (no app framing → IP fragments
  at MTU 1500). `retry=True`: initiator resends HELLO every `rto_s` until ACCEPT or
  deadline; responder caches its ACCEPT per HELLO and resends the cached one on a
  duplicate (a fresh `accept_hello` would re-encapsulate and break key agreement).
- `ok` only if the initiator finishes before the deadline AND both peers hold the
  same key with mirrored prefixes (initiator send == responder recv).
- `ids` = two identities from `handshake.make_identity()`, generated once per
  `measure` call so keygen is excluded from timing.
- `measure(transport, attempts, deadline_s) -> dict` with keys
  `name, attempts, successes, success_pct, median_ms, deadline_s, hello_bytes,
  accept_bytes, fragments`. Transports: `"tcp"`, `"udp-naive"`, `"udp-retry"`.
  `fragments` = `handshake_survival.expected_fragments(hello) + (accept)` for UDP, 0 for TCP.

Produces `scripts/run_full_handshake.py` mirroring `scripts/run_handshake_survival.py`:
losses 0/5/10/20/30, one common deadline (default 3.0 s, `--deadline`), 100 attempts
(`--quick` → 5), `loopback_mtu()` around the sweep, `netem(loss_pct, dev="lo")`
per loss level, writes `results/full_handshake.csv` and `results/full_handshake.png`
via `graphs.plot_loss_vs_success`. Add `predicted_pct` = `(1-p)^fragments` for UDP-naive.

Produces `docs/EXPERIMENT-full-handshake.md`: method, what changed vs the synthetic
experiment, caveats (loopback, netem i.i.d. loss, single deadline), results table
placeholder to be filled by the container run, reproduction command.

Tests `tests/eval/test_full_handshake.py`:
- macOS-runnable (no netem): each transport succeeds 3/3 on clean loopback and keys
  agree; responder resends the identical cached ACCEPT for a duplicate HELLO;
  a deadline shorter than possible (e.g. 1e-4 s) yields `ok=False`, no hang.
- container-only (skip without `tc`): at 20% loss with `loopback_mtu()`,
  tcp success_pct > udp-naive success_pct + 30.

Expected: new macOS tests pass; full suite = baseline + new tests, no failures.
Commit: `feat(eval): measure full authenticated handshake under a common deadline`.

## Task 3 — Live FEC recovery in the receive path
Why: the encoder enables Opus in-band FEC, but `demo._receiver_loop` only calls
normal decode. Missing frames are silently skipped, so FEC never helps a live call.

Produces `src/hpqv/playout.py`:
- `class Playout(codec)` with `frames(seq, opus) -> list[bytes]` returning PCM frames
  to play in order. On a gap (`seq > next_seq`): frame `seq-1` is recovered with
  `codec.decode(opus, decode_fec=True)`; earlier missing frames use Opus packet-loss
  concealment (decoder called with no data; fall back to a zero frame only if opuslib
  cannot do PLC). Then normal decode of `seq`. Counters `recovered`, `concealed`.
- Gaps longer than `MAX_GAP` frames (50 = 1 s) are not synthesized: reset `next_seq`
  and decode normally (a stall should not burst a second of fake audio).
- First packet sets `next_seq`; no gap synthesis before it.

Changes:
- `JitterBuffer.pop()` / `flush()` return `(seq, frame)`; update all callers
  (`demo.py`, `tests/test_jitter.py`).
- `demo._receiver_loop` routes every popped frame through `Playout`; flush too.
- `Stats` gains `recovered` and `concealed`; `snapshot()` callers in `demo.py`,
  `gui.py`, `tests/test_loss_control.py` updated. CLI status line and end-of-call
  line and the GUI counters show `recovered`.
- README limitation text / `docs/EXPERIMENT-fec-recovery.md`: state live recovery now
  exists (keep test_docs_current green).

Tests `tests/test_playout.py`:
- encode N chirp frames with FEC on; drop chosen seqs; feed survivors in order;
  output frame count == frames from first to last received seq (timing continuity);
  `recovered` == single-gap losses whose successor arrived; `concealed` counts the rest.
- a recovered frame's SNR vs the undamaged decode beats a zero frame.
- a gap > MAX_GAP produces no synthesized frames.
- `_receiver_loop` end-to-end with a fake UDP socket (or real loopback socket) and a
  list sink: a dropped datagram yields `stats.recovered == 1`.

Expected: new tests pass; full suite = baseline + new tests, no failures.
Commit: `feat: recover lost voice frames with Opus in-band FEC in the live receiver`.

## Task 4 — Run the container sweep (after merge of 2+3)
Needs Docker running. `docker build -t hpqv-eval ./eval`, then
`docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval bash -c "... python scripts/run_full_handshake.py"`
and the container test suite. Fill the results table in
`docs/EXPERIMENT-full-handshake.md`. Commit results.

## Review focus
- Task 2: deadline enforcement on every blocking call (no thread outlives an attempt;
  sockets closed on all paths); duplicate-HELLO idempotence; fair comparison wording.
- Task 3: thread safety of new Stats fields; seq wraparound irrelevant (64-bit);
  duplicate/late packets after a gap; Playout state on reset.
