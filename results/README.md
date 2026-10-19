# Results

Generated evaluation data and figures. These are **committed deliberately** so the
report and defense can cite exact numbers, and so the figures are reviewable without
re-running the harness.

| File | Produced by | Contents |
|---|---|---|
| `eval.csv` | `scripts/run_eval.py` | Track 2 loss sweep (5-run medians): fragment count, drop rate, jitter, TTFB, MOS |
| `eval_latency.csv` | `scripts/run_eval.py` | Track 2 latency sweep for TTFB |
| `ns3_eval.csv` | `scripts/run_ns3.py` | Track 3 NS-3 loss sweep (hybrid / tcp / quic) |
| `ns3_latency.csv` | `scripts/run_ns3.py` | Track 3 NS-3 latency sweep |
| `handshake_survival.csv` | `scripts/run_handshake_survival.py` | Earlier synthetic-payload handshake delivery vs loss (unequal timeouts), 100 attempts/cell |
| `full_handshake.csv` | `scripts/run_full_handshake.py` | Full authenticated handshake over TCP / naive UDP / UDP with retry under one 3 s deadline, 100 attempts/cell |
| `fec_recovery.csv` | `scripts/run_fec_recovery.py` | Offline Opus in-band FEC recovery (segmental SNR) vs loss |
| `*.png` | the same scripts | The figures for the report |
| `v2/handshake_bench.{csv,json,png}` | `scripts/run_handshake_bench.py --n 50 --out results/v2` | **v2** TLS 1.3 handshake on loopback: hybrid (X25519MLKEM768 + ML-DSA-65) vs a benchmark-only classical baseline (x25519 + Ed25519), 50 interleaved runs. Machine, versions, load average and metric definitions are in the JSON `meta`. `git_dirty: true` there is untracked `.claude/` tooling only. |

Everything above `v2/` was measured on **v1**; `v2/` holds the first v2 measurements.

Regenerate everything with the commands in `docs/EVALUATION-REPORT.md` §13.
Numbers vary slightly between runs (network measurement is sampled, not deterministic) —
the committed CSVs are the specific run the report quotes.
