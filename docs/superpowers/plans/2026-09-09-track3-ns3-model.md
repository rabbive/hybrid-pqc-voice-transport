# Track 3 — NS-3 Secondary Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Corroborate the real-prototype results and add the panel-expected NS-3 study: model hybrid vs TCP/TLS vs QUIC as size+delay in ns-3, sweep loss/latency, and produce comparison graphs sharing the same E-model MOS pipeline as Track 2.

**Architecture:** A C++ ns-3 program models a two-node link under configurable loss/delay and runs one of three transport scenarios (hybrid / tcp / quic), where the PQC handshake is modelled as a one-time byte transfer (real liboqs sizes) plus a fixed CPU delay (real liboqs benchmark), and voice is a 20 ms-frame CBR stream. FlowMonitor yields delay/jitter/loss + TTFB. A Python driver sweeps conditions, runs the binary, parses output, computes MOS via the existing `hpqv.eval.mos`, and plots via `hpqv.eval.graphs`.

**Tech Stack:** ns-3 3.41 (apt `ns3` + `libns3-dev`), C++17, Python 3.11, existing `hpqv.eval.mos`/`graphs`, liboqs (real sizes + timing).

**Spec:** docs/superpowers/specs/2026-09-08-hybrid-pqc-voice-design.md (Track B)
**ADR:** docs/adr/0003-ns3-as-secondary-evaluation-model.md

## Global Constraints

- ALL eval work runs in the container. Extend `eval/Dockerfile` to add `ns3` + `libns3-dev` (apt). All Bash sandbox-disabled.
- Reuse `hpqv.eval.mos.mos_from_network` and `hpqv.eval.graphs` — do NOT reimplement MOS or plotting.
- PQC is MODELLED, never executed, in ns-3: handshake_bytes from real liboqs sizes (ML-KEM-768 pub 1184, ct 1088; ML-DSA-65 pub 1952, sig 3309), handshake_ms from a real liboqs micro-benchmark.
- **QUIC:** ns-3 has no official QUIC module. Model QUIC as a UDP-based flow with a 1-RTT handshake (vs hybrid's TCP-handshake + UDP-voice, and tcp/tls's full-TCP). Document this simplification in comments and the report — it is a deliberate model, not real QUIC.
- Voice model: 20 ms frames, ~64 kbps CBR (Opus-ish), for a fixed call duration (e.g. 10 s sim time).
- TDD where it fits: Python parts get pytest; the C++ sim gets a compile+run smoke check asserting sane output. Commit per task.

## File Structure
- `eval/Dockerfile` — add ns3 + libns3-dev (Task 1)
- `ns3/hpqv_sim.cc` — the C++ simulation (Task 3)
- `ns3/build.sh` — compile helper (finds ns3 include/lib flags) (Task 1/3)
- `src/hpqv/eval/pqc_cost.py` — real liboqs sizes + timing → model params (Task 2)
- `scripts/run_ns3.py` — sweep driver → CSV + graphs (Task 4)
- `tests/eval/test_pqc_cost.py`, `tests/eval/test_ns3_smoke.py`

---

### Task 1: ns-3 in the image + a compiling hello-sim (CONTAINER, Gate)

**Files:** Modify `eval/Dockerfile`; create `ns3/hello_sim.cc`, `ns3/build.sh`.

Goal: prove we can compile and run an ns-3 C++ program in the container.

- [ ] **Step 1** — Add to `eval/Dockerfile` apt install list: `ns3 libns3-dev`. Rebuild: `docker build -t hpqv-eval ./eval`.
- [ ] **Step 2** — Write `ns3/hello_sim.cc`: a minimal ns-3 program (include `ns3/core-module.h`, schedule one event that prints "ns3 ok", `Simulator::Run/Destroy`).
- [ ] **Step 3** — Write `ns3/build.sh` that compiles a given .cc against ns-3. Determine flags inside the container — try `pkg-config --cflags --libs "$(pkg-config --list-all | grep -o 'ns3[^ ]*' | tr '\n' ' ')"`; if Debian ships no combined .pc, fall back to `-I/usr/include -lns3.41-core -lns3.41-network -lns3.41-internet -lns3.41-point-to-point -lns3.41-applications -lns3.41-flow-monitor` (adjust the version/module suffixes to what `ls /usr/lib/*/libns3-*.so` shows). Document the working invocation in the script.
- [ ] **Step 4** — Compile + run hello_sim in the container; assert it prints "ns3 ok":
  `docker run --rm -v "$PWD":/work hpqv-eval bash -c 'cd /work/ns3 && bash build.sh hello_sim.cc /tmp/hello && /tmp/hello'`
- [ ] **Step 5** — Commit `eval/Dockerfile`, `ns3/hello_sim.cc`, `ns3/build.sh`.

---

### Task 2: PQC cost model (HOST, real liboqs)

**Files:** Create `src/hpqv/eval/pqc_cost.py`, `tests/eval/test_pqc_cost.py`.

**Interfaces:**
- `handshake_bytes(scheme: str) -> int` — bytes on the wire for the modelled handshake of scheme in {"hybrid","tcp","quic"}. All three carry the same PQC material (kem_pub+ct+sigs); the scheme differs only in transport, so bytes are ~equal (use the real total: hello 1184+1952+3309 + accept 1088+3309 ≈ 10842). Return that constant total for all schemes (transport differences are modelled by delay, not bytes).
- `handshake_ms() -> float` — measured wall-time to run one real PQC handshake (Kyber keygen+encap+decap + Dilithium sign+verify) via liboqs, median of a few runs. This is the CPU cost injected as delay in the sim.

- [ ] **Step 1: failing test**
```python
# tests/eval/test_pqc_cost.py
from hpqv.eval import pqc_cost

def test_handshake_bytes_realistic():
    b = pqc_cost.handshake_bytes("hybrid")
    assert 9000 < b < 13000            # ~10.8 KB of PQC material

def test_handshake_ms_positive_and_small():
    ms = pqc_cost.handshake_ms()
    assert 0 < ms < 200                # PQC handshake is sub-ms..few-ms
```

- [ ] **Step 2** — run (host, with DYLD var), expect FAIL.
- [ ] **Step 3** — implement using real `oqs` calls + `time.perf_counter`, median of ~5 runs for `handshake_ms`; `handshake_bytes` sums the real record sizes.
- [ ] **Step 4** — `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/eval/test_pqc_cost.py -v`, expect PASS.
- [ ] **Step 5** — commit.

---

### Task 3: the ns-3 simulation (CONTAINER)

**Files:** Create `ns3/hpqv_sim.cc`.

CLI: `hpqv_sim --scheme=<hybrid|tcp|quic> --loss=<0..1> --delayMs=<ms> --hsBytes=<int> --hsMs=<double> --dur=<s>`. Prints one CSV line: `scheme,loss,delayMs,ttfb_ms,voice_delay_ms,voice_jitter_ms,voice_loss_pct`.

Model:
- Two nodes on a PointToPoint link: `DataRate` (e.g. 10Mbps), channel `Delay = delayMs/2` each way; attach a `RateErrorModel` with rate=`loss` on the receiver device (drops packets to emulate loss).
- Handshake: model as sending `hsBytes` over the link starting at t=0 using a BulkSend (TCP) for hybrid/tcp, or a single burst for quic; the handshake "completes" when hsBytes are delivered; then add `hsMs` as fixed processing delay. TTFB = handshake completion time (ms) + hsMs.
- Voice: after handshake, an OnOff/UDP app (hybrid, quic) or a TCP app (tcp) sends 20 ms CBR frames for `dur` seconds to a PacketSink. hybrid/quic voice = UDP (loss shows as drops); tcp voice = TCP (loss shows as delay/HOL, ~0 loss).
- FlowMonitor: collect voice-flow mean delay, jitter, and loss ratio → the printed metrics. TTFB from the handshake flow's last-rx time.

- [ ] **Step 1: smoke test (CONTAINER)** — `tests/eval/test_ns3_smoke.py` compiles hpqv_sim via build.sh, runs it once for `--scheme=hybrid --loss=0 --delayMs=50 --hsBytes=10842 --hsMs=1 --dur=2`, parses the CSV line, asserts: ttfb_ms > 0, voice_loss_pct == 0 at loss 0, and a `--scheme=hybrid --loss=0.2` run yields voice_loss_pct > 0. Mark the module container-only (skipif on ns3 headers / a compiled binary).
- [ ] **Step 2** — run in container, expect FAIL (no hpqv_sim.cc yet).
- [ ] **Step 3** — implement `ns3/hpqv_sim.cc` per the model above; keep it one file, use standard ns-3 helpers (PointToPointHelper, InternetStackHelper, Ipv4AddressHelper, BulkSendHelper, OnOffHelper, PacketSinkHelper, FlowMonitorHelper). Parse CLI with `CommandLine`.
- [ ] **Step 4** — run smoke in container, expect PASS.
- [ ] **Step 5** — commit.

---

### Task 4: sweep driver → CSV + comparison graphs (CONTAINER)

**Files:** Create `scripts/run_ns3.py`, `tests/eval/test_run_ns3_smoke.py`.

`main(quick=False, losses=None, delays=None)`:
- Compile hpqv_sim once (via build.sh) if the binary is missing.
- Get hsBytes/hsMs from `pqc_cost`.
- Loss sweep {0,5,10,20,30}% at delay 50 ms × schemes {hybrid,tcp,quic}; latency sweep {20,50,100,200} ms at loss 0 for TTFB.
- Run the binary per condition, parse CSV lines, compute `mos = mos.mos_from_network(voice_delay_ms, voice_jitter_ms, voice_loss_pct)`.
- Write `results/ns3_eval.csv` + `results/ns3_latency.csv`; plot via `graphs.plot_loss_vs_mos`, `plot_latency_vs_ttfb`, and a `plot_loss_vs_mos`-style hybrid-vs-tcp-vs-quic figure (`results/ns3_loss_vs_mos.png`, `results/ns3_latency_vs_ttfb.png`).

- [ ] **Step 1** — container smoke test: `main(quick=True, losses=[0,20])` writes ns3_eval.csv (rows for all 3 schemes) and the PNGs non-empty; hybrid/quic show voice loss at 20% while tcp stays ~0.
- [ ] **Step 2–4** — TDD in container.
- [ ] **Step 5** — commit.

---

## Self-review
- Track B coverage: NS-3 model of hybrid + TCP/TLS + QUIC (the baseline Track 2 couldn't host), loss + latency sweeps, FlowMonitor metrics, shared E-model MOS, comparison graphs. ✓
- PQC modelled as size+delay from REAL liboqs numbers (pqc_cost), not invented. ✓
- Reuses mos.py + graphs.py (no reimplementation). ✓
- QUIC simplification explicitly documented (no official ns-3 QUIC). ✓

## Notes
- ns-3 module/version suffixes (`ns3.41-*`) must match what the apt package installs — Task 1 pins the exact working compile flags in build.sh.
- Keep sim durations short in tests (2 s) for speed; the real sweep can use 10 s.
