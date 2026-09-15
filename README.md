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

## The design

| Channel | Transport | Carries |
|---|---|---|
| **Control** | TCP | the PQC handshake (mutually authenticated), call setup/teardown, key rotation |
| **Data** | UDP | Opus voice, every datagram ≤ 1200 B with the Don't-Fragment bit set |

Nothing ever fragments. Voice keeps UDP's latency; the oversized handshake gets
TCP's reliability.

## Headline results

Measured on the real implementation under `tc netem` impairment — not simulated.

**Zero fragmentation, at every loss level.**

| packet loss | our hybrid | naive PQC-over-UDP |
|---|---|---|
| 0–30 % | **0 fragments** | 4–5 fragments |

**And that matters — it decides whether the call connects at all:**

| packet loss | naive handshake completes | hybrid handshake completes |
|---|---|---|
| 10 % | 39 % | **100 %** |
| 20 % | 19 % | **99 %** |
| 30 % | 2 % | **83 %** |

At 20 % loss roughly one naive call in five connects; ours connects 99 times in 100.
Measurements track the `(1-p)^8` fragment-loss prediction, confirming the mechanism.

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
uv run pytest -q              # 38 pass on macOS; 15 container-only tests skip
                              # all 53 pass in the evaluation container (below)
```

macOS needs `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for opuslib (see
`docs/wire-format.md` → Runtime notes).

## Run a real call

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
press Start. Then **drag the packet-loss slider during the call**: loss is
injected live, so the audience hears the transport degrade and recover while the
dropped counter climbs — no hanging up.

**CLI**, same machinery:

```bash
python -m hpqv.demo listen --port 9000 --identity-file identity.json
python -m hpqv.demo call 192.168.1.42:9000 --identity-file identity.json
```

`--drop-pct 30` injects loss, fixed for the duration of the call.

Use two machines — on one box the mic picks up the speaker and howls. Full
instructions and troubleshooting: **[docs/DEMO.md](docs/DEMO.md)**.

## Reproduce every figure

`tc netem` and ns-3 do not run on macOS, so evaluation runs in a container:

```bash
docker build -t hpqv-eval ./eval
docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval bash -c \
  'cd /work && uv sync -q && uv run python scripts/run_eval.py'              # Track 2
# also: scripts/run_ns3.py, scripts/run_handshake_survival.py, scripts/run_fec_recovery.py
python scripts/render_tables.py    # refresh the tables in the docs afterwards
```

Outputs land in `results/` (committed, so the report can cite exact numbers).

## Layout

```
src/hpqv/          handshake, control, packet, jitter, audio, udp, call, session, demo, gui
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
| [docs/wire-format.md](docs/wire-format.md) | Byte layout of the handshake and voice datagrams |
| [docs/DEMO.md](docs/DEMO.md) | Running the live call (GUI and CLI) |
| [docs/REPORT.md](docs/REPORT.md) | IEEE-structured project report |
| [docs/adr/](docs/adr/) | Why NS-3 is secondary, why mutual auth was added, etc. |

## Status and limitations

The implementation and evaluation are complete. Known limitations are documented
rather than hidden — see `docs/EVALUATION-REPORT.md` §4 and `ns3/README.md`. Two
worth knowing up front: measurements are on an emulated local link (real networks
add middleboxes that drop fragments outright, which would penalise the naive
baseline *further*), and the NS-3 model understates TCP's head-of-line latency.

This is a final-year academic project, not production software: it has not been
security-audited, and identities are pinned out-of-band rather than via a CA.

Remaining work is tracked in
[GitHub issues](https://github.com/rabbive/hybrid-pqc-voice-transport/issues) —
including a two-device dry-run, the report sections still to be written, and the
deferred future-work items.
