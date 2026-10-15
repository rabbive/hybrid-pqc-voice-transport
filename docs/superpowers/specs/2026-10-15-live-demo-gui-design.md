# Live demo GUI — design and build contract

Goal: an extensive live demo (final review, 10+ min) and a lab tool. Every knob
changes the running call; every effect is visible on screen and exportable as
citable CSV + charts. User approved all 14 features on 2026-10-15.

## Shared seam (already on `feat/review-iii-gaps`)
- `hpqv.controls.CallControls` — thread-safe knob set: `get(name)`, `set(**kw)`
  (clamps ranges, validates choices, raises KeyError on unknown names), `snapshot()`.
  Knobs: `loss_pct, loss_model ("random"|"burst"), burst_len, delay_ms, jitter_ms,
  incoming_loss_pct, fec, repair ("silence"|"plc"|"fec"), bitrate (0=auto),
  jitter_depth`. Worker threads read per frame; nothing else stores knob values.
- `demo.Stats` fields: `sent, received, dropped, recovered, concealed, late,
  incoming_dropped, max_datagram, key_gen, handshake_ms, handshake_bytes,
  tx_level, rx_level`; `as_dict()` snapshot under the lock.
- `demo._run_call(sock_tcp, udp_sock, dest, session, args, stats=, controls=, stop_event=)`;
  `_sender_loop(..., controls, stats, stop_event)`; `_receiver_loop(udp_sock, session, sink, controls, stats, stop_event)`.

## Work units (parallel, each in its own worktree)

### W1 — Media path (sender/receiver loops)
Owns `src/hpqv/impair.py` (new), `_sender_loop`, `_receiver_loop`, `playout.py`, `audio.py`.
- Loss models: random (existing) and Gilbert-Elliott burst. From target average
  loss L and mean burst length B: r = 1/B, p = L·r/(1−L); lose every packet in bad state.
  Seedable RNG. A `LossModel` whose `drop(loss_pct, model, burst_len) -> bool` keeps state.
- Delay/jitter: outgoing datagrams are scheduled at now + delay ± uniform(jitter)
  on a `DelayLine` (heap + one sender thread), so reordering happens naturally.
  Zero delay and jitter = send inline (no added latency, no thread churn).
- Receiver-side loss: `incoming_loss_pct` drops datagrams after decrypt, before the
  jitter buffer; count `incoming_dropped`.
- Live jitter depth: receiver applies `jitter_depth` changes to the running buffer.
  Count `late` = packets discarded because their seq was already played/repaired.
- Live FEC toggle and bitrate: sender applies encoder CTLs (`audio._encoder_set`)
  in the sender thread only when the knob changes (FEC → INBAND_FEC 0/1 and
  PACKET_LOSS_PERC 10/0; bitrate → OPUS_SET_BITRATE 4002, 0 → OPUS_AUTO −1000).
- Repair mode in `Playout`: `repair` knob read per gap: "silence" = zero frames,
  "plc" = conceal() for every missing frame, "fec" = current behaviour.
  `recovered` counts only FEC-rebuilt frames; `concealed` counts PLC and silence fills.
- `max_datagram` = largest sealed datagram length sent.

### W2 — Live key rotation and call end (control channel)
Owns `src/hpqv/keyring.py`, `src/hpqv/callctl.py` (new), `packet.py` flags use,
the `packet.seal`/`open_` call sites in the loops (coordinate: only those lines),
and the TCP control channel during a call.
- Packet header `flags` bit 0 = key generation parity. Receiver keeps current and
  previous generation keys and picks by parity, so in-flight packets across a
  rotation still decrypt. Nonce stays prefix ‖ seq; each generation has its own key
  and prefixes via `session.rotate`, so (key, nonce) never repeats.
- `MSG_KEY_ROTATE` payload = u32 new generation. A peer rotates only when
  generation == current + 1, so simultaneous presses rotate exactly once.
  Both directions rotate together; sender switches its send key immediately.
- `MSG_CALL_END` on hang-up; the peer's call ends cleanly (stop_event set).
- A control-reader thread runs during the call; TCP close = call end.
- GUI-facing API: `callctl.CallHandle` created by the caller of `_run_call`
  (passed as `call_handle=`): `rotate_keys()`, `hang_up()`, `key_generation()`,
  `key_fingerprint() -> str` (first 8 hex of SHA-256 of the current key).
  `stats.key_gen` mirrors the generation.

### W3 — Telemetry, handshake metrics, recording, export
Owns `src/hpqv/telemetry.py` (new), `demo.listen`/`demo.call` handshake timing,
`FrameSink` (tee to speaker + WAV), `packaging/requirements.txt`.
- Handshake: time `run_initiator`/`run_responder`; set `stats.handshake_ms`,
  `stats.handshake_bytes` (HELLO + ACCEPT bytes actually sent/received).
  Expose a `demo.handshake_info()` dict for the GUI: algorithms, byte counts,
  transport "TCP", UDP fragments 0, datagram cap 1200.
- `Telemetry(stats, controls)`: `sample(t)` appends one row per call (GUI calls it
  ~1×/s, `dt_s` records the real spacing): time, all counters, and — measured
  only by the local receiver on the PEER's stream — per-interval loss % (seq span
  expected vs delivered), residual loss % after repair, RFC 3550 jitter, added
  one-way delay relative to the lowest transit seen this call, and live MOS via
  `eval.mos.mos_from_network(20·jitter_depth + added_delay, 0, residual_loss)`
  (jitter term 0: the real buffer is already in delay). Local knob values are
  recorded as `knob_*` for context; sender knobs affect what the peer hears.
  `events` list records knob changes and key rotations with timestamps.
- `export(out_dir) -> list[paths]`: `timeline.csv`, `summary.md` (settings,
  totals, mean/min MOS per settings segment, recovered/lost ratio), and PNGs:
  loss vs recovered over time with knob-change markers, MOS over time,
  datagram size vs the 1200 B cap. matplotlib imported lazily; add it to
  packaging requirements.
- Recording: `FrameSink` can tee to a WAV while also playing to the speaker.

### W4 — GUI (after W1–W3 merge)
Panels: connection; handshake (W3 info); repair mode radio + FEC checkbox;
loss (outgoing slider, model radio, burst length), delay, jitter, incoming loss;
jitter depth; bitrate; presets (Clean, Bad Wi-Fi, Burst loss, Congested);
WAV input picker (mic vs file); record-to-WAV; rotate keys + fingerprint;
live counters + MOS; scrolling Canvas graph (loss, recovered, MOS, last 60 s);
export session (W3). Split gui.py into small panel modules if it grows past ~400 lines.

### W5 — Citable scenarios (after W1–W3 merge, parallel with W4)
`scripts/run_live_scenarios.py`: headless loopback calls through the real
pipeline with WAV input over scripted scenarios (clean; random 10/20% with
repair silence/plc/fec; burst 10% B=3 with fec; delay 100 ± 30 ms with jitter
depth 2 vs 6); writes `results/live_scenarios.csv` + PNG charts and a
`docs/EXPERIMENT-live-scenarios.md` with the table, method and caveats.

## Rules for every unit
TDD; existing tests stay green; macOS test command
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib PYTHONPATH=src <repo>/.venv/bin/python -m pytest -q`;
no new runtime deps except matplotlib in packaging; don't edit `docs/REVIEW-*`
or `presentation/`; terse docstrings that explain why.
