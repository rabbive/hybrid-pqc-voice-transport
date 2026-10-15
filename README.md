# hpqv — A Hybrid TCP/UDP Transport for Fragmentation-Free Post-Quantum Voice

A working implementation and evaluation of a voice transport that keeps a
post-quantum handshake **off** the datagram path, so it never fragments.

**Team:** Cain Manoj (23BCE1663) · Sreenandu G (23BCE1120) · Ashwanth Kumaravel (23BCE1244)
**Guide:** Dr. Renuka Devi S.

## The problem

Post-quantum keys are big. A single handshake here carries **10,842 bytes** of
ML-KEM-768 and ML-DSA-65 material — over **7× the 1500-byte Internet MTU**. Sent
naively over UDP it is split into **8 IP fragments**, and losing any one of them
destroys the entire datagram.

(10,842 B is the key and signature material; each message also carries a 1-byte
version, so the wire payload is 10,844 B — HELLO 6,446 B + ACCEPT 4,398 B — which is
what the GUI handshake panel and `results/full_handshake.csv` report.)

## The design

| Channel | Transport | Carries |
|---|---|---|
| **Control** | TCP | the PQC handshake (mutually authenticated), call setup, live key rotation, call end |
| **Data** | UDP | Opus voice, every datagram ≤ 1200 B with the Don't-Fragment bit set |

Nothing ever fragments. Voice keeps UDP's latency; the oversized handshake gets
TCP's reliability.

## Headline results

Measured on the real implementation under `tc netem` impairment — not simulated.

**Zero fragmentation, at every loss level.**

| packet loss | our hybrid | naive PQC-over-UDP |
|---|---|---|
| 0–30 % | **0 fragments** | 4–5 fragments |

Source: [`results/eval.csv`](results/eval.csv).

**Does the call connect?** The real ML-KEM-768 + ML-DSA-65 authenticated handshake
(signatures verified, key agreed on both sides), every transport held to **one
common 3 s deadline**, 100 attempts per cell:

| packet loss | TCP (hybrid control channel) | naive UDP (one-shot) | `(1−p)^8` | UDP with HELLO retry |
|---:|---:|---:|---:|---:|
| 0 % | 100 % | 100 % | 100 % | 100 % |
| 5 % | 100 % | 72 % | 66 % | 100 % |
| 10 % | 99 % | 44 % | 43 % | 100 % |
| 20 % | **84 %** | **18 %** | 17 % | 86 % |
| 30 % | **56 %** | **6 %** | 6 % | 52 % |

Source: [`results/full_handshake.csv`](results/full_handshake.csv), method and
caveats in [docs/EXPERIMENT-full-handshake.md](docs/EXPERIMENT-full-handshake.md).

What this supports, and what it does not:

- A one-shot UDP handshake fails the way fragment arithmetic predicts: naive UDP
  tracks `(1−p)^8` at every loss level. This is the mechanism behind the design.
- Over TCP the same handshake completes 84 % of the time at 20 % loss and 56 % at
  30 %, against 18 % and 6 % for naive UDP.
- **A UDP design that retransmits its HELLO performs about as well as TCP** (86 %
  vs 84 % at 20 %; 52 % vs 56 % at 30 %). The gain over naive UDP comes from
  *retransmission*, not from TCP specifically. UDP-with-retry still sends 8 IP
  fragments per attempt and depends on them reaching the peer, which loopback
  does not challenge; the hybrid sends none. We do not claim TCP beats every
  reliable-UDP design.
- These figures are lower than our earlier synthetic experiment (below) because
  that one used a 20 s TCP timeout and a random payload. Quote these for "does a
  real call set up".

*Earlier experiment (synthetic payload, not the full handshake).* Delivering a random
10,842-byte blob, naive UDP with a 1 s timeout and no retry vs TCP with a 20 s
timeout: 19 % vs 99 % at 20 % loss, 2 % vs 83 % at 30 %
([`results/handshake_survival.csv`](results/handshake_survival.csv),
[docs/EXPERIMENT-handshake-survival.md](docs/EXPERIMENT-handshake-survival.md)).
It does not run the authenticated handshake and gives the two transports unequal
deadlines, so the table above supersedes it as the headline.

**Voice repair.** In the offline recovery experiment, Opus in-band FEC leads
no-FEC reconstruction by 24–30 dB segmental SNR at every lossy level (3 s
synthetic chirp, not speech, not a listening test —
[docs/EXPERIMENT-fec-recovery.md](docs/EXPERIMENT-fec-recovery.md)). The live
receiver now applies the same recovery.

Full numbers, method and honest limitations: **[docs/EVALUATION-REPORT.md](docs/EVALUATION-REPORT.md)**.

## Download the desktop demo

**[HPQV v0.1.0 Demo 1](https://github.com/rabbive/hybrid-pqc-voice-transport/releases/tag/v0.1.0-demo.1)** — portable apps with GUI and CLI; no separate Python installation needed.

- [Windows x64 ZIP](https://github.com/rabbive/hybrid-pqc-voice-transport/releases/download/v0.1.0-demo.1/HPQV-windows-x64.zip)
- [Apple Silicon Mac ZIP](https://github.com/rabbive/hybrid-pqc-voice-transport/releases/download/v0.1.0-demo.1/HPQV-macos-arm64.zip)
- [Setup and troubleshooting guide](https://github.com/rabbive/hybrid-pqc-voice-transport/releases/download/v0.1.0-demo.1/START-HERE.md)

Extract the entire ZIP and open HPQV. Use two computers on the same LAN, create one identity file, privately copy it to the other computer, then choose Listen on one and Call its local IP on the other. Development builds may show OS security prompts; see the guide. SHA-256 checksums are attached to the release.

## Quick start

```bash
uv sync                       # Python 3.11, pinned
uv run pytest -q --ignore=tests/test_gui_window.py
                              # 186 pass on macOS; 16 container-only tests skip
```

The 16 skipped tests need `tc`/`tshark`/ns-3 and run only in the evaluation
container (below). The 9 Tk window tests in `tests/test_gui_window.py` run only
from an interactive terminal or with `HPQV_GUI_TESTS=1` (they skip without a
display, because Tk can abort the whole process on macOS when probed headless).
Counts above are from the 2026-10-16 macOS run on `chore/docs-final`.

macOS needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for opuslib (see
`docs/wire-format.md` → Runtime notes).

## Run a real call

For Windows and Apple Silicon app packages, see [Desktop demo setup](docs/DESKTOP-DEMO.md).
The portable builds include a GUI and CLI and need no separate Python installation.

Generate one identity file and copy it to both machines — each peer pins the
other's public key, so both must load the same file:

```bash
python -m hpqv.demo keygen --out identity.json
```

**GUI** (recommended for a live audience — no extra dependency, tkinter is stdlib):

```bash
python -m hpqv.gui
```

Pick the identity file, choose *Listen* on one machine and *Call* on the other,
press Start. Every control changes the running call, and every effect shows up in
the live stats and the 60 s graph on the right — no hanging up. The window is a
notebook of controls (left) beside always-visible stats and graph (right), sized
for a 1366×768 laptop or a projector:

- **Call tab** — connection and identity (*Create identity* writes a new file);
  input source (*Microphone* or a *WAV file* picked from disk — 48 kHz mono 16-bit,
  loops until you hang up); *Record received audio* to a WAV; and the
  **handshake panel**: ML-KEM / ML-DSA names, HELLO / ACCEPT / total bytes, TCP
  transport, measured handshake time, "UDP fragments: 0" and the largest datagram
  sent against the 1200 B cap.
- **Network tab** — presets (*Clean*, *Bad Wi-Fi*, *Burst loss*, *Congested*) that
  move the network sliders but leave repair mode, FEC and bitrate as set; repair
  mode (*Silence* / *Concealment only* / *FEC + concealment*) and an *Encoder FEC*
  checkbox; outgoing loss 0–50 %, loss model (*Random* / *Burst* with mean burst
  length 1–10), delay 0–500 ms, jitter 0–200 ms, incoming loss 0–50 %; jitter-buffer
  depth 1–15 frames (shown in ms) and Opus bitrate (Auto, 8–64 kbit/s).
- **Security tab** — *Rotate keys* re-keys both sides mid-call and the audio keeps
  playing; the key generation and an 8-hex-digit fingerprint (SHA-256 of the
  current key) update live so two people can compare it aloud
  ([ADR-0004](docs/adr/0004-live-key-rotation.md)). The button is disabled while the
  previous key retires (about 4 s).
- **Live stats and graph** — sent, received, dropped (out), dropped (in), late,
  recovered (FEC), concealed, loss %, residual loss %, and a live MOS. The graph
  plots loss, residual loss, recovered/s and MOS over the last 60 s.
- **Export session…** writes the call's `timeline.csv`, `summary.md` and three charts
  (loss vs recovered, MOS, datagram size vs the 1200 B cap) to a folder you pick
  (also after hanging up). *Hang up* ends the call on both sides; a peer hang-up
  or a dropped control connection updates the status line.

**Read the tiles correctly.** The outgoing-loss, delay, jitter, FEC and bitrate
controls act on what the *peer* hears; the tiles, graph, MOS and export show the
audio *you* receive, measured by your own receiver. Incoming loss, repair mode and
jitter depth shape what you hear. The MOS is an ITU-T G.107 E-model estimate from
that measured loss and delay, not a listening test.

Step-by-step script for a live demo: **[docs/DEMO-RUNBOOK.md](docs/DEMO-RUNBOOK.md)**.

**CLI**, same machinery:

```bash
python -m hpqv.demo listen --port 9000 --identity-file identity.json
python -m hpqv.demo call 192.168.1.42:9000 --identity-file identity.json
```

`--drop-pct 30` injects loss, fixed for the duration of the call (the GUI changes it live).

Use two machines — on one box the mic picks up the speaker and howls. Full
instructions and troubleshooting: **[docs/DEMO.md](docs/DEMO.md)**.

## Reproduce every figure

`tc netem` and ns-3 do not run on macOS, so evaluation runs in a container:

```bash
docker build -t hpqv-eval ./eval
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval bash -c \
  'cd /work && uv sync -q && uv run python scripts/run_eval.py'              # Track 2
# also: scripts/run_ns3.py, scripts/run_handshake_survival.py, scripts/run_fec_recovery.py,
#       scripts/run_full_handshake.py
python scripts/render_tables.py    # refresh the tables in the docs afterwards
```

Outputs land in `results/` (committed, so the report can cite exact numbers).

## Layout

```
src/hpqv/          handshake, control, packet, jitter, audio, playout (FEC/PLC), udp, call, session,
                   keyring + callctl (live rotation, call end), controls + impair (live knobs),
                   telemetry (receiver-side stats, live MOS, export), demo (CLI), gui* (live-demo window)
src/hpqv/eval/     MOS (ITU-T G.107), capture, stats, baselines, netem, runner, graphs
ns3/               hpqv_sim.cc — the NS-3 secondary model (see ns3/README.md)
eval/Dockerfile    the Linux evaluation environment
scripts/           sweep drivers + table renderer
results/           committed CSVs and figures
docs/              evaluation report, experiments, wire format, ADRs
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/EVALUATION-REPORT.md](docs/EVALUATION-REPORT.md) | Every test and result, method, limitations, metric glossary |
| [docs/EXPERIMENT-handshake-survival.md](docs/EXPERIMENT-handshake-survival.md) | Does the handshake survive loss? |
| [docs/EXPERIMENT-fec-recovery.md](docs/EXPERIMENT-fec-recovery.md) | Does Opus in-band FEC actually recover frames? |
| [docs/EXPERIMENT-full-handshake.md](docs/EXPERIMENT-full-handshake.md) | Real authenticated handshake over TCP and one-datagram UDP under loss, with and without HELLO retry |
| [docs/wire-format.md](docs/wire-format.md) | Byte layout of the handshake and voice datagrams |
| [docs/DEMO-RUNBOOK.md](docs/DEMO-RUNBOOK.md) | Minute-by-minute script for the 15–20 min two-laptop GUI demo, with caveats and likely panel questions |
| [docs/DEMO.md](docs/DEMO.md) | Running the live call (GUI and CLI) |
| [docs/DESKTOP-DEMO.md](docs/DESKTOP-DEMO.md) | Windows / Apple Silicon app packages and two-device setup |
| [docs/REPORT.md](docs/REPORT.md) | IEEE-structured project report |
| [docs/adr/](docs/adr/) | Why NS-3 is secondary, why mutual auth was added, how live key rotation works (0004) |

## Status and limitations

The implementation, evaluation and live-demo GUI are complete for this review.
Known limitations are documented rather than hidden — see
`docs/EVALUATION-REPORT.md` §12 and `ns3/README.md`.

**Closed since Review III** (details in the evaluation report): live FEC recovery
in the receiver (and the Apple Silicon Opus CTL bug that had silently disabled it
on macOS arm64), live key rotation and call-end signalling, the full authenticated
handshake measured under one common deadline, and receiver-side live telemetry
with a live MOS estimate.

**Still open:**

- No real two-device validation has been recorded in the repo; everything measured
  is on loopback or an emulated local link (`tc netem`, i.i.d. random loss).
- No real networks or middleboxes. Real paths may drop fragments outright, which
  would penalise the naive baseline further, but we have not measured it.
- Fixed 1200 B datagram cap; no dynamic PMTU discovery.
- NS-3's QUIC is a proxy and its TCP model understates head-of-line latency. No
  real QUIC/TLS stack is benchmarked, so we make no claim against those protocols.
- Quality is estimated with the E-model (and segmental SNR offline). There are no
  listening tests.
- The in-call control channel (rotation, hang-up) is plain TCP after the handshake;
  an on-path attacker can desynchronise keys (denial of service, not disclosure).
  Authenticated control records are future work (ADR-0004).
- FEC is single-frame lookahead: it recovers isolated losses and is defeated by
  bursts. This is shown, not hidden, in the Burst loss preset.

This is a final-year academic project, not production software: it has not been
security-audited, and identities are pinned out-of-band rather than via a CA.

Further work is tracked in
[GitHub issues](https://github.com/rabbive/hybrid-pqc-voice-transport/issues).
