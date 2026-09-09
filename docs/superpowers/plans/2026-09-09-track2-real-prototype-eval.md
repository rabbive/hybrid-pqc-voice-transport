# Track 2 — Real-Prototype Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Produce the primary evidence for the project — the fragmentation-free proof and the quality graphs — by measuring the real prototype vs baselines under controlled `tc netem` impairment.

**Architecture:** Pure-logic analysis modules (MOS via E-model, fragment counting, stream stats) that are host-testable, plus two baseline transports and a `tc netem` experiment runner that execute inside the `hpqv-eval` Docker container. One sweep script produces CSV results and PNG graphs.

**Tech Stack:** Python 3.11, existing `hpqv` modules, tshark (capture + fragment analysis), scapy (test-only pcap crafting), matplotlib, numpy.

**Spec:** docs/superpowers/specs/2026-09-08-hybrid-pqc-voice-design.md (§Testing & Evaluation, Track A)
**Roadmap:** docs/superpowers/plans/2026-09-09-evaluation-and-hardening-roadmap.md (Track 2)

## Global Constraints

- Reuse existing `hpqv` modules (handshake, control, packet, call, audio, udp) — do NOT reimplement crypto or audio.
- New deps: add `scapy` and `matplotlib` via `uv add` (matplotlib is runtime, scapy is fine as a normal dep — it's used in tests and optionally analysis).
- **Two test environments** — each task says which:
  - **HOST**: pure logic, runs on macOS: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/<f> -v`
  - **CONTAINER**: needs `tc netem` / real fragmentation — run via:
    `docker run --rm --cap-add=NET_ADMIN -v "$PWD":/work hpqv-eval bash -c 'cd /work && uv sync -q && uv run pytest tests/<f> -v'`
    (after a container run, re-sync the host venv: `rm -rf .venv && uv sync -q`.)
- New code lives under `src/hpqv/eval/`. Fragment-free target: hybrid handshake produces 0 IP fragments; naive-over-UDP produces >0.
- MOS uses the ITU-T G.107 E-model. TDD throughout; commit per task.

## File Structure
- `src/hpqv/eval/__init__.py`
- `src/hpqv/eval/mos.py` — E-model R-factor → MOS (Task 1)
- `src/hpqv/eval/capture.py` — tshark capture wrapper + IP-fragment counter (Task 2)
- `src/hpqv/eval/stats.py` — jitter, loss/drop, TTFB from arrival records (Task 3)
- `src/hpqv/eval/baselines.py` — naive-PQC-over-UDP + TCP-voice transports (Task 4)
- `src/hpqv/eval/netem.py` — apply/clear netem context manager (Task 5)
- `src/hpqv/eval/runner.py` — run one scenario under one condition → metrics (Task 6)
- `src/hpqv/eval/graphs.py` — results → PNG figures (Task 7)
- `scripts/run_eval.py` — full sweep driver (Task 8)
- `tests/eval/test_*.py`

---

### Task 1: E-model MOS (HOST)

**Files:** Create `src/hpqv/eval/__init__.py`, `src/hpqv/eval/mos.py`, `tests/eval/test_mos.py`

**Interfaces:**
- Produces: `r_factor(delay_ms: float, jitter_ms: float, loss_pct: float) -> float`; `mos(r: float) -> float`; `mos_from_network(delay_ms, jitter_ms, loss_pct) -> float`.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_mos.py
from hpqv.eval import mos

def test_perfect_network_high_mos():
    m = mos.mos_from_network(delay_ms=0, jitter_ms=0, loss_pct=0)
    assert 4.0 <= m <= 4.5          # clean network ≈ 4.4 (G.107 default R≈93)

def test_loss_degrades_mos():
    good = mos.mos_from_network(0, 0, 0)
    bad = mos.mos_from_network(0, 0, 20)
    assert bad < good - 1.0         # 20% loss must drop MOS clearly

def test_mos_clamped():
    assert 1.0 <= mos.mos(-50) <= 4.5
    assert 1.0 <= mos.mos(200) <= 4.5
```

- [ ] **Step 2: Run — expect FAIL** — `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/eval/test_mos.py -v`

- [ ] **Step 3: Implement**
```python
# src/hpqv/eval/mos.py
# ITU-T G.107 E-model, simplified for a fixed narrowband codec baseline.
# Effective latency + an Ie-eff-style loss penalty (Opus-ish: Ie=1, Bpl=20).
def r_factor(delay_ms: float, jitter_ms: float, loss_pct: float) -> float:
    R0, Is = 93.2, 0.0
    eff_delay = delay_ms + 2.0 * jitter_ms          # jitter buffer adds ~2x jitter
    # Idd: delay impairment (approx, negligible <150ms, rises after)
    if eff_delay < 160:
        Id = 0.024 * eff_delay
    else:
        Id = 0.024 * eff_delay + 0.11 * (eff_delay - 120)
    Ie, Bpl = 1.0, 20.0                              # Opus-like packet-loss robustness
    Ie_eff = Ie + (95 - Ie) * (loss_pct / (loss_pct + Bpl)) if loss_pct > 0 else Ie
    return R0 - Is - Id - Ie_eff

def mos(r: float) -> float:
    if r < 0: return 1.0
    if r > 100: return 4.5
    m = 1 + 0.035 * r + 7e-6 * r * (r - 60) * (100 - r)
    return max(1.0, min(4.5, m))

def mos_from_network(delay_ms: float, jitter_ms: float, loss_pct: float) -> float:
    return mos(r_factor(delay_ms, jitter_ms, loss_pct))
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): ITU-T E-model MOS from delay/jitter/loss"`

---

### Task 2: Capture + IP-fragment counter (HOST)

**Files:** Create `src/hpqv/eval/capture.py`, `tests/eval/test_capture.py`

**Interfaces:**
- Produces: `count_ip_fragments(pcap_path: str) -> int` (uses tshark); `capture(iface, out_path)` context manager wrapping dumpcap/tshark (used later, not unit-tested here).

- [ ] **Step 1: Write the failing test** — craft a pcap with one fragmented datagram using scapy (no network), assert the counter finds the fragments.
```python
# tests/eval/test_capture.py
from scapy.all import IP, UDP, fragment, wrpcap
from hpqv.eval.capture import count_ip_fragments

def test_counts_fragments(tmp_path):
    pkt = IP(dst="10.0.0.1") / UDP(dport=5000) / (b"x" * 4000)   # >MTU
    frags = fragment(pkt, fragsize=1400)                          # 3 fragments
    p = tmp_path / "frag.pcap"; wrpcap(str(p), frags)
    assert count_ip_fragments(str(p)) == len(frags)

def test_zero_fragments(tmp_path):
    from scapy.all import wrpcap
    pkt = IP(dst="10.0.0.1")/UDP(dport=5000)/(b"x"*200)
    p = tmp_path / "ok.pcap"; wrpcap(str(p), [pkt])
    assert count_ip_fragments(str(p)) == 0
```

- [ ] **Step 2: Run — expect FAIL** (add deps first: `uv add scapy matplotlib`). Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/eval/test_capture.py -v`

- [ ] **Step 3: Implement**
```python
# src/hpqv/eval/capture.py
import subprocess

def count_ip_fragments(pcap_path: str) -> int:
    # A fragment = MF flag set OR non-zero fragment offset.
    out = subprocess.run(
        ["tshark", "-r", pcap_path, "-Y", "ip.flags.mf==1 || ip.frag_offset>0",
         "-T", "fields", "-e", "frame.number"],
        capture_output=True, text=True, check=True).stdout
    return sum(1 for line in out.splitlines() if line.strip())
```
Note: macOS may lack tshark; if `tshark` is not on PATH this task's test is CONTAINER-only. Prefer running Task 2's test in the container (tshark is installed there). The counter itself is pure subprocess.

- [ ] **Step 4: Run — expect PASS** (container if host has no tshark):
`docker run --rm -v "$PWD":/work hpqv-eval bash -c 'cd /work && uv sync -q && uv run pytest tests/eval/test_capture.py -v'`

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): IP-fragment counter over tshark"`

---

### Task 3: Stream stats — jitter, loss, TTFB (HOST)

**Files:** Create `src/hpqv/eval/stats.py`, `tests/eval/test_stats.py`

**Interfaces:**
- Produces: `drop_rate(sent: int, received_seqs: list[int]) -> float`; `mean_jitter_ms(arrivals: list[float], frame_ms: float = 20.0) -> float` (RFC 3550-style); `ttfb_ms(connect_t: float, first_frame_t: float) -> float`.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_stats.py
from hpqv.eval import stats

def test_drop_rate():
    assert stats.drop_rate(10, [0,1,2,3,4,5,6,7]) == 0.2   # 2 of 10 lost

def test_jitter_zero_for_perfect_spacing():
    arrivals = [i*0.02 for i in range(10)]                 # perfect 20ms
    assert stats.mean_jitter_ms(arrivals) < 0.001

def test_jitter_positive_for_irregular():
    arrivals = [0.0, 0.02, 0.05, 0.06, 0.10]
    assert stats.mean_jitter_ms(arrivals) > 0

def test_ttfb():
    assert stats.ttfb_ms(1.0, 1.25) == 250.0
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**
```python
# src/hpqv/eval/stats.py
def drop_rate(sent: int, received_seqs) -> float:
    if sent <= 0: return 0.0
    return (sent - len(set(received_seqs))) / sent

def mean_jitter_ms(arrivals, frame_ms: float = 20.0) -> float:
    # RFC 3550 interarrival jitter, in ms.
    if len(arrivals) < 2: return 0.0
    j = 0.0
    prev = arrivals[0]
    for t in arrivals[1:]:
        d = abs((t - prev) * 1000.0 - frame_ms)
        j += (d - j) / 16.0
        prev = t
    return j

def ttfb_ms(connect_t: float, first_frame_t: float) -> float:
    return (first_frame_t - connect_t) * 1000.0
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): stream stats (drop rate, jitter, TTFB)"`

---

### Task 4: Baseline transports — naive-PQC-over-UDP + TCP voice (CONTAINER)

**Files:** Create `src/hpqv/eval/baselines.py`, `tests/eval/test_baselines.py`

**Interfaces:**
- Produces:
  - `naive_send_handshake_udp(udp_sock, dest, blob: bytes) -> int` — sends an oversized handshake blob as ONE UDP datagram with NO app-layer framing and NO DF (so the kernel fragments it). Returns bytes sent.
  - `tcp_voice_send(sock, session, frames) -> int` / `tcp_voice_recv(sock, session, expected) -> list[bytes]` — voice over a TCP stream (reliable, head-of-line-blocked) reusing packet.seal/open_ with length-prefixed records.

- [ ] **Step 1: Write the failing test** (CONTAINER — captures real fragmentation on loopback)
```python
# tests/eval/test_baselines.py
import socket, threading, subprocess, time, os
from hpqv.eval import baselines
from hpqv.eval.capture import count_ip_fragments

def test_naive_udp_handshake_fragments(tmp_path):
    # A realistic PQC handshake blob exceeds the 1500B MTU (~6.4KB): must fragment.
    blob = os.urandom(6445)
    pcap = tmp_path / "naive.pcap"
    cap = subprocess.Popen(["tshark","-i","lo","-w",str(pcap),"-f","udp port 5555"],
                           stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.bind(("127.0.0.1",5555))
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    baselines.naive_send_handshake_udp(tx, ("127.0.0.1",5555), blob)
    time.sleep(1.0); cap.terminate(); cap.wait()
    assert count_ip_fragments(str(pcap)) > 0        # THE villain fragments
```
(Add a TCP-voice round-trip test mirroring test_call.py's structure over a TCP socket.)

- [ ] **Step 2: Run — expect FAIL** (container)

- [ ] **Step 3: Implement**
```python
# src/hpqv/eval/baselines.py
import struct
from hpqv import packet

def naive_send_handshake_udp(udp_sock, dest, blob: bytes) -> int:
    # Deliberately naive: no framing, no DF — one oversized datagram the kernel
    # must fragment at the IP layer. This is the baseline our hybrid beats.
    return udp_sock.sendto(blob, dest)

def tcp_voice_send(sock, session, frames) -> int:
    from hpqv.audio import OpusCodec
    key, send_prefix, _ = session
    codec = OpusCodec(); n = 0
    for seq, pcm in enumerate(frames):
        dg = packet.seal(key, send_prefix, seq, codec.encode(pcm))
        sock.sendall(struct.pack(">I", len(dg)) + dg); n += 1
    return n

def tcp_voice_recv(sock, session, expected) -> list:
    from hpqv.audio import OpusCodec
    key, _, recv_prefix = session
    codec = OpusCodec(); out = []
    buf = b""
    def recvn(n):
        nonlocal buf
        while len(buf) < n:
            c = sock.recv(4096)
            if not c: return None
            buf += c
        r, buf = buf[:n], buf[n:]; return r
    for _ in range(expected):
        hdr = recvn(4)
        if hdr is None: break
        (ln,) = struct.unpack(">I", hdr)
        dg = recvn(ln)
        if dg is None: break
        _seq, _flags, opus = packet.open_(key, recv_prefix, dg)
        out.append(codec.decode(opus))
    return out
```

- [ ] **Step 4: Run — expect PASS** (container). Then re-sync host venv.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): naive-UDP (fragmenting) and TCP-voice baselines"`

---

### Task 5: netem context manager (CONTAINER)

**Files:** Create `src/hpqv/eval/netem.py`, `tests/eval/test_netem.py`

**Interfaces:**
- Produces: `netem(loss_pct=0, delay_ms=0, jitter_ms=0, dev="lo")` — a context manager that applies the qdisc on enter and removes it on exit; raises if `tc` fails.

- [ ] **Step 1: Write the failing test** (CONTAINER, needs NET_ADMIN)
```python
# tests/eval/test_netem.py
import subprocess
from hpqv.eval.netem import netem

def test_netem_applies_and_clears():
    with netem(loss_pct=10, delay_ms=50, dev="lo"):
        out = subprocess.run(["tc","qdisc","show","dev","lo"],
                             capture_output=True, text=True).stdout
        assert "netem" in out and "loss 10%" in out
    out = subprocess.run(["tc","qdisc","show","dev","lo"],
                         capture_output=True, text=True).stdout
    assert "netem" not in out            # cleared on exit
```

- [ ] **Step 2: Run — expect FAIL** (container)

- [ ] **Step 3: Implement**
```python
# src/hpqv/eval/netem.py
import subprocess
from contextlib import contextmanager

@contextmanager
def netem(loss_pct=0.0, delay_ms=0.0, jitter_ms=0.0, dev="lo"):
    parts = ["tc","qdisc","add","dev",dev,"root","netem"]
    if delay_ms: parts += ["delay", f"{delay_ms}ms"] + ([f"{jitter_ms}ms"] if jitter_ms else [])
    if loss_pct: parts += ["loss", f"{loss_pct}%"]
    subprocess.run(parts, check=True)
    try:
        yield
    finally:
        subprocess.run(["tc","qdisc","del","dev",dev,"root"],
                       stderr=subprocess.DEVNULL)
```

- [ ] **Step 4: Run — expect PASS** (container)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): tc netem context manager"`

---

### Task 6: Scenario runner (CONTAINER)

**Files:** Create `src/hpqv/eval/runner.py`, `tests/eval/test_runner.py`

**Interfaces:**
- Produces: `run_scenario(name: str, loss_pct: float, delay_ms: float, jitter_ms: float, n_frames: int = 250) -> dict` where name in {"hybrid","naive","tcp"}; returns `{"name","loss_pct","frag_count","drop_rate","jitter_ms","ttfb_ms","mos"}`. Uses the existing hybrid call path (call.py), the baselines, capture, stats, mos, and netem together.

- [ ] **Step 1: Write the failing test** (CONTAINER) — a smoke run at 0% loss for "hybrid": expect frag_count == 0 and mos > 4.0.
```python
# tests/eval/test_runner.py
from hpqv.eval.runner import run_scenario

def test_hybrid_zero_loss_no_fragments():
    r = run_scenario("hybrid", loss_pct=0, delay_ms=0, jitter_ms=0, n_frames=50)
    assert r["frag_count"] == 0          # fragmentation-free
    assert r["mos"] > 4.0
    assert r["name"] == "hybrid"

def test_naive_fragments():
    r = run_scenario("naive", loss_pct=0, delay_ms=0, jitter_ms=0, n_frames=50)
    assert r["frag_count"] > 0           # the villain fragments
```

- [ ] **Step 2: Run — expect FAIL** (container)

- [ ] **Step 3: Implement** — wire netem + a tshark capture around the chosen scenario's send/recv (hybrid via `call.send_stream`/`recv_stream`, naive via `baselines`, tcp via `tcp_voice_*`), collect seqs/arrival times, compute frag_count (capture), drop_rate/jitter/ttfb (stats), mos (mos_from_network with the configured delay/jitter and the *measured* drop rate). Keep it a single function; no framework. Handle capture start/stop with a subprocess and a short settle sleep.

- [ ] **Step 4: Run — expect PASS** (container)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): scenario runner (netem + capture + metrics)"`

---

### Task 7: Graphs (HOST)

**Files:** Create `src/hpqv/eval/graphs.py`, `tests/eval/test_graphs.py`

**Interfaces:**
- Produces: `plot_loss_vs_mos(rows, out_png)`, `plot_loss_vs_fragments(rows, out_png)`, `plot_latency_vs_ttfb(rows, out_png)` where rows is a list of scenario-result dicts. Each writes a PNG and returns its path.

- [ ] **Step 1: Write the failing test**
```python
# tests/eval/test_graphs.py
import os
from hpqv.eval import graphs

def test_writes_png(tmp_path):
    rows = [{"name":"hybrid","loss_pct":l,"mos":4.3-0.05*l,"frag_count":0,
             "ttfb_ms":40,"delay_ms":50} for l in (0,10,20,30)] + \
           [{"name":"naive","loss_pct":l,"mos":3.5-0.05*l,"frag_count":3,
             "ttfb_ms":80,"delay_ms":50} for l in (0,10,20,30)]
    p = graphs.plot_loss_vs_mos(rows, str(tmp_path/"mos.png"))
    assert os.path.getsize(p) > 0
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement** — matplotlib with the `Agg` backend (`matplotlib.use("Agg")`), one line per scenario name, labelled axes, legend, saved at ~150 dpi. Group rows by `name`.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): result graphs (loss-vs-MOS, fragments, TTFB)"`

---

### Task 8: Sweep driver + real graphs (CONTAINER)

**Files:** Create `scripts/run_eval.py`, `tests/eval/test_run_eval_smoke.py`

**Interfaces:**
- Produces: `main(quick: bool = False)` — sweeps loss {0,5,10,20,30} × scenarios {hybrid,naive,tcp} at delay 50ms jitter 10ms, median of N runs (N=1 when quick), writes `results/eval.csv` and the three PNGs under `results/`.

- [ ] **Step 1: Write the failing test** (CONTAINER) — quick smoke: run `main(quick=True)` restricted to loss {0,20}, assert eval.csv exists with hybrid frag_count 0 at loss 0 and the three PNGs are non-empty.

- [ ] **Step 2: Run — expect FAIL** (container)

- [ ] **Step 3: Implement** — loop conditions × scenarios calling `run_scenario`, collect rows, write CSV (csv module), call the three graph functions. `if __name__ == "__main__": main()`.

- [ ] **Step 4: Run — expect PASS** (container). Attach the generated `results/*.png` to the report.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(eval): sweep driver producing CSV + graphs"`

---

## Self-review checklist (run after writing all tasks)
- Every metric in spec §Track A has a task: fragment count (T2/T6), MOS (T1/T6), TTFB (T3/T6), jitter/drop (T3/T6), graphs (T7/T8). ✓
- Baselines hybrid/naive/tcp all present (T4/T6). ✓
- Interfaces consistent: session is the 3-tuple `(key, send_prefix, recv_prefix)` from the hardened handshake — baselines and runner unpack all three. ✓

## Notes
- FEC loss-recovery proof (deferred from prototype) can be a small extra test in Task 6 (run "hybrid" at 15% loss with FEC on vs a codec built with FEC off, compare drop-driven MOS). Add if time; not blocking the core graphs.
- The QUIC baseline is NOT here — it lives in Track 3 (NS-3), where a QUIC model is tractable.
